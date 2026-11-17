import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from herdbench._http import HttpClient
from herdbench.sources import hfmirror
from herdbench.sources.hfmirror import Mirror, MirrorError, RemoteFile, SparseFile, coalesce
from tests.utils.server import Routes, serve

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
REV = "de05dc101c819aa68accfefd6647b8bf6de02189"


def tree_entries():
    return json.loads((FIXTURES / "hf_tree_page.json").read_text())["entries"]


def fast_http():
    return HttpClient(retries=4, backoff=0.0, timeout=10.0)


def test_parse_tree_keeps_files_and_reads_lfs_hash():
    files = hfmirror.parse_tree(tree_entries())
    assert all(isinstance(f, RemoteFile) for f in files)
    assert not any(f.path == "av_archive" for f in files)  # directories are dropped
    dup = next(f for f in files if f.path == "perp_archive/date=2026-03-24/933300000_1774310589265.parquet")
    assert dup.size == 16298512
    assert dup.sha256 == "ccbe49b423114b07d1d8eb5d54c34445d6076885bdc249435f4e26b455fb8b57"
    assert dup.snapshot_ms == 1774310589265
    assert dup.date == "2026-03-24"


def test_snapshots_and_fills_select_only_their_own_files():
    files = hfmirror.parse_tree(tree_entries())
    snaps = Mirror.snapshots(files)
    fills = Mirror.fills(files)
    assert len(snaps) == 5
    assert all(f.path.startswith("perp_archive/") for f in snaps)
    assert len(fills) == 2
    assert all(f.path.startswith("fills_raw_") for f in fills)  # the fills/ duplicate copy is excluded


def test_coalesce_merges_spans_closer_than_the_gap():
    spans = [(2_000_000, 2_000_010), (0, 9), (15, 20), (21, 30)]
    assert coalesce(spans, max_gap=10) == [(0, 30), (2_000_000, 2_000_010)]
    assert coalesce(spans, max_gap=0) == [(0, 9), (15, 30), (2_000_000, 2_000_010)]


def test_sparse_file_serves_prefetched_bytes_and_fetches_a_gap_once():
    data = bytes(range(256)) * 4
    calls = []

    def fetch(start, end):
        calls.append((start, end))
        return data[start:end + 1]

    sf = SparseFile(len(data), fetch)
    sf.add(100, data[100:200])
    sf.seek(120)
    assert sf.read(10) == data[120:130]
    assert calls == []
    sf.seek(500)
    assert sf.read(8) == data[500:508]
    assert calls == [(500, 507)]
    sf.seek(502)
    assert sf.read(4) == data[502:506]  # now covered: no second fetch
    assert calls == [(500, 507)]
    assert sf.on_demand_reads == 1


def _parquet_bytes(tmp_path: Path) -> bytes:
    n = 30_000
    table = pa.table({
        "coin": pa.array(["BTC", "ETH", "xyz:NVDA"] * (n // 3)),
        "price": pa.array([float(i) for i in range(n)]),
        "tx_hash": pa.array([hashlib.sha256(str(i).encode()).hexdigest() for i in range(n)]),
        "address": pa.array([f"0x{i % 997:040x}" for i in range(n)]),
        "crossed": pa.array([i % 2 == 0 for i in range(n)]),
    })
    path = tmp_path / "f.parquet"
    pq.write_table(table, path, row_group_size=10_000, compression="snappy")
    return path.read_bytes()


def test_read_parquet_columns_equals_full_read_and_skips_other_columns(tmp_path):
    raw = _parquet_bytes(tmp_path)
    fetched = []

    def fetch(start, end):
        fetched.append(end - start + 1)
        return raw[start:end + 1]

    table, info = hfmirror.read_parquet_columns(fetch, len(raw), ["coin", "crossed"], max_gap=0)
    expected = pq.read_table(tmp_path / "f.parquet", columns=["coin", "crossed"])
    assert table.equals(expected)
    assert info["rows"] == 30_000
    assert info["on_demand_reads"] == 0
    assert sum(fetched) < 0.5 * len(raw)  # the tx_hash and address columns were never transferred


def test_read_parquet_columns_rejects_a_missing_column(tmp_path):
    raw = _parquet_bytes(tmp_path)
    with pytest.raises(MirrorError, match="start_position"):
        hfmirror.read_parquet_columns(lambda s, e: raw[s:e + 1], len(raw), ["coin", "start_position"])


def test_download_checks_sha256_and_never_leaves_a_bad_file(tmp_path):
    payload = b"snapshot-bytes" * 1000
    good = hashlib.sha256(payload).hexdigest()
    routes = Routes(files={"/files/perp_archive/date=2026-01-01/1_2.parquet": payload},
                    redirects={f"/datasets/r/resolve/{REV}/perp_archive/date=2026-01-01/1_2.parquet":
                               "/files/perp_archive/date=2026-01-01/1_2.parquet"})
    with serve(routes) as base:
        m = Mirror(revision=REV, repo="r", hub=base, http=fast_http())
        dest = tmp_path / "out.parquet"
        bad = RemoteFile("perp_archive/date=2026-01-01/1_2.parquet", len(payload), "0" * 64)
        with pytest.raises(MirrorError, match="SHA-256"):
            m.download(bad, dest)
        assert not dest.exists()
        ok = RemoteFile(bad.path, len(payload), good)
        assert m.download(ok, dest).read_bytes() == payload


def test_download_resumes_a_partial_copy_with_a_range_request(tmp_path):
    payload = bytes(range(256)) * 400
    path = "perp_archive/date=2026-01-02/3_4.parquet"
    routes = Routes(files={f"/files/{path}": payload},
                    redirects={f"/datasets/r/resolve/{REV}/{path}": f"/files/{path}"})
    dest = tmp_path / "snap.parquet"
    part = dest.with_suffix(".parquet.part")
    part.write_bytes(payload[:40_000])
    with serve(routes) as base:
        m = Mirror(revision=REV, repo="r", hub=base, http=fast_http())
        m.download(RemoteFile(path, len(payload), hashlib.sha256(payload).hexdigest()), dest)
    assert dest.read_bytes() == payload
    ranges = [h.get("Range") for method, p, h in routes.log if method == "GET" and p == f"/files/{path}"]
    assert "bytes=40000-" in ranges


def test_list_files_follows_link_pagination():
    page1 = [{"type": "file", "path": "fills_raw_2025-07-28.parquet", "size": 10, "oid": "a",
              "lfs": {"oid": "1" * 64, "size": 10, "pointerSize": 133}}]
    page2 = [{"type": "file", "path": "fills_raw_2025-07-29.parquet", "size": 11, "oid": "b",
              "lfs": {"oid": "2" * 64, "size": 11, "pointerSize": 133}},
             {"type": "directory", "path": "fills", "size": 0, "oid": "c"}]
    first = f"/api/datasets/r/tree/{REV}?recursive=true&limit=1000"
    routes = Routes(json_pages={first: (page1, "/page2"), "/page2": (page2, None)})
    with serve(routes) as base:
        files = Mirror(revision=REV, repo="r", hub=base, http=fast_http()).list_files()
    assert [f.path for f in files] == ["fills_raw_2025-07-28.parquet", "fills_raw_2025-07-29.parquet"]
    assert files[1].sha256 == "2" * 64


def test_read_range_retries_through_a_temporary_503():
    payload = b"0123456789" * 100
    path = "fills_raw_2025-08-01.parquet"
    routes = Routes(files={f"/files/{path}": payload},
                    redirects={f"/datasets/r/resolve/{REV}/{path}": f"/files/{path}"},
                    fail_first={f"/files/{path}": (1, 503)})
    with serve(routes) as base:
        m = Mirror(revision=REV, repo="r", hub=base, http=fast_http())
        assert m.read_range(path, 10, 19) == payload[10:20]


def _fills_file(tmp_path: Path) -> bytes:
    n = 1_000
    cols = {name: pa.array([f"{name}{i % 7}" for i in range(n)]) for name in hfmirror.FILL_COLUMNS}
    cols["price"] = pa.array([i for i in range(n)], type=pa.decimal128(20, 10))
    cols["size"] = pa.array([1] * n, type=pa.decimal128(20, 10))
    cols["liquidation_mark_px"] = pa.array([None] * n, type=pa.decimal128(20, 10))
    cols["crossed"] = pa.array([i % 2 == 0 for i in range(n)])
    cols["tx_hash"] = pa.array(["f" * 66] * n)  # an unwanted column that must not be transferred
    path = tmp_path / "fills.parquet"
    pq.write_table(pa.table(cols), path, row_group_size=400)
    return path.read_bytes()


def test_fetch_fills_slim_writes_provenance_and_skips_a_day_it_already_has(tmp_path):
    raw = _fills_file(tmp_path)
    path = "fills_raw_2025-09-01.parquet"
    routes = Routes(files={f"/files/{path}": raw}, redirects={f"/datasets/r/resolve/{REV}/{path}": f"/files/{path}"})
    rf = RemoteFile(path, len(raw), hashlib.sha256(raw).hexdigest())
    with serve(routes) as base:
        m = Mirror(revision=REV, repo="r", hub=base, http=fast_http())
        [out] = hfmirror.fetch_fills_slim(m, tmp_path / "cache", [rf], workers=1)
        side = json.loads(out.with_suffix(".json").read_text())
        table = pq.read_table(out)
        assert table.column_names == list(hfmirror.FILL_COLUMNS)
        assert table.schema.field("price").type == pa.float64()
        assert table.column("price").to_pylist()[:3] == [0.0, 1.0, 2.0]
        assert side["source_sha256"] == rf.sha256
        assert side["revision"] == REV
        assert side["rows"] == 1_000
        gets_after_first = sum(1 for method, *_ in routes.log if method == "GET")
        hfmirror.fetch_fills_slim(m, tmp_path / "cache", [rf], workers=1)
        assert sum(1 for method, *_ in routes.log if method == "GET") == gets_after_first  # cached: no traffic
        changed = RemoteFile(path, len(raw), "9" * 64)
        hfmirror.fetch_fills_slim(m, tmp_path / "cache", [changed], workers=1)
        assert sum(1 for method, *_ in routes.log if method == "GET") > gets_after_first  # new hash: refetched


def test_slim_casts_decimals_to_float_and_keeps_everything_else():
    t = pa.table({"price": pa.array([1, 2], type=pa.decimal128(20, 10)), "coin": ["BTC", "ETH"]})
    s = hfmirror.slim(t)
    assert s.schema.field("price").type == pa.float64()
    assert s.column("price").to_pylist() == [1.0, 2.0]
    assert s.column("coin").to_pylist() == ["BTC", "ETH"]

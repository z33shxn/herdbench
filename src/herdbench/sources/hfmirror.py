"""The public Hugging Face mirror of Hyperliquid's all-account position snapshots and fills.

Repository ``craftify2221/hyperliquid-fills-raw``: no login and no key. Every request is pinned to one
dataset revision (a commit hash), so a rerun reads exactly the same bytes.

* Position snapshots (``perp_archive/date=YYYY-MM-DD/<block>_<ms>.parquet``) are copied verbatim and
  checked against the SHA-256 that the Hub publishes for each file.
* Fill files (``fills_raw_YYYY-MM-DD.parquet``) are read column by column through HTTP range requests:
  only :data:`FILL_COLUMNS` are transferred (about a sixth of each file), then saved as compact Parquet.
"""

from __future__ import annotations

import concurrent.futures as cf
import hashlib
import io
import json
import os
import re
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from herdbench._http import HttpClient
from herdbench.config import MIRROR_REPO

HUB = "https://huggingface.co"

#: The 14 fill columns this project reads; everything a gate or a cohort rule needs, nothing else.
FILL_COLUMNS: tuple[str, ...] = (
    "coin",
    "dex",
    "asset_class",
    "price",
    "size",
    "side",
    "timestamp",
    "direction",
    "address",
    "crossed",
    "twap_id",
    "is_liquidation",
    "liquidation_mark_px",
    "liquidation_method",
)

_SNAPSHOT_RE = re.compile(r"^perp_archive/date=(\d{4}-\d{2}-\d{2})/(\d+)_(\d+)\.parquet$")
_FILL_RE = re.compile(r"^fills_raw_(\d{4}-\d{2}-\d{2})\.parquet$")

Fetch = Callable[[int, int], bytes]


class MirrorError(RuntimeError):
    pass


@dataclass(frozen=True)
class RemoteFile:
    path: str
    size: int
    sha256: str | None

    @property
    def date(self) -> str | None:
        m = _SNAPSHOT_RE.match(self.path) or _FILL_RE.match(self.path)
        return m.group(1) if m else None

    @property
    def snapshot_ms(self) -> int | None:
        """Capture time of a snapshot, in Unix milliseconds, from its file name."""
        m = _SNAPSHOT_RE.match(self.path)
        return int(m.group(3)) if m else None


def parse_tree(entries: Iterable[dict]) -> list[RemoteFile]:
    """Turn Hub tree-API entries into files (directories dropped; LFS hash kept)."""
    out = []
    for e in entries:
        if e.get("type") != "file":
            continue
        lfs = e.get("lfs") or {}
        out.append(RemoteFile(e["path"], int(e.get("size") or lfs.get("size") or 0), lfs.get("oid")))
    return out


def coalesce(spans: Iterable[tuple[int, int]], max_gap: int) -> list[tuple[int, int]]:
    """Merge inclusive byte spans whose gap is at most ``max_gap`` bytes."""
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start - merged[-1][1] - 1 <= max_gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


class SparseFile(io.RawIOBase):
    """Read-only file object over prefetched byte segments; any uncovered read is fetched on demand."""

    def __init__(self, size: int, fetch: Fetch):
        super().__init__()
        self.size = size
        self._fetch = fetch
        self._segments: list[tuple[int, bytes]] = []
        self._pos = 0
        self.bytes_fetched = 0
        self.on_demand_reads = 0

    def add(self, start: int, data: bytes) -> None:
        self._segments.append((start, data))
        self._segments.sort(key=lambda seg: seg[0])
        self.bytes_fetched += len(data)

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = 0) -> int:
        self._pos = {0: 0, 1: self._pos, 2: self.size}[whence] + offset
        return self._pos

    def read(self, n: int | None = -1) -> bytes:
        if n is None or n < 0:
            n = self.size - self._pos
        n = max(0, min(n, self.size - self._pos))
        if n == 0:
            return b""
        start, end = self._pos, self._pos + n
        for seg_start, data in self._segments:
            if seg_start <= start and end <= seg_start + len(data):
                self._pos = end
                return data[start - seg_start : end - seg_start]
        self.on_demand_reads += 1
        data = self._fetch(start, end - 1)
        self.add(start, data)
        self._pos = end
        return data

    def readinto(self, b) -> int:
        data = self.read(len(b))
        b[: len(data)] = data
        return len(data)


def read_parquet_columns(
    fetch: Fetch, size: int, columns: Sequence[str], max_gap: int = 1 << 20, workers: int = 4
) -> tuple[pa.Table, dict]:
    """Read ``columns`` of a remote Parquet file of ``size`` bytes, fetching only their byte spans.

    Correctness never depends on the span plan: any byte the reader needs that was not prefetched is
    fetched on demand (and counted in ``on_demand_reads``).
    """
    tail = min(size, 1 << 16)
    footer = fetch(size - tail, size - 1)
    if footer[-4:] != b"PAR1":
        raise MirrorError("not a Parquet file (missing PAR1 footer)")
    meta_len = int.from_bytes(footer[-8:-4], "little")
    if meta_len + 8 > len(footer):
        footer = fetch(size - meta_len - 8, size - 1)
    sparse = SparseFile(size, fetch)
    sparse.add(size - len(footer), footer)
    md = pq.ParquetFile(sparse).metadata
    names = [md.schema.column(i).path for i in range(md.num_columns)]
    missing = [c for c in columns if c not in names]
    if missing:
        raise MirrorError(f"Parquet file lacks columns {missing}")
    wanted = sorted(names.index(c) for c in columns)
    spans = []
    for g in range(md.num_row_groups):
        rg = md.row_group(g)
        for i in wanted:
            col = rg.column(i)
            start = col.data_page_offset
            if col.has_dictionary_page and col.dictionary_page_offset:
                start = min(start, col.dictionary_page_offset)
            spans.append((start, start + col.total_compressed_size - 1))
    plan = coalesce(spans, max_gap)
    with cf.ThreadPoolExecutor(max(1, workers)) as pool:
        for (start, _end), data in zip(plan, pool.map(lambda se: fetch(se[0], se[1]), plan), strict=True):
            sparse.add(start, data)
    table = pq.ParquetFile(sparse).read(columns=list(columns))
    if table.num_rows != md.num_rows:
        raise MirrorError(f"read {table.num_rows} rows but the footer records {md.num_rows}")
    info = {
        "rows": md.num_rows,
        "row_groups": md.num_row_groups,
        "bytes_transferred": sparse.bytes_fetched,
        "on_demand_reads": sparse.on_demand_reads,
        "created_by": md.created_by,
    }
    return table, info


def slim(table: pa.Table) -> pa.Table:
    """Cast decimal columns (price, size, liquidation mark) to float64; leave every other column as is."""
    return pa.table(
        {
            name: (col.cast(pa.float64()) if pa.types.is_decimal(col.type) else col)
            for name, col in zip(table.column_names, table.columns, strict=True)
        }
    )


class Mirror:
    """Client for one pinned revision of the mirror."""

    def __init__(self, revision: str, repo: str = MIRROR_REPO, hub: str = HUB, http: HttpClient | None = None):
        self.revision = revision
        self.repo = repo
        self.hub = hub.rstrip("/")
        self.http = http or HttpClient()
        self._resolved: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    # metadata ------------------------------------------------------------------------------------
    def list_files(self) -> list[RemoteFile]:
        url: str | None = f"{self.hub}/api/datasets/{self.repo}/tree/{self.revision}?recursive=true&limit=1000"
        out: list[RemoteFile] = []
        while url:
            r = self.http.get(url)
            if r.status_code != 200:
                raise MirrorError(f"listing failed: HTTP {r.status_code}")
            out.extend(parse_tree(r.json()))
            url = r.links.get("next", {}).get("url")
        return out

    @staticmethod
    def snapshots(files: Iterable[RemoteFile]) -> list[RemoteFile]:
        return sorted((f for f in files if _SNAPSHOT_RE.match(f.path)), key=lambda f: f.path)

    @staticmethod
    def fills(files: Iterable[RemoteFile]) -> list[RemoteFile]:
        return sorted((f for f in files if _FILL_RE.match(f.path)), key=lambda f: f.path)

    # bytes ---------------------------------------------------------------------------------------
    def resolve(self, path: str, force: bool = False) -> str:
        """Follow the Hub's redirect once and reuse the final (signed) URL for up to 30 minutes."""
        with self._lock:
            hit = self._resolved.get(path)
        if hit and not force and time.time() - hit[1] < 1800:
            return hit[0]
        r = self.http.get(
            f"{self.hub}/datasets/{self.repo}/resolve/{self.revision}/{path}",
            headers={"Range": "bytes=0-0"},
            allow_redirects=True,
        )
        if r.status_code not in (200, 206):
            raise MirrorError(f"cannot resolve {path}: HTTP {r.status_code}")
        with self._lock:
            self._resolved[path] = (r.url, time.time())
        return r.url

    def read_range(self, path: str, start: int, end_inclusive: int) -> bytes:
        want = end_inclusive - start + 1
        last = ""
        for attempt in range(3):  # a fresh resolve recovers from an expired signed URL
            r = self.http.get(
                self.resolve(path, force=attempt > 0), headers={"Range": f"bytes={start}-{end_inclusive}"}
            )
            if r.status_code == 206 and len(r.content) == want:
                return r.content
            last = f"HTTP {r.status_code}, {len(r.content)} of {want} bytes"
        raise MirrorError(f"range {start}-{end_inclusive} of {path} failed: {last}")

    def download(self, rf: RemoteFile, dest: Path, chunk: int = 1 << 20) -> Path:
        """Download a whole file (resuming a partial copy), then verify its size and SHA-256."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and dest.stat().st_size == rf.size and (rf.sha256 is None or sha256_file(dest) == rf.sha256):
            return dest
        part = dest.with_name(dest.name + ".part")
        for attempt in range(2):
            have = part.stat().st_size if part.exists() else 0
            if have > rf.size:
                part.unlink()
                have = 0
            if have < rf.size:
                headers = {"Range": f"bytes={have}-"} if have else {}
                with self.http.get(self.resolve(rf.path, force=attempt > 0), headers=headers, stream=True) as r:
                    if r.status_code not in (200, 206):
                        raise MirrorError(f"HTTP {r.status_code} downloading {rf.path}")
                    mode = "ab" if (r.status_code == 206 and have) else "wb"
                    with open(part, mode) as fh:
                        for block in r.iter_content(chunk):
                            fh.write(block)
            if part.stat().st_size == rf.size and (rf.sha256 is None or sha256_file(part) == rf.sha256):
                os.replace(part, dest)
                return dest
            part.unlink()
        raise MirrorError(f"{rf.path}: size or SHA-256 does not match the published value")

    def read_columns(self, rf: RemoteFile, columns: Sequence[str] = FILL_COLUMNS) -> tuple[pa.Table, dict]:
        table, info = read_parquet_columns(lambda s, e: self.read_range(rf.path, s, e), rf.size, columns)
        info.update({"path": rf.path, "revision": self.revision, "source_size": rf.size, "source_sha256": rf.sha256})
        return table, info


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def fetch_snapshots(mirror: Mirror, root: Path, files: Iterable[RemoteFile], workers: int = 2) -> list[Path]:
    """Download every snapshot under ``root`` keeping the mirror's relative paths."""
    snaps = Mirror.snapshots(files)
    with cf.ThreadPoolExecutor(workers) as pool:
        return list(pool.map(lambda rf: mirror.download(rf, root / rf.path), snaps))


def slim_fill_path(root: Path, day: str) -> Path:
    return root / "fills_slim" / f"fills_{day}.parquet"


def fetch_fills_slim(mirror: Mirror, root: Path, files: Iterable[RemoteFile], workers: int = 2) -> list[Path]:
    """Save :data:`FILL_COLUMNS` of every fill day under ``root/fills_slim`` with a provenance sidecar."""

    def one(rf: RemoteFile) -> Path:
        out = slim_fill_path(root, str(rf.date))
        side = out.with_suffix(".json")
        if out.exists() and side.exists():
            meta = json.loads(side.read_text(encoding="utf-8"))
            if (
                meta.get("source_sha256") == rf.sha256
                and meta.get("revision") == mirror.revision
                and meta.get("columns") == list(FILL_COLUMNS)
            ):
                return out
        t0 = time.monotonic()
        table, info = mirror.read_columns(rf, FILL_COLUMNS)
        table = slim(table)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.name + ".tmp")
        pq.write_table(
            table, tmp, compression="zstd", compression_level=3, use_dictionary=True, row_group_size=1_000_000
        )
        os.replace(tmp, out)
        info.update(
            {
                "columns": list(FILL_COLUMNS),
                "decimal_to_float64": ["price", "size", "liquidation_mark_px"],
                "seconds": round(time.monotonic() - t0, 1),
                "local_bytes": out.stat().st_size,
            }
        )
        _write_json(side, info)
        return out

    with cf.ThreadPoolExecutor(workers) as pool:
        return list(pool.map(one, Mirror.fills(files)))

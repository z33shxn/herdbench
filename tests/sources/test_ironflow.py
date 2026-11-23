from datetime import datetime, timezone
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from herdbench._http import HttpClient
from herdbench.sources import ironflow
from tests.utils.server import Routes, serve

REV = "b2153cf3aed73b6b200a378064538a2263923e9d"


def test_market_names_map_to_snapshot_names():
    assert ironflow.normalize_market("AAVE-PERP") == "AAVE"
    assert ironflow.normalize_market("xyz:AAPL-PERP") == "xyz:AAPL"
    assert ironflow.normalize_market("kPEPE-PERP") == "kPEPE"
    assert ironflow.normalize_market("PURR/USDC") == "PURR/USDC"


def funding_month(tmp_path: Path) -> bytes:
    t0 = datetime(2026, 3, 1, 0, 0, 0, 168000, tzinfo=timezone.utc)
    table = pa.table(
        {
            "timestamp": pa.array([t0, t0, t0.replace(hour=1)], type=pa.timestamp("us", tz="UTC")),
            "market": ["AAVE-PERP", "xyz:AAPL-PERP", "AAVE-PERP"],
            "coin": ["AAVE", "AAPL", "AAVE"],
            "dex": ["", "xyz", ""],
            "funding_rate": [0.0000125, -0.00004, 0.00002],
            "premium": [-0.000321, -0.0008, 0.0001],
        }
    )
    path = tmp_path / "2026-03.parquet"
    pq.write_table(table, path)
    return path.read_bytes()


def test_funding_downloads_pinned_months_and_normalises_names(tmp_path):
    routes = Routes(
        files={f"/datasets/ironflow/hyperliquid-funding/resolve/{REV}/data/2026-03.parquet": funding_month(tmp_path)}
    )
    with serve(routes) as base:
        src = ironflow.Ironflow(
            "hyperliquid-funding",
            revision=REV,
            hub=base,
            http=HttpClient(retries=2, backoff=0.0),
            cache_dir=tmp_path / "cache",
        )
        df = src.funding(["2026-03"])
    assert df.columns == ["market", "time", "funding", "premium"]
    aave = df.filter(pl.col("market") == "AAVE").sort("time")
    assert aave["funding"].to_list() == pytest.approx([0.0000125, 0.00002])
    assert df.filter(pl.col("market") == "xyz:AAPL")["premium"].to_list() == pytest.approx([-0.0008])

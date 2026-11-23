"""Ironflow's public Hyperliquid datasets on Hugging Face (CC BY 4.0; no login).

Used for hourly funding rates and premiums: every settled rate from Hyperliquid's own ``fundingHistory``,
in monthly Parquet files. HerdBench pins a dataset revision and cross-checks a random sample of market-hours
against the API itself (``herdbench check``, results/funding_check.json). Market names carry a
``-PERP`` suffix that is removed so they match snapshot market names.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import polars as pl

from herdbench._http import HttpClient

HUB = "https://huggingface.co"
FUNDING_REVISION = "b2153cf3aed73b6b200a378064538a2263923e9d"


def normalize_market(name: str) -> str:
    return name[:-5] if name.endswith("-PERP") else name


class Ironflow:
    def __init__(
        self, dataset: str, revision: str, hub: str = HUB, http: HttpClient | None = None, cache_dir: Path | None = None
    ):
        self.dataset = dataset
        self.revision = revision
        self.hub = hub.rstrip("/")
        self.http = http or HttpClient()
        self.cache_dir = Path(cache_dir) if cache_dir else None

    def month(self, ym: str) -> Path:
        """Local copy of one monthly file (downloaded once, then reused)."""
        if self.cache_dir is None:
            raise ValueError("cache_dir is required")
        dest = self.cache_dir / self.dataset / f"{ym}.parquet"
        if dest.exists():
            return dest
        r = self.http.get(f"{self.hub}/datasets/ironflow/{self.dataset}/resolve/{self.revision}/data/{ym}.parquet")
        if r.status_code != 200:
            raise RuntimeError(f"Ironflow {self.dataset} {ym}: HTTP {r.status_code}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        tmp.write_bytes(r.content)
        tmp.replace(dest)
        return dest

    def funding(self, months: Iterable[str]) -> pl.DataFrame:
        parts = [
            pl.read_parquet(self.month(ym), columns=["timestamp", "market", "funding_rate", "premium"]) for ym in months
        ]
        df = pl.concat(parts)
        return (
            df.with_columns(
                pl.col("market").map_elements(normalize_market, return_dtype=pl.String),
                pl.col("timestamp").dt.replace_time_zone("UTC").dt.cast_time_unit("us"),
            )
            .rename({"timestamp": "time", "funding_rate": "funding"})
            .select(["market", "time", "funding", "premium"])
            .unique(["market", "time"], keep="first")
            .sort(["market", "time"])
        )

"""All-account position snapshots: pick one file per UTC date, read it, and summarise each market."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq

from herdbench.config import PREREG

_DIR = re.compile(r"^date=(\d{4})-(\d{2})-(\d{2})$")
_FILE = re.compile(r"^(\d+)_(\d+)\.parquet$")
READ_COLUMNS = ["user", "market", "size", "notional", "liquidation_price", "leverage", "account_value"]

#: Builder-dex backstop liquidators: 0x4 followed by 36 zeros, then the dex index in the last 3 hex digits.
_LIQUIDATOR_PREFIX = "0x4" + "0" * 36


def local_index(root: Path) -> dict[date, Path]:
    """Map each UTC date to its snapshot file under ``root/perp_archive``; the earliest capture wins."""
    best: dict[date, tuple[int, Path]] = {}
    for folder in sorted((Path(root) / "perp_archive").glob("date=*")):
        m = _DIR.match(folder.name)
        if not m:
            continue
        day = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        for f in folder.glob("*.parquet"):
            fm = _FILE.match(f.name)
            if fm and (day not in best or int(fm.group(2)) < best[day][0]):
                best[day] = (int(fm.group(2)), f)
    return {day: best[day][1] for day in sorted(best)}


def capture_ms(path: Path) -> int:
    m = _FILE.match(Path(path).name)
    if not m:
        raise ValueError(f"not a snapshot file name: {path}")
    return int(m.group(2))


def read_raw(path: Path) -> pl.DataFrame:
    """Every position in the snapshot (protocol accounts included), zero sizes dropped, addresses lower-cased."""
    table = pq.ParquetFile(path).read(columns=READ_COLUMNS)  # ParquetFile: no hive-partition column is added
    df = pl.from_arrow(table)
    assert isinstance(df, pl.DataFrame)
    return df.with_columns(
        pl.col("user").str.to_lowercase(),
        pl.col("size").cast(pl.Float64),
        pl.col("notional").cast(pl.Float64),
        pl.col("liquidation_price").cast(pl.Float64),
        pl.col("account_value").cast(pl.Float64),
    ).filter(pl.col("size") != 0)


def dex_expr() -> pl.Expr:
    return (
        pl.when(pl.col("market").str.contains(":"))
        .then(pl.col("market").str.split(":").list.first())
        .otherwise(pl.lit("main"))
    )


def market_table(raw: pl.DataFrame) -> pl.DataFrame:
    """Per market: mark (gross notional / gross size), open interest and the long-short identity gap."""
    is_long = pl.col("size") > 0
    agg = raw.group_by("market").agg(
        long_ntl=pl.col("notional").filter(is_long).sum(),
        short_ntl=pl.col("notional").filter(~is_long).sum(),
        oi_contracts=pl.col("size").filter(is_long).sum(),
        abs_size=pl.col("size").abs().sum(),
        n_positions=pl.len().cast(pl.Int64),
    )
    return (
        agg.with_columns(
            dex=dex_expr(),
            mark=(pl.col("long_ntl") + pl.col("short_ntl")) / pl.col("abs_size"),
            oi_notional=pl.col("long_ntl"),
            long_short_gap=(pl.col("long_ntl") - pl.col("short_ntl")) / (pl.col("long_ntl") + pl.col("short_ntl")),
        )
        .select(["market", "dex", "mark", "oi_contracts", "oi_notional", "long_short_gap", "n_positions"])
        .sort("market")
    )


def entity_account_values(raw: pl.DataFrame, emap: dict[str, str]) -> pl.DataFrame:
    """Entity account value: each address's largest reported value (no double counting across the dexes
    an account spans), summed over the entity's addresses."""
    per_addr = raw.group_by("user").agg(av=pl.col("account_value").max())
    if emap:
        mapping = pl.DataFrame({"user": list(emap), "_entity": list(emap.values())})
        per_addr = per_addr.join(mapping, on="user", how="left").with_columns(entity=pl.coalesce("_entity", "user"))
    else:
        per_addr = per_addr.with_columns(entity=pl.col("user"))
    return per_addr.group_by("entity").agg(av=pl.col("av").sum()).select(["entity", "av"]).sort("entity")


def protocol_expr(col: str = "user") -> pl.Expr:
    return pl.col(col).is_in(list(PREREG.protocol_addresses)) | (
        pl.col(col).str.starts_with(_LIQUIDATOR_PREFIX) & (pl.col(col).str.len_chars() == 42)
    )


def without_protocol(raw: pl.DataFrame) -> pl.DataFrame:
    return raw.filter(~protocol_expr("user"))

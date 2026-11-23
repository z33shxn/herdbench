"""Write miniature archives in the mirror's exact schemas, for end-to-end tests without the network."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

SNAPSHOT_SCHEMA = pa.schema(
    [
        ("user", pa.string()),
        ("market", pa.string()),
        ("size", pa.float64()),
        ("notional", pa.float64()),
        ("entry_price", pa.float64()),
        ("liquidation_price", pa.float64()),
        ("leverage_type", pa.string()),
        ("leverage", pa.uint32()),
        ("funding_pnl", pa.float64()),
        ("account_value", pa.float64()),
        ("account_mode", pa.string()),
    ]
)

FILL_SCHEMA = pa.schema(
    [
        ("coin", pa.string()),
        ("dex", pa.string()),
        ("asset_class", pa.string()),
        ("price", pa.float64()),
        ("size", pa.float64()),
        ("side", pa.string()),
        ("timestamp", pa.timestamp("ms", tz="UTC")),
        ("direction", pa.string()),
        ("address", pa.string()),
        ("crossed", pa.bool_()),
        ("twap_id", pa.uint64()),
        ("is_liquidation", pa.bool_()),
        ("liquidation_mark_px", pa.float64()),
        ("liquidation_method", pa.string()),
    ]
)


def snapshot(root: Path, day: date, rows: list[dict], capture_ms: int | None = None) -> Path:
    ms = capture_ms or int(datetime(day.year, day.month, day.day, 0, 5, tzinfo=timezone.utc).timestamp() * 1000)
    path = Path(root) / "perp_archive" / f"date={day:%Y-%m-%d}" / f"1_{ms}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    full = [
        {
            "entry_price": 1.0,
            "liquidation_price": None,
            "leverage_type": "cross",
            "leverage": 3,
            "funding_pnl": 0.0,
            "account_value": 1000.0,
            "account_mode": None,
            **r,
        }
        for r in rows
    ]
    pq.write_table(pa.Table.from_pylist(full, schema=SNAPSHOT_SCHEMA), path)
    return path


def fills(root: Path, day: date, rows: list[dict]) -> Path:
    path = Path(root) / "fills_slim" / f"fills_{day:%Y-%m-%d}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    noon = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)
    full = [
        {
            "dex": "hyperliquid",
            "asset_class": "perp",
            "side": "buy",
            "timestamp": noon,
            "direction": "Open Long",
            "crossed": True,
            "twap_id": None,
            "is_liquidation": False,
            "liquidation_mark_px": None,
            "liquidation_method": None,
            **r,
        }
        for r in rows
    ]
    pq.write_table(pa.Table.from_pylist(full, schema=FILL_SCHEMA), path)
    return path

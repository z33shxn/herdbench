"""Market-level aggregates from fills: liquidations, volume and hourly price bars.

In the archive, ``is_liquidation`` is true only on the liquidated account's own fills (market and
backstop liquidations alike), so summing those fills counts each liquidation once. A liquidated long is
closed by a sale (side ``sell``); a liquidated short by a purchase (side ``buy``). Every trade appears once
per counterparty, with one taker and one maker, so volume and price bars use the taker rows only; the maker
notional is kept to check that maker and taker sides balance (Gate 0).
"""

from __future__ import annotations

from datetime import date

import polars as pl


def daily_liquidations(fills: pl.DataFrame, day: date) -> pl.DataFrame:
    f = fills.filter((pl.col("asset_class") == "perp") & pl.col("is_liquidation").fill_null(False)).with_columns(
        ntl=pl.col("price") * pl.col("size")
    )
    return (
        f.group_by("coin")
        .agg(
            liq_ntl=pl.col("ntl").sum(),
            liq_long_ntl=pl.col("ntl").filter(pl.col("side") == "sell").sum(),
            liq_short_ntl=pl.col("ntl").filter(pl.col("side") == "buy").sum(),
            n_liq=pl.len().cast(pl.Int64),
        )
        .rename({"coin": "market"})
        .with_columns(day=pl.lit(day))
        .select(["day", "market", "liq_ntl", "liq_long_ntl", "liq_short_ntl", "n_liq"])
        .sort("market")
    )


def daily_volume(fills: pl.DataFrame, day: date) -> pl.DataFrame:
    f = fills.filter(pl.col("asset_class") == "perp").with_columns(ntl=pl.col("price") * pl.col("size"))
    return (
        f.group_by("coin")
        .agg(
            volume_ntl=pl.col("ntl").filter(pl.col("crossed")).sum(),
            maker_ntl=pl.col("ntl").filter(~pl.col("crossed")).sum(),
            n_trades=pl.col("crossed").sum().cast(pl.Int64),
        )
        .rename({"coin": "market"})
        .with_columns(day=pl.lit(day))
        .select(["day", "market", "volume_ntl", "maker_ntl", "n_trades"])
        .sort("market")
    )


def hourly_liquidations(fills: pl.DataFrame) -> pl.DataFrame:
    """Liquidated notional per market and UTC hour (labelled by its start), split into longs and shorts."""
    f = fills.filter((pl.col("asset_class") == "perp") & pl.col("is_liquidation").fill_null(False)).with_columns(
        hour=pl.col("timestamp").dt.truncate("1h"), ntl=pl.col("price") * pl.col("size")
    )
    return (
        f.group_by(["coin", "hour"])
        .agg(
            liq_ntl=pl.col("ntl").sum(),
            liq_long_ntl=pl.col("ntl").filter(pl.col("side") == "sell").sum(),
            liq_short_ntl=pl.col("ntl").filter(pl.col("side") == "buy").sum(),
        )
        .rename({"coin": "market"})
        .select(["market", "hour", "liq_ntl", "liq_long_ntl", "liq_short_ntl"])
        .sort(["market", "hour"])
    )


def hourly_bars(fills: pl.DataFrame) -> pl.DataFrame:
    """One bar per market and UTC hour (labelled by its start) from taker fills, in time order."""
    f = (
        fills.filter((pl.col("asset_class") == "perp") & pl.col("crossed"))
        .with_columns(hour=pl.col("timestamp").dt.truncate("1h"), ntl=pl.col("price") * pl.col("size"))
        .sort(["coin", "timestamp"])
    )
    return (
        f.group_by(["coin", "hour"], maintain_order=True)
        .agg(
            open=pl.col("price").first(),
            high=pl.col("price").max(),
            low=pl.col("price").min(),
            close=pl.col("price").last(),
            volume_ntl=pl.col("ntl").sum(),
            n_trades=pl.len().cast(pl.Int64),
        )
        .rename({"coin": "market"})
        .select(["market", "hour", "open", "high", "low", "close", "volume_ntl", "n_trades"])
        .sort(["market", "hour"])
    )

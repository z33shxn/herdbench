"""Market-maker classification from fills, on entities (a master and its sub-accounts together).

An entity is a market maker for snapshot date D if, over the 30 calendar days before D:
  * at least 80% of its perp traded notional was passive (maker),
  * it traded at least $10M,
  * it was active on at least 10 days, and
  * it traded at least 50 times its mean gross position notional across the window's snapshots
    (denominator floored at $1). Real market makers turn their inventory over many times a day; patient
    limit-order whales and carry traders, who are also mostly passive, turn it over far less.
The status is unknown when the window holds fewer than 15 days of archived fills; the last known set is
then carried forward for at most 15 days, after which the date has no non_mm cohort.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta

import polars as pl

from herdbench.config import PREREG


def daily_activity(fills: pl.DataFrame, day: date) -> pl.DataFrame:
    f = (fills.filter(pl.col("asset_class") == "perp")
         .with_columns(ntl=pl.col("price") * pl.col("size")))
    return (f.group_by("address").agg(
        maker_ntl=pl.col("ntl").filter(~pl.col("crossed")).sum(),
        taker_ntl=pl.col("ntl").filter(pl.col("crossed")).sum(),
        n_fills=pl.len().cast(pl.Int64))
        .with_columns(day=pl.lit(day))
        .select(["address", "day", "maker_ntl", "taker_ntl", "n_fills"])
        .sort("address"))


def entity_activity(activity: pl.DataFrame, emap: dict[str, str]) -> pl.DataFrame:
    a = activity.with_columns(pl.col("address").cast(pl.String))
    if emap:
        mapping = pl.DataFrame({"address": list(emap), "_entity": list(emap.values())})
        a = a.join(mapping, on="address", how="left").with_columns(entity=pl.coalesce("_entity", "address"))
    else:
        a = a.with_columns(entity=pl.col("address"))
    return (a.group_by(["entity", "day"]).agg(maker_ntl=pl.col("maker_ntl").sum(), taker_ntl=pl.col("taker_ntl").sum())
            .select(["entity", "day", "maker_ntl", "taker_ntl"]).sort(["entity", "day"]))


def market_makers(window_rows: pl.DataFrame, maker_share: float, min_notional: float, min_active_days: int,
                  mean_gross: pl.DataFrame | None = None,
                  turnover_multiple: float = PREREG.mm_turnover_multiple) -> set[str]:
    g = (window_rows.group_by("entity").agg(
        maker=pl.col("maker_ntl").sum(), taker=pl.col("taker_ntl").sum(), days=pl.col("day").n_unique())
         .with_columns(total=pl.col("maker") + pl.col("taker")))
    hit = g.filter((pl.col("total") > 0) & (pl.col("maker") / pl.col("total") >= maker_share)
                   & (pl.col("total") >= min_notional) & (pl.col("days") >= min_active_days))
    if mean_gross is not None:
        hit = (hit.join(mean_gross.select(["entity", "mean_gross"]), on="entity", how="left")
               .with_columns(_den=pl.max_horizontal(pl.col("mean_gross").fill_null(0.0), pl.lit(1.0)))
               .filter(pl.col("total") >= turnover_multiple * pl.col("_den")))
    return set(hit["entity"].to_list())


def mean_gross_in_window(gross: pl.DataFrame, snapshot_dates: Iterable[date], lo: date, hi: date) -> pl.DataFrame:
    """Mean gross position notional per entity over the window's snapshots (absent on a date = 0)."""
    dates = [d for d in snapshot_dates if lo <= d <= hi]
    if not dates:
        return pl.DataFrame(schema={"entity": pl.String, "mean_gross": pl.Float64})
    g = gross.filter(pl.col("date").is_in(dates)).group_by("entity").agg(total=pl.col("gross").sum())
    return g.with_columns(mean_gross=pl.col("total") / len(dates)).select(["entity", "mean_gross"])


def set_for(activity: pl.DataFrame, available_days: Iterable[date], asof: date,
            maker_share: float = PREREG.mm_maker_share, window_days: int = PREREG.mm_window_days,
            min_fill_days: int = PREREG.mm_min_fill_days, min_notional: float = PREREG.mm_min_notional,
            min_active_days: int = PREREG.mm_min_active_days, gross: pl.DataFrame | None = None,
            snapshot_dates: Iterable[date] = (), turnover_multiple: float = PREREG.mm_turnover_multiple
            ) -> set[str] | None:
    lo, hi = asof - timedelta(days=window_days), asof - timedelta(days=1)
    if sum(1 for d in set(available_days) if lo <= d <= hi) < min_fill_days:
        return None
    rows = activity.filter((pl.col("day") >= lo) & (pl.col("day") <= hi))
    mg = mean_gross_in_window(gross, snapshot_dates, lo, hi) if gross is not None else None
    return market_makers(rows, maker_share, min_notional, min_active_days, mg, turnover_multiple)


def carry_forward(known: dict[date, set[str]], asof: date, max_days: int = PREREG.mm_carry_forward_days) -> set[str] | None:
    past = [d for d in known if d <= asof and (asof - d).days <= max_days]
    return known[max(past)] if past else None

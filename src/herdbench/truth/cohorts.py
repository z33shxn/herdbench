"""Pre-registered crowd definitions, as boolean columns on one snapshot's entity holdings.

``all``         every non-protocol entity
``non_mm``      all minus market-maker entities (undefined, so empty, when the market-maker set is unknown)
``top_av``      inside non_mm: the top 20% of each market's rankable holders by account value 8 days earlier
                (an entity without a lagged value is not rankable); ties broken by entity
``trend``       inside non_mm: trailing trend score >= +0.20
``contrarian``  inside non_mm: trailing trend score <= -0.20
``non_mm_<x>``  robustness versions of non_mm with other market-maker rules (analysis only, never published)

Ranking on a lagged account value keeps a rally from promoting longs into ``top_av`` through their
unrealised profit, and nesting every sub-cohort inside non_mm keeps market-maker inventory out of them.
"""

from __future__ import annotations

import polars as pl

from herdbench.config import PREREG
from herdbench.truth.metrics import ceil_count


def _not_in(entities: set[str] | None) -> pl.Expr:
    if entities is None:
        return pl.lit(False)
    return ~pl.col("entity").is_in(sorted(entities))


def assign(holdings: pl.DataFrame, mm: set[str] | None, mm_robust: dict[str, set[str] | None],
           trend: pl.DataFrame | None, lagged_av: pl.DataFrame | None,
           top_fraction: float = PREREG.top_av_fraction, threshold: float = PREREG.trend_threshold) -> pl.DataFrame:
    h = holdings.with_columns(
        all=pl.lit(True),
        non_mm=_not_in(mm),
        **{name: _not_in(mm_robust.get(name)) for name in PREREG.robustness_cohorts})
    if lagged_av is not None and lagged_av.height:
        h = h.join(lagged_av.select(["entity", pl.col("av").alias("_av")]), on="entity", how="left")
    else:
        h = h.with_columns(_av=pl.lit(None, dtype=pl.Float64))
    h = (h.with_columns(_rankable=pl.col("non_mm") & pl.col("_av").is_not_null())
         .sort(["market", "_av", "entity"], descending=[False, True, False], nulls_last=True)
         .with_columns(_rank=pl.col("_rankable").cast(pl.Int64).cum_sum().over("market"),
                       _n=pl.col("_rankable").cast(pl.Int64).sum().over("market"))
         .with_columns(top_av=pl.col("_rankable") & (pl.col("_rank") <= ceil_count(pl.col("_n"), top_fraction)))
         .drop(["_av", "_rankable", "_rank", "_n"]))
    if trend is None or trend.height == 0:
        return h.with_columns(trend=pl.lit(False), contrarian=pl.lit(False))
    h = h.join(trend.select(["entity", pl.col("score").alias("_score")]), on="entity", how="left")
    return h.with_columns(
        trend=pl.col("non_mm") & (pl.col("_score") >= threshold).fill_null(False),
        contrarian=pl.col("non_mm") & (pl.col("_score") <= -threshold).fill_null(False)).drop("_score")

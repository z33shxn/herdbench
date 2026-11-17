"""Disclosure control for everything published. The analysis itself runs on all cells.

1. Cell rule: a cell is publishable only if at least 20 entities contribute and its two largest entities
   hold at most 80% of its gross notional (an (n, k) dominance rule).
2. Complement rule: for each nested pair published on a market-day (``all`` contains ``non_mm``;
   ``non_mm`` contains ``top_av``, ``trend`` and ``contrarian``), the difference between the two cells
   must pass the same rule, otherwise the larger cell is suppressed, so no cell can be recovered by
   subtracting two published ones.
3. Rounding: ratios to 3 decimals, notional to 2 significant figures, entity counts down to a multiple
   of 10. Robustness cohorts are never published as daily cells.
"""

from __future__ import annotations

import math

import polars as pl

from herdbench.config import PREREG

INTERNAL_COLUMNS: tuple[str, ...] = ("top2_share", "suppressed")
RATIO_COLUMNS: tuple[str, ...] = ("ni", "ai", "lwi", "abs_ni", "top10_share", "fragile_share", "coheld_share", "dtl")
NOTIONAL_COLUMNS: tuple[str, ...] = ("gross_notional", "net_notional", "oi_notional")
NESTED_PAIRS: tuple[tuple[str, str], ...] = (("all", "non_mm"), ("non_mm", "top_av"), ("non_mm", "trend"),
                                             ("non_mm", "contrarian"))


def _fails(n: pl.Expr, top2: pl.Expr, min_entities: int, top2_max: float) -> pl.Expr:
    return (n < min_entities) | (top2 > top2_max + 1e-12)


def apply_disclosure(cells: pl.DataFrame, min_entities: int = PREREG.min_entities,
                     top2_max: float = PREREG.dominance_top2_max) -> pl.DataFrame:
    return cells.with_columns(suppressed=_fails(pl.col("n_entities"), pl.col("top2_share"), min_entities, top2_max))


def apply_complement_rule(cells: pl.DataFrame, diffs: pl.DataFrame, min_entities: int = PREREG.min_entities,
                          top2_max: float = PREREG.dominance_top2_max) -> pl.DataFrame:
    """``diffs``: date, market, parent, child, n_entities, top2_share of each parent-minus-child set."""
    bad = (diffs.filter(_fails(pl.col("n_entities"), pl.col("top2_share"), min_entities, top2_max))
           .select(["date", "market", pl.col("parent").alias("cohort")]).unique()
           .with_columns(_hit=pl.lit(True)))
    return (cells.join(bad, on=["date", "market", "cohort"], how="left")
            .with_columns(suppressed=pl.col("suppressed") | pl.col("_hit").fill_null(False)).drop("_hit"))


def published(cells: pl.DataFrame) -> pl.DataFrame:
    return cells.filter(~pl.col("suppressed")).drop([c for c in INTERNAL_COLUMNS if c in cells.columns])


def _sig2(x: float | None) -> float | None:
    if x is None or x == 0 or (isinstance(x, float) and math.isnan(x)):
        return x
    return float(round(x, 1 - int(math.floor(math.log10(abs(x))))))


def publish_rounding(df: pl.DataFrame, decimals: int = 3) -> pl.DataFrame:
    exprs = [pl.col(c).round(decimals) for c in RATIO_COLUMNS if c in df.columns]
    exprs += [pl.col(c).map_elements(_sig2, return_dtype=pl.Float64) for c in NOTIONAL_COLUMNS if c in df.columns]
    if "n_entities" in df.columns:
        exprs.append(((pl.col("n_entities") // 10) * 10).cast(pl.Int64))
    return df.with_columns(exprs)

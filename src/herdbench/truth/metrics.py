"""Truth metrics for one cell (date x market x cohort), computed on entities.

Each entity's positions in a market (across its sub-accounts) are netted first (``entity_positions``):
an entity is long, short, or holds nothing net. On those entity holdings:

``ni``            = (L - S) / (L + S)                    net positioning (signed)
``ai``            = (entities long - entities short) / entities                (signed)
``lwi``           = sum sign * notional * w / sum notional * w, w = effective leverage (signed)
``abs_ni``        = |ni|                                                       (unsigned)
``top10_share``   = share of gross held by the largest 10% of the cell's entities (unsigned)
``fragile_share`` = share of the cell's address-level gross within 10% of liquidation (unsigned)
``coheld_share``  = share of gross held by entities with same-direction positions in at least 5 other
                    eligible markets that day (unsigned)
``top2_share`` feeds the disclosure rule and is never published.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import polars as pl

from herdbench.config import PREREG

KEYS: tuple[str, ...] = ("date", "market", "cohort")
METRICS: tuple[str, ...] = ("ni", "ai", "lwi", "abs_ni", "top10_share", "fragile_share", "coheld_share")


def ceil_count(n: pl.Expr, fraction: float) -> pl.Expr:
    """ceil(n * fraction) without float noise (0.1 * 30 is 3.0000000000000004 in binary floating point)."""
    return (n.cast(pl.Float64) * fraction).round(9).ceil()


def entity_positions(pos: pl.DataFrame, fragile_distance: float = PREREG.fragile_distance) -> pl.DataFrame:
    """Net each entity's address-level positions per market. Needs market, entity, size, notional,
    liquidation_price and mark (plus ``date`` if present). Entities with no net position are dropped."""
    keys = [k for k in ("date", "market", "entity") if k in pos.columns]
    p = pos.filter(pl.col("size") != 0).with_columns(
        sign=pl.when(pl.col("size") > 0).then(1.0).otherwise(-1.0),
        fragile=(((pl.col("mark") - pl.col("liquidation_price")).abs() / pl.col("mark")) <= fragile_distance).fill_null(
            False
        ),
    )
    g = p.group_by(keys).agg(
        signed=(pl.col("sign") * pl.col("notional")).sum(),
        gross_addr=pl.col("notional").sum(),
        fragile_ntl=pl.col("notional").filter(pl.col("fragile")).sum(),
        n_addr=pl.len().cast(pl.Int64),
    )
    return (
        g.filter(pl.col("signed").abs() > 1e-9 * pl.col("gross_addr"))
        .with_columns(notional=pl.col("signed").abs(), is_long=pl.col("signed") > 0)
        .drop("signed")
        .sort(keys)
    )


def coheld_flags(rows: pl.DataFrame, eligible: Iterable[str], min_other: int = 5) -> pl.DataFrame:
    """Flag holdings whose entity holds the same direction in at least ``min_other`` other eligible markets."""
    elig = sorted(set(eligible))
    r = rows.with_columns(_elig=pl.col("market").is_in(elig))
    counts = (
        r.filter(pl.col("_elig"))
        .group_by("entity")
        .agg(_nl=pl.col("is_long").sum().cast(pl.Int64), _ns=(~pl.col("is_long")).sum().cast(pl.Int64))
    )
    r = r.join(counts, on="entity", how="left").with_columns(pl.col("_nl").fill_null(0), pl.col("_ns").fill_null(0))
    same = pl.when(pl.col("is_long")).then(pl.col("_nl")).otherwise(pl.col("_ns")) - pl.col("_elig").cast(pl.Int64)
    return r.with_columns(coheld=same >= min_other).drop(["_elig", "_nl", "_ns"])


def cell_metrics(
    rows: pl.DataFrame, keys: Sequence[str] = KEYS, top_share: float = PREREG.top_share_fraction
) -> pl.DataFrame:
    """One row per cell from entity holdings: keys + entity, notional, is_long, w, gross_addr, fragile_ntl, coheld."""
    keys = list(keys)
    r = (
        rows.sort([*keys, "entity"])
        .with_columns(rank=pl.col("notional").rank(method="ordinal", descending=True).over(keys), n=pl.len().over(keys))
        .with_columns(k=ceil_count(pl.col("n"), top_share))
    )
    sign = pl.when(pl.col("is_long")).then(1.0).otherwise(-1.0)
    agg = r.group_by(keys).agg(
        n_entities=pl.len().cast(pl.Int64),
        long_ntl=pl.col("notional").filter(pl.col("is_long")).sum(),
        short_ntl=pl.col("notional").filter(~pl.col("is_long")).sum(),
        n_long=pl.col("is_long").sum().cast(pl.Float64),
        lev_num=(sign * pl.col("notional") * pl.col("w")).sum(),
        lev_den=(pl.col("notional") * pl.col("w")).sum(),
        top_k=pl.col("notional").filter(pl.col("rank") <= pl.col("k")).sum(),
        top2=pl.col("notional").filter(pl.col("rank") <= 2).sum(),
        fragile=pl.col("fragile_ntl").sum(),
        gross_addr=pl.col("gross_addr").sum(),
        coheld_ntl=pl.col("notional").filter(pl.col("coheld")).sum(),
    )
    n = pl.col("n_entities").cast(pl.Float64)
    out = (
        agg.with_columns(
            gross_notional=pl.col("long_ntl") + pl.col("short_ntl"),
            net_notional=pl.col("long_ntl") - pl.col("short_ntl"),
        )
        .with_columns(
            ni=pl.col("net_notional") / pl.col("gross_notional"),
            ai=(2.0 * pl.col("n_long") - n) / n,
            lwi=pl.when(pl.col("lev_den") > 0).then(pl.col("lev_num") / pl.col("lev_den")).otherwise(None),
            top10_share=pl.col("top_k") / pl.col("gross_notional"),
            top2_share=pl.col("top2") / pl.col("gross_notional"),
            fragile_share=pl.when(pl.col("gross_addr") > 0)
            .then(pl.col("fragile") / pl.col("gross_addr"))
            .otherwise(None),
            coheld_share=pl.col("coheld_ntl") / pl.col("gross_notional"),
        )
        .with_columns(abs_ni=pl.col("ni").abs())
    )
    return out.select([*keys, "n_entities", "gross_notional", "net_notional", *METRICS, "top2_share"]).sort(keys)

"""Trend-following and contrarian style scores from consecutive snapshots.

For snapshot dates t-2, t-1, t on consecutive calendar days, a wallet's flow in a market is
f = (size_t - size_{t-1}) * mark_t, paired with the market's previous return r = ln(mark_{t-1}/mark_{t-2}).
Daily contributions per wallet are c = sum f*r, a = sum |f|*|r| and the number of nonzero flows. The
score for snapshot D is sum c / sum a over flows dated in [D - window, D - 1]: +1 means every move was in
the direction of the previous day's return, -1 means every move went against it. Flows on or after D
never classify D, and nothing is differenced across a missing snapshot day.
"""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl

EMPTY_SCHEMA = {"user": pl.String, "date": pl.Date, "c": pl.Float64, "a": pl.Float64, "n_flows": pl.Int64}


def day_contributions(
    t: date, cur: pl.DataFrame, prev: pl.DataFrame, marks_t: pl.DataFrame, marks_1: pl.DataFrame, marks_2: pl.DataFrame
) -> pl.DataFrame:
    """Contributions dated t. ``cur``/``prev``: user, market, size at t and t-1. ``marks_*``: market, mark."""
    flows = (
        cur.select(["user", "market", "size"])
        .join(
            prev.select(["user", "market", pl.col("size").alias("size_prev")]),
            on=["user", "market"],
            how="full",
            coalesce=True,
        )
        .with_columns(pl.col("size").fill_null(0.0), pl.col("size_prev").fill_null(0.0))
        .with_columns(du=pl.col("size") - pl.col("size_prev"))
        .filter(pl.col("du") != 0)
    )
    ret = (
        marks_1.select(["market", pl.col("mark").alias("m_1")])
        .join(marks_2.select(["market", pl.col("mark").alias("m_2")]), on="market", how="inner")
        .with_columns(r=(pl.col("m_1") / pl.col("m_2")).log())
        .select(["market", "r"])
    )
    f = (
        flows.join(marks_t.select(["market", pl.col("mark").alias("m_t")]), on="market", how="inner")
        .join(ret, on="market", how="inner")
        .with_columns(f=pl.col("du") * pl.col("m_t"))
    )
    if f.height == 0:
        return pl.DataFrame(schema=EMPTY_SCHEMA)
    return (
        f.group_by("user")
        .agg(
            c=(pl.col("f") * pl.col("r")).sum(),
            a=(pl.col("f").abs() * pl.col("r").abs()).sum(),
            n_flows=pl.len().cast(pl.Int64),
        )
        .with_columns(date=pl.lit(t))
        .select(["user", "date", "c", "a", "n_flows"])
    )


def contributions(snaps: pl.DataFrame, marks: pl.DataFrame) -> pl.DataFrame:
    """In-memory version for small inputs. ``snaps``: date, user, market, size; ``marks``: date, market, mark."""
    days = sorted(set(snaps["date"].to_list()))
    have = set(days)
    out = []
    for t in days:
        t1, t2 = t - timedelta(days=1), t - timedelta(days=2)
        if t1 in have and t2 in have:
            out.append(
                day_contributions(
                    t,
                    snaps.filter(pl.col("date") == t),
                    snaps.filter(pl.col("date") == t1),
                    marks.filter(pl.col("date") == t),
                    marks.filter(pl.col("date") == t1),
                    marks.filter(pl.col("date") == t2),
                )
            )
    out = [o for o in out if o.height]
    if not out:
        return pl.DataFrame(schema=EMPTY_SCHEMA)
    return pl.concat(out).sort(["date", "user"])


def trend_scores(
    contrib: pl.DataFrame, asof: date, window_days: int, min_flows: int, key: str = "user"
) -> pl.DataFrame:
    """Score per ``key`` (an address, or an entity after ``entity_contributions``) for snapshot date ``asof``."""
    lo, hi = asof - timedelta(days=window_days), asof - timedelta(days=1)
    g = (
        contrib.filter((pl.col("date") >= lo) & (pl.col("date") <= hi))
        .group_by(key)
        .agg(c=pl.col("c").sum(), a=pl.col("a").sum(), n_flows=pl.col("n_flows").sum())
    )
    return (
        g.filter((pl.col("a") > 0) & (pl.col("n_flows") >= min_flows))
        .with_columns(score=pl.col("c") / pl.col("a"))
        .select([key, "score", "n_flows"])
        .sort(key)
    )


def entity_contributions(contrib: pl.DataFrame, emap: dict[str, str]) -> pl.DataFrame:
    """Sum address-level contributions within each entity (a master and its sub-accounts)."""
    c = contrib
    if emap:
        mapping = pl.DataFrame({"user": list(emap), "_entity": list(emap.values())})
        c = c.join(mapping, on="user", how="left").with_columns(entity=pl.coalesce("_entity", "user"))
    else:
        c = c.with_columns(entity=pl.col("user"))
    return (
        c.group_by(["entity", "date"])
        .agg(c=pl.col("c").sum(), a=pl.col("a").sum(), n_flows=pl.col("n_flows").sum())
        .sort(["date", "entity"])
    )

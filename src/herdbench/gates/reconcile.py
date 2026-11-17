"""Integrity gates that every result depends on.

* Reconciliation: for two consecutive snapshots, each account's change in position must equal the net of
  its signed fills timestamped after the first capture and up to the second. Changes without fills and
  fills without changes both count as mismatches, weighted by notional (|change| x mark).
* Gate 0: maker notional equals taker notional in every market (each trade has one maker and one taker).
"""

from __future__ import annotations

from datetime import datetime

import polars as pl

REL_TOL = 1e-6
ABS_TOL = 1e-9


def signed_fills(fills: pl.DataFrame, start: datetime, end: datetime) -> pl.DataFrame:
    f = fills.filter((pl.col("asset_class") == "perp") & (pl.col("timestamp") > start) & (pl.col("timestamp") <= end))
    signed = pl.when(pl.col("side") == "buy").then(pl.col("size")).otherwise(-pl.col("size"))
    return (f.with_columns(net=signed).group_by(["address", "coin"]).agg(net=pl.col("net").sum())
            .rename({"address": "user", "coin": "market"}))


def pair(prev: pl.DataFrame, cur: pl.DataFrame, fills: pl.DataFrame, start: datetime, end: datetime,
         marks: pl.DataFrame, group: set[str] | None = None) -> dict:
    """Reconcile one snapshot pair. ``prev``/``cur``: user, market, size; ``marks``: market, mark."""
    d = (cur.select(["user", "market", "size"])
         .join(prev.select(["user", "market", pl.col("size").alias("size_prev")]), on=["user", "market"],
               how="full", coalesce=True)
         .with_columns(pl.col("size").fill_null(0.0), pl.col("size_prev").fill_null(0.0))
         .with_columns(delta=pl.col("size") - pl.col("size_prev")))
    j = (d.join(signed_fills(fills, start, end), on=["user", "market"], how="full", coalesce=True)
         .with_columns(pl.col("delta").fill_null(0.0), pl.col("net").fill_null(0.0),
                       pl.col("size").fill_null(0.0), pl.col("size_prev").fill_null(0.0))
         .filter((pl.col("delta") != 0) | (pl.col("net") != 0))
         .join(marks.select(["market", "mark"]), on="market", how="left"))
    tol = pl.max_horizontal(pl.col("size").abs(), pl.col("size_prev").abs()) * REL_TOL + ABS_TOL
    j = j.with_columns(
        ok=(pl.col("delta") - pl.col("net")).abs() <= tol,
        weight=pl.max_horizontal(pl.col("delta").abs(), pl.col("net").abs()) * pl.col("mark").fill_null(0.0))

    def share(frame: pl.DataFrame) -> float:
        total = frame["weight"].sum()
        return float(frame.filter(pl.col("ok"))["weight"].sum() / total) if total else float("nan")

    out = {"n_changes": int(j.filter(pl.col("delta") != 0).height), "n_rows": int(j.height),
           "n_mismatched": int(j.filter(~pl.col("ok")).height), "reconciled_share": share(j),
           "unpriced_rows": int(j.filter(pl.col("mark").is_null()).height)}
    if group is not None:
        in_group = pl.col("user").is_in(sorted(group))
        out["reconciled_share_group"] = share(j.filter(in_group))
        out["reconciled_share_rest"] = share(j.filter(~in_group))
    return out


def maker_taker_balance(fills: pl.DataFrame) -> pl.DataFrame:
    f = fills.filter(pl.col("asset_class") == "perp").with_columns(ntl=pl.col("price") * pl.col("size"))
    g = f.group_by("coin").agg(taker=pl.col("ntl").filter(pl.col("crossed")).sum(),
                               maker=pl.col("ntl").filter(~pl.col("crossed")).sum())
    return (g.with_columns(gap=(pl.col("taker") - pl.col("maker")).abs() / pl.max_horizontal("taker", "maker"))
            .rename({"coin": "market"}).select(["market", "taker", "maker", "gap"]))

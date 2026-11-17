"""Binance (opaque-venue) proxies for the primary panel, computed before each snapshot's cutoff c.

bn_global_ls, bn_top_account_ls, bn_top_position_ls  ln of the global account, top-trader account and
    top-trader position long/short ratios from the last Vision row stamped before c (a row stamped T
    carries information as of T + 5 minutes; a row whose information is over 60 minutes old is missing)
bn_oi_change_7d   ln(open interest before c / open interest before c - 7d), same rows
bn_taker_ls       ln(sum taker buy volume / sum taker sell volume) over 5-minute klines opening in [c - 24h, c)
bn_funding_24h    mean funding rate of the settlements in [c - 24h, c) (secondary)

Licence: Binance Vision data is CC BY-NC-SA 4.0; statistics computed from these proxies are published
in a separate file under that licence.
"""

from __future__ import annotations

from datetime import timedelta

import polars as pl

from herdbench.proxies.native import EPS, period_means

RATIOS = {"bn_global_ls": "count_long_short_ratio", "bn_top_account_ls": "count_toptrader_long_short_ratio",
          "bn_top_position_ls": "sum_toptrader_long_short_ratio"}
VISION_LAG = timedelta(minutes=5)


def _last_rows(metrics: pl.DataFrame, queries: pl.DataFrame, shift: timedelta, max_stale_minutes: int) -> pl.DataFrame:
    cols = ["symbol", "create_time", *RATIOS.values(), "sum_open_interest"]
    m = metrics.select(cols).with_columns(pl.col("create_time").cast(pl.Datetime("us", "UTC"))).sort("create_time")
    q = (queries.select(["market", "symbol", "cutoff"])
         .with_columns(pl.col("cutoff").cast(pl.Datetime("us", "UTC")))
         .with_columns(_t=pl.col("cutoff") - shift - EPS).sort("_t"))
    tolerance = f"{max_stale_minutes + int(VISION_LAG.total_seconds() // 60)}m"
    return q.join_asof(m, left_on="_t", right_on="create_time", by="symbol", strategy="backward", tolerance=tolerance)


def ratio_features(metrics: pl.DataFrame, queries: pl.DataFrame, max_stale_minutes: int = 60) -> pl.DataFrame:
    now = _last_rows(metrics, queries, timedelta(0), max_stale_minutes)
    back = _last_rows(metrics, queries, timedelta(days=7), max_stale_minutes).select(
        ["market", "cutoff", pl.col("sum_open_interest").alias("_oi_back")])
    out = now.join(back, on=["market", "cutoff"], how="left")
    exprs = [pl.when(pl.col(src) > 0).then(pl.col(src).log()).alias(name) for name, src in RATIOS.items()]
    exprs.append(pl.when((pl.col("sum_open_interest") > 0) & (pl.col("_oi_back") > 0))
                 .then((pl.col("sum_open_interest") / pl.col("_oi_back")).log()).alias("bn_oi_change_7d"))
    return out.select(["market", "cutoff", *exprs]).sort(["market", "cutoff"])


def taker_features(klines: pl.DataFrame, queries: pl.DataFrame) -> pl.DataFrame:
    k = klines.rename({"symbol": "market"}).select(["market", "open_time", "taker_buy_volume", "taker_sell_volume"])
    q = queries.select(["market", "symbol", "cutoff"]).with_columns(pl.col("cutoff").cast(pl.Datetime("us", "UTC")))
    windows = q.select([pl.col("symbol").alias("market"), (pl.col("cutoff") - timedelta(hours=24)).alias("start"),
                        pl.col("cutoff").alias("end")])
    sums = period_means(k, "open_time", ["taker_buy_volume", "taker_sell_volume"], windows)
    sums = sums.select([pl.col("market").alias("symbol"), pl.col("end").alias("cutoff"),
                        pl.when((pl.col("taker_buy_volume") > 0) & (pl.col("taker_sell_volume") > 0))
                        .then((pl.col("taker_buy_volume") / pl.col("taker_sell_volume")).log()).alias("bn_taker_ls")])
    return q.join(sums, on=["symbol", "cutoff"], how="left").select(["market", "cutoff", "bn_taker_ls"])


def funding_features(funding: pl.DataFrame, queries: pl.DataFrame) -> pl.DataFrame:
    f = funding.rename({"symbol": "market"}).select(["market", "time", "funding"])
    q = queries.select(["market", "symbol", "cutoff"]).with_columns(pl.col("cutoff").cast(pl.Datetime("us", "UTC")))
    windows = q.select([pl.col("symbol").alias("market"), (pl.col("cutoff") - timedelta(hours=24)).alias("start"),
                        pl.col("cutoff").alias("end")])
    means = period_means(f, "time", ["funding"], windows).select(
        [pl.col("market").alias("symbol"), pl.col("end").alias("cutoff"), pl.col("funding").alias("bn_funding_24h")])
    return q.join(means, on=["symbol", "cutoff"], how="left").select(["market", "cutoff", "bn_funding_24h"])

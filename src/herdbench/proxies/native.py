"""Hyperliquid-native proxies, each computed only from data stamped before its snapshot's cutoff.

The cutoff of a snapshot is the last full UTC hour at or before its capture time (most snapshots are
captured a few minutes after midnight, so the cutoff is D 00:00; 62 were captured later in the day).
Every proxy for that snapshot uses the cutoff, so none can see the positions it is scored against:

funding_24h, funding_7d   mean settled hourly funding in [c - 24h, c) and [c - 7d, c)
premium_24h               mean hourly premium (mark against oracle) in [c - 24h, c); abs_premium_24h = |.|
momentum_7d               ln(P(c) / P(c - 7d)), P(t) = last trade price before t
oi_flow_7d, oi_change_7d  sign(momentum) * ln(OI_{D-1} / OI_{D-8}) and ln(OI_{D-1} / OI_{D-8}), from the
                          previous snapshots (never the snapshot being scored)
liq_intensity_7d          liquidated notional in [c - 7d, c) / open interest at D-1
oi_to_volume_7d           open interest at D-1 / mean daily taker volume in [c - 7d, c)
comovement_30d            correlation of the market's 30 daily returns ending at c with the average of the
                          other markets in its panel
rv_7d                     standard deviation of hourly log returns in [c - 7d, c), scaled to a day (a control)
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone

import numpy as np
import polars as pl

# join_asof with ``by`` cannot verify sortedness itself; every call here sorts its inputs explicitly.
warnings.filterwarnings("ignore", message="Sortedness of columns cannot be checked")

BASELINE_FUNDING = 0.0000125  # Hyperliquid's hourly baseline (0.01% per 8 hours)
EPS = timedelta(microseconds=1)


def cutoff(capture_ms: int) -> datetime:
    t = datetime.fromtimestamp(capture_ms / 1000, tz=timezone.utc)
    return t.replace(minute=0, second=0, microsecond=0)


def _sorted_queries(q: pl.DataFrame, col: str) -> pl.DataFrame:
    return q.with_columns(pl.col(col).cast(pl.Datetime("us", "UTC"))).sort(col)


def price_at(hourly: pl.DataFrame, queries: pl.DataFrame, lookback: str = "24h") -> pl.DataFrame:
    """Last trade price at or before each (market, t): the close of the latest hourly bar ending by t."""
    bars = (
        hourly.select(["market", "hour", "close"])
        .with_columns(end=(pl.col("hour") + pl.duration(hours=1)).cast(pl.Datetime("us", "UTC")))
        .sort("end")
    )
    q = _sorted_queries(queries.select(["market", "t"]), "t")
    out = q.join_asof(
        bars.select(["market", "end", "close"]),
        left_on="t",
        right_on="end",
        by="market",
        strategy="backward",
        tolerance=lookback,
    )
    return out.select(["market", "t", pl.col("close").alias("price")])


def _window_sums(
    events: pl.DataFrame, time_col: str, values: list[str], queries: pl.DataFrame, start_offset: timedelta, name: str
) -> pl.DataFrame:
    """Sum of each value over events with start <= time < cutoff, via cumulative sums and as-of lookups."""
    ev = (
        events.select(["market", time_col, *values])
        .with_columns(pl.col(time_col).cast(pl.Datetime("us", "UTC")), _one=pl.lit(1.0))
        .sort(["market", time_col])
        .with_columns([pl.col(v).cum_sum().over("market").alias(f"_c_{v}") for v in [*values, "_one"]])
        .sort(time_col)
    )
    cums = [f"_c_{v}" for v in [*values, "_one"]]
    bounds = queries.select(["market", "cutoff"]).with_columns(
        pl.col("cutoff").cast(pl.Datetime("us", "UTC")),
        _hi=(pl.col("cutoff") - EPS).cast(pl.Datetime("us", "UTC")),
        _lo=(pl.col("cutoff") - start_offset - EPS).cast(pl.Datetime("us", "UTC")),
    )

    def at(t_col: str) -> pl.DataFrame:
        q = _sorted_queries(bounds.select(["market", "cutoff", t_col]), t_col)
        return (
            q.join_asof(
                ev.select(["market", time_col, *cums]),
                left_on=t_col,
                right_on=time_col,
                by="market",
                strategy="backward",
            )
            .select(["market", "cutoff", *cums])
            .fill_null(0.0)
        )

    hi, lo = at("_hi"), at("_lo")
    out = hi.join(lo, on=["market", "cutoff"], suffix="_lo")
    return out.select(
        [
            "market",
            "cutoff",
            *[(pl.col(f"_c_{v}") - pl.col(f"_c_{v}_lo")).alias(f"{name}_{v}") for v in values],
            (pl.col("_c__one") - pl.col("_c__one_lo")).alias(f"{name}_n"),
        ]
    )


def period_means(events: pl.DataFrame, time_col: str, values: list[str], queries: pl.DataFrame) -> pl.DataFrame:
    """Mean of each value over events with start <= time < end. ``queries``: market, start, end."""
    ev = (
        events.select(["market", time_col, *values])
        .with_columns(pl.col(time_col).cast(pl.Datetime("us", "UTC")), _one=pl.lit(1.0))
        .sort(["market", time_col])
        .with_columns([pl.col(v).cum_sum().over("market").alias(f"_c_{v}") for v in [*values, "_one"]])
        .sort(time_col)
    )
    cums = [f"_c_{v}" for v in [*values, "_one"]]
    q = queries.select(["market", "start", "end"]).with_columns(
        pl.col("start").cast(pl.Datetime("us", "UTC")), pl.col("end").cast(pl.Datetime("us", "UTC"))
    )

    def at(col: str) -> pl.DataFrame:
        qq = q.with_columns(_t=pl.col(col) - EPS).sort("_t")
        return (
            qq.join_asof(
                ev.select(["market", time_col, *cums]),
                left_on="_t",
                right_on=time_col,
                by="market",
                strategy="backward",
            )
            .select(["market", "start", "end", *cums])
            .fill_null(0.0)
        )

    hi, lo = at("end"), at("start")
    out = hi.join(lo, on=["market", "start", "end"], suffix="_lo")
    n = pl.col("_c__one") - pl.col("_c__one_lo")
    return out.select(
        [
            "market",
            "start",
            "end",
            *[pl.when(n > 0).then((pl.col(f"_c_{v}") - pl.col(f"_c_{v}_lo")) / n).alias(v) for v in values],
            n.alias("n"),
        ]
    )


def period_sums(events: pl.DataFrame, time_col: str, values: list[str], queries: pl.DataFrame) -> pl.DataFrame:
    """Sum of each value over events with start <= time < end (zero when there are none)."""
    means = period_means(events, time_col, values, queries)
    return means.select(
        ["market", "start", "end", *[(pl.col(v).fill_null(0.0) * pl.col("n")).alias(v) for v in values], "n"]
    )


def funding_features(funding: pl.DataFrame, queries: pl.DataFrame) -> pl.DataFrame:
    """``funding``: market, time, funding, premium. ``queries``: market, cutoff. Needs half the window's hours."""
    f = funding.with_columns(pinned=(pl.col("funding") == BASELINE_FUNDING).cast(pl.Float64))
    d1 = _window_sums(f, "time", ["funding", "premium", "pinned"], queries, timedelta(hours=24), "d1")
    d7 = _window_sums(f, "time", ["funding", "pinned"], queries, timedelta(days=7), "d7")
    out = d1.join(d7, on=["market", "cutoff"])
    ok1, ok7 = pl.col("d1_n") >= 12, pl.col("d7_n") >= 84
    return out.select(
        [
            "market",
            "cutoff",
            pl.when(ok1).then(pl.col("d1_funding") / pl.col("d1_n")).alias("funding_24h"),
            pl.when(ok7).then(pl.col("d7_funding") / pl.col("d7_n")).alias("funding_7d"),
            pl.when(ok1).then(pl.col("d1_premium") / pl.col("d1_n")).alias("premium_24h"),
            pl.when(ok1).then((pl.col("d1_premium") / pl.col("d1_n")).abs()).alias("abs_premium_24h"),
            pl.when(ok1).then(pl.col("d1_pinned") / pl.col("d1_n")).alias("pinned_share_24h"),
            pl.when(ok7).then(pl.col("d7_pinned") / pl.col("d7_n")).alias("pinned_share_7d"),
        ]
    )


def open_interest_features(markets: pl.DataFrame, dates: Iterable[date]) -> pl.DataFrame:
    """OI from the snapshots one and eight days before each date (the scored snapshot is never used)."""
    m = markets.select(["date", "market", "oi_contracts", "oi_notional"])
    q = pl.DataFrame({"date": list(dates)}).with_columns(
        d1=pl.col("date") - pl.duration(days=1), d8=pl.col("date") - pl.duration(days=8)
    )
    one = q.join(
        m.rename({"date": "d1", "oi_contracts": "oi_c1", "oi_notional": "oi_prev_notional"}), on="d1", how="inner"
    )
    both = one.join(
        m.select([pl.col("date").alias("d8"), "market", pl.col("oi_contracts").alias("oi_c8")]),
        on=["d8", "market"],
        how="left",
    )
    return both.select(
        [
            "date",
            "market",
            pl.col("oi_c1").alias("oi_prev_contracts"),
            "oi_prev_notional",
            pl.when((pl.col("oi_c1") > 0) & (pl.col("oi_c8") > 0))
            .then((pl.col("oi_c1") / pl.col("oi_c8")).log())
            .alias("oi_change_7d"),
        ]
    ).sort(["date", "market"])


def price_features(hourly: pl.DataFrame, queries: pl.DataFrame) -> pl.DataFrame:
    """momentum_7d and rv_7d for each (market, cutoff)."""
    q = queries.select(["market", "cutoff"]).with_columns(pl.col("cutoff").cast(pl.Datetime("us", "UTC")))
    now = price_at(hourly, q.rename({"cutoff": "t"})).rename({"t": "cutoff", "price": "p_now"})
    back = price_at(hourly, q.with_columns(t=pl.col("cutoff") - pl.duration(days=7)).select(["market", "t"]))
    back = back.with_columns(cutoff=pl.col("t") + pl.duration(days=7)).select(
        ["market", "cutoff", pl.col("price").alias("p_back")]
    )
    rets = (
        hourly.select(["market", "hour", "close"])
        .sort(["market", "hour"])
        .with_columns(r=(pl.col("close") / pl.col("close").shift(1).over("market")).log())
        .drop_nulls("r")
        .with_columns(end=pl.col("hour") + pl.duration(hours=1), r2=pl.col("r") ** 2)
    )
    rv = _window_sums(
        rets.rename({"end": "t_end"}),
        "t_end",
        ["r", "r2"],
        q.with_columns(cutoff=pl.col("cutoff") + EPS),
        timedelta(days=7),
        "rv",
    )
    rv = rv.with_columns(cutoff=pl.col("cutoff") - EPS)
    var = (pl.col("rv_r2") - pl.col("rv_r") ** 2 / pl.col("rv_n")) / (pl.col("rv_n") - 1)
    rv = rv.select(
        [
            "market",
            "cutoff",
            pl.when(pl.col("rv_n") >= 84).then((pl.max_horizontal(var, pl.lit(0.0)) * 24).sqrt()).alias("rv_7d"),
        ]
    )
    out = q.join(now, on=["market", "cutoff"], how="left").join(back, on=["market", "cutoff"], how="left")
    out = out.with_columns(
        momentum_7d=pl.when((pl.col("p_now") > 0) & (pl.col("p_back") > 0)).then(
            (pl.col("p_now") / pl.col("p_back")).log()
        )
    )
    return out.join(rv, on=["market", "cutoff"], how="left").select(["market", "cutoff", "momentum_7d", "rv_7d"])


def window_coverage(
    cutoffs: Iterable[datetime], fill_days: Iterable[date], span: timedelta = timedelta(days=7)
) -> pl.DataFrame:
    """Share of [c - span, c) that falls on archived fill days, per cutoff c (1.0 when the window is complete)."""
    have = set(fill_days)
    rows = []
    for c in set(cutoffs):
        start, covered = c - span, timedelta(0)
        d = start.date()
        while datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc) < c:
            lo = max(start, datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc))
            hi = min(c, datetime.combine(d + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc))
            if d in have and hi > lo:
                covered += hi - lo
            d += timedelta(days=1)
        rows.append({"cutoff": c, "coverage": covered / span})
    schema: dict[str, pl.DataType] = {"cutoff": pl.Datetime("us", "UTC"), "coverage": pl.Float64()}
    return pl.DataFrame(rows, schema=schema) if rows else pl.DataFrame(schema=schema)


def flow_features(
    liq_hourly: pl.DataFrame, hourly: pl.DataFrame, queries: pl.DataFrame, fill_days: Iterable[date] | None = None
) -> pl.DataFrame:
    """Liquidations and volume in the 7 days before each cutoff. ``queries``: market, cutoff, oi_prev_notional.

    As registered, the 7-day sums treat a day without archived fills as a day without activity. With
    ``fill_days``, three extra columns describe the window: ``flow_coverage_7d`` (share of the window on archived
    days), ``flows_complete_7d`` (every day archived; used by the complete-window sensitivity check) and
    ``volume_7d_archived_mean`` (mean daily taker volume over the archived part of the window; the denominator
    of the published dtl).
    """
    q = queries.select(["market", "cutoff"])
    bar_window = timedelta(days=7)  # with the 1h shift below: bars labelled c-7d ... c-1h, i.e. inside [c-7d, c)
    liq = _window_sums(
        liq_hourly,
        "hour",
        ["liq_ntl", "liq_long_ntl", "liq_short_ntl"],
        q.with_columns(cutoff=pl.col("cutoff") - timedelta(hours=1) + EPS),
        bar_window,
        "w",
    )
    vol = _window_sums(
        hourly,
        "hour",
        ["volume_ntl"],
        q.with_columns(cutoff=pl.col("cutoff") - timedelta(hours=1) + EPS),
        bar_window,
        "v",
    )
    shift = timedelta(hours=1) - EPS
    liq = liq.with_columns(cutoff=pl.col("cutoff") + shift)
    vol = vol.with_columns(cutoff=pl.col("cutoff") + shift)
    out = (
        queries.with_columns(pl.col("cutoff").cast(pl.Datetime("us", "UTC")))
        .join(liq, on=["market", "cutoff"], how="left")
        .join(vol, on=["market", "cutoff"], how="left")
    )
    mean_vol = pl.col("v_volume_ntl") / 7.0
    out = out.select(
        [
            "market",
            "cutoff",
            pl.when(pl.col("oi_prev_notional") > 0)
            .then(pl.col("w_liq_ntl").fill_null(0.0) / pl.col("oi_prev_notional"))
            .alias("liq_intensity_7d"),
            pl.col("w_liq_long_ntl").fill_null(0.0).alias("liq_long_7d"),
            pl.col("w_liq_short_ntl").fill_null(0.0).alias("liq_short_7d"),
            mean_vol.alias("volume_7d_mean"),
            pl.when(mean_vol > 0).then(pl.col("oi_prev_notional") / mean_vol).alias("oi_to_volume_7d"),
            pl.col("v_volume_ntl").alias("_vol_sum"),
        ]
    )
    if fill_days is None:
        return out.drop("_vol_sum")
    cov = window_coverage(out["cutoff"].unique().to_list(), fill_days)
    return (
        out.join(cov, on="cutoff", how="left")
        .with_columns(
            flow_coverage_7d=pl.col("coverage"),
            flows_complete_7d=pl.col("coverage") >= 1 - 1e-9,
            volume_7d_archived_mean=pl.when(pl.col("coverage") > 0).then(
                pl.col("_vol_sum") / (7.0 * pl.col("coverage"))
            ),
        )
        .drop(["coverage", "_vol_sum"])
    )


def comovement(
    hourly: pl.DataFrame, queries: pl.DataFrame, groups: dict[str, str], window: int = 30, min_returns: int = 20
) -> pl.DataFrame:
    """Correlation of each market's daily returns ending at its cutoff with the mean of its panel's others."""
    q = queries.select(["market", "cutoff"]).filter(pl.col("market").is_in(list(groups)))
    if q.height == 0:
        return pl.DataFrame(
            schema={"market": pl.String, "cutoff": pl.Datetime("us", "UTC"), "comovement_30d": pl.Float64}
        )
    cutoffs = q["cutoff"].unique().to_list()
    group_members: dict[str, list[str]] = {}
    for m, g in groups.items():
        group_members.setdefault(g, []).append(m)
    lookups = pl.DataFrame(
        [
            {"market": m, "cutoff": c, "k": k, "t": c - timedelta(days=k)}
            for c in cutoffs
            for m in groups
            for k in range(window + 1)
        ]
    )
    prices = price_at(hourly, lookups.select(["market", "t"])).unique(["market", "t"])
    pts = lookups.with_columns(
        pl.col("t").cast(pl.Datetime("us", "UTC")), pl.col("cutoff").cast(pl.Datetime("us", "UTC"))
    )
    pts = pts.join(prices, on=["market", "t"], how="left")
    results = []
    for c in cutoffs:
        sub = pts.filter(pl.col("cutoff") == c)
        for _g, members in group_members.items():
            mat = np.full((len(members), window + 1), np.nan)
            idx = {m: i for i, m in enumerate(members)}
            for row in sub.filter(pl.col("market").is_in(members)).iter_rows(named=True):
                if row["price"] is not None and row["price"] > 0:
                    mat[idx[row["market"]], row["k"]] = math.log(row["price"])
            rets = mat[:, :-1] - mat[:, 1:]  # r_k = ln P(c - k d) - ln P(c - (k+1) d)
            ok = ~np.isnan(rets)
            total = np.where(ok, rets, 0.0).sum(axis=0)
            count = ok.sum(axis=0)
            for m, i in idx.items():
                others_n = count - ok[i]
                others = np.where(
                    others_n > 0, (total - np.where(ok[i], rets[i], 0.0)) / np.maximum(others_n, 1), np.nan
                )
                both = ok[i] & ~np.isnan(others)
                rho = float("nan")
                if both.sum() >= min_returns:
                    a, b = rets[i, both], others[both]
                    if a.std() > 0 and b.std() > 0:
                        rho = float(np.corrcoef(a, b)[0, 1])
                results.append({"market": m, "cutoff": c, "comovement_30d": rho})
    out = pl.DataFrame(results).with_columns(
        pl.col("cutoff").cast(pl.Datetime("us", "UTC")), pl.col("comovement_30d").fill_nan(None)
    )
    return q.with_columns(pl.col("cutoff").cast(pl.Datetime("us", "UTC"))).join(
        out, on=["market", "cutoff"], how="left"
    )

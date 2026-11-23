"""Second target: do proxies (and observed positioning) anticipate later liquidations?

The outcome for snapshot date D is liquidated notional in the 7 days after its capture (hourly bars from
the first full hour after the capture), divided by open interest at D; the signed version is
(long - short) / (long + short) liquidated notional. A window that touches a day without archived fills
is dropped. Scoring is cross-sectional (one Spearman correlation per day, averaged with a Newey-West
error), which removes market-wide days in the same way as day fixed effects; each unsigned result is
also reported as a partial correlation controlling for realised volatility.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import polars as pl
from scipy import stats as sps

from herdbench.grading import inference
from herdbench.grading.stats import block_indices, newey_west_se, spearman
from herdbench.proxies.native import period_sums


def _ceil_hour(ms: int) -> datetime:
    t = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    floor = t.replace(minute=0, second=0, microsecond=0)
    return floor if floor == t else floor + timedelta(hours=1)


def forward_liquidations(
    liq_hourly: pl.DataFrame, markets: pl.DataFrame, fill_days: Iterable[date], horizon_days: int
) -> pl.DataFrame:
    have = set(fill_days)
    rows = []
    for r in markets.select(["date", "market", "capture_ms", "oi_notional"]).iter_rows(named=True):
        start = _ceil_hour(r["capture_ms"])
        end = start + timedelta(days=horizon_days)
        days = {
            start.date() + timedelta(days=i)
            for i in range((end - timedelta(microseconds=1)).date().toordinal() - start.date().toordinal() + 1)
        }
        if days <= have:
            rows.append({"date": r["date"], "market": r["market"], "start": start, "end": end, "oi": r["oi_notional"]})
    if not rows:
        return pl.DataFrame(
            schema={"date": pl.Date, "market": pl.String, "fwd_liq_intensity": pl.Float64, "fwd_liq_signed": pl.Float64}
        )
    q = pl.DataFrame(rows)
    sums = period_sums(
        liq_hourly, "hour", ["liq_ntl", "liq_long_ntl", "liq_short_ntl"], q.select(["market", "start", "end"])
    )
    out = q.with_columns(pl.col("start").cast(pl.Datetime("us", "UTC")), pl.col("end").cast(pl.Datetime("us", "UTC")))
    out = out.join(sums, on=["market", "start", "end"], how="left")
    total = pl.col("liq_long_ntl") + pl.col("liq_short_ntl")
    return out.select(
        [
            "date",
            "market",
            pl.when(pl.col("oi") > 0).then(pl.col("liq_ntl").fill_null(0.0) / pl.col("oi")).alias("fwd_liq_intensity"),
            pl.when(total > 0).then((pl.col("liq_long_ntl") - pl.col("liq_short_ntl")) / total).alias("fwd_liq_signed"),
        ]
    ).sort(["date", "market"])


def xs_partial(
    proxy: pd.DataFrame, target: pd.DataFrame, control: pd.DataFrame | None, min_markets: int, lags: int
) -> dict:
    """Daily cross-sectional (partial) Spearman correlation, Fama-MacBeth mean and Newey-West error."""
    cols = proxy.columns.intersection(target.columns)
    if control is not None:
        cols = cols.intersection(control.columns)
    idx = proxy.index.intersection(target.index)
    daily = []
    for d in idx:
        x, y = proxy.loc[d, cols], target.loc[d, cols]
        # a date absent from the control counts as all-missing, so it is skipped rather than scored without it
        z = control.reindex(index=[d], columns=cols).iloc[0] if control is not None else None
        ok = x.notna() & y.notna() & (z.notna() if z is not None else True)
        if ok.sum() < min_markets:
            continue
        r = inference.partial_spearman(x[ok], y[ok], z[ok]) if z is not None else spearman(x[ok], y[ok])
        if not math.isnan(r):
            daily.append((d, r))
    if len(daily) < 3:
        return {"mean": float("nan"), "se": float("nan"), "p": float("nan"), "n_days": len(daily)}
    s = pd.Series([r for _d, r in daily], index=[d for d, _r in daily])
    se = newey_west_se(s.to_numpy(), lags)
    mean = float(s.mean())
    return {
        "mean": mean,
        "se": se,
        "p": float(2 * sps.norm.sf(abs(mean / se))) if se > 0 else float("nan"),
        "n_days": int(s.size),
        "series": s,
    }


def delta_rho(
    fragile: pd.DataFrame,
    proxies: dict[str, pd.DataFrame],
    target: pd.DataFrame,
    min_markets: int,
    lags: int,
    block: int,
    reps: int,
    seed: int,
) -> list[dict]:
    """fragile_share against the best unsigned proxy chosen on one half and scored on the other, both ways.

    On the scoring half both daily cross-sectional correlations use the same markets each day, so their
    difference compares like with like.
    """
    dates = target.index.sort_values()
    half = dates[len(dates) // 2]
    halves = {"first": dates[dates < half], "second": dates[dates >= half]}
    out = []
    for pick, score in (("first", "second"), ("second", "first")):
        chosen = max(
            proxies,
            key=lambda n: np.nan_to_num(
                xs_partial(proxies[n].loc[proxies[n].index.isin(halves[pick])], target, None, min_markets, lags)[
                    "mean"
                ],
                nan=-np.inf,
            ),
        )
        # both daily correlations on the same markets: where fragile_share, the proxy and the target all exist
        idx = fragile.index.intersection(proxies[chosen].index).intersection(target.index)
        idx = idx[idx.isin(halves[score])]
        cols = fragile.columns.intersection(proxies[chosen].columns).intersection(target.columns)
        f_s, p_s = fragile.reindex(index=idx, columns=cols), proxies[chosen].reindex(index=idx, columns=cols)
        both = f_s.notna() & p_s.notna() & target.reindex(index=idx, columns=cols).notna()
        fr = xs_partial(f_s.where(both), target, None, min_markets, lags)
        pr = xs_partial(p_s.where(both), target, None, min_markets, lags)
        if "series" not in fr or "series" not in pr:
            out.append(
                {
                    "picked_on": pick,
                    "scored_on": score,
                    "proxy": chosen,
                    "delta": float("nan"),
                    "ci_low": float("nan"),
                    "ci_high": float("nan"),
                }
            )
            continue
        diff = (fr["series"] - pr["series"]).dropna()
        boots = [float(diff.to_numpy()[rows].mean()) for rows in block_indices(len(diff), block, reps, seed)]
        out.append(
            {
                "picked_on": pick,
                "scored_on": score,
                "proxy": chosen,
                "delta": float(diff.mean()),
                "ci_low": float(np.percentile(boots, 2.5)),
                "ci_high": float(np.percentile(boots, 97.5)),
                "n_days": int(diff.size),
            }
        )
    return out

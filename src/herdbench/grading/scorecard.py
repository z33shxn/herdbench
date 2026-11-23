"""The scorecard: the pre-registered primary family, then every secondary statistic for any proxy-truth pair.

Inputs are long tables: truth cells (date, market, cohort, metric columns) and proxies (date, market,
proxy columns). ``primary_family`` applies common support (a market-day is dropped for every proxy when
any proxy or the truth is missing), picks the bootstrap block length from the data, and returns one
verdict per proxy with its rank interval, plus the half-sample stability check. ``score_pair`` returns
the secondary statistics for a single pairing.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import cast

import numpy as np
import pandas as pd
import polars as pl

from herdbench.config import PREREG, PreRegistration
from herdbench.grading import inference, stats


def wide(df: pl.DataFrame, value: str, markets: Sequence[str] | None = None) -> pd.DataFrame:
    """Date x market frame of ``value`` (dates as a DatetimeIndex, markets sorted)."""
    d = df.select(["date", "market", value])
    if markets is not None:
        d = d.filter(pl.col("market").is_in(list(markets)))
    pdf = d.to_pandas()
    if pdf.empty:
        return pd.DataFrame()
    w = pdf.pivot_table(index="date", columns="market", values=value, aggfunc="first")
    w.index = pd.to_datetime(w.index)
    return w.sort_index().sort_index(axis=1).astype(float)


def boot_p(draws: np.ndarray) -> float:
    d = draws[~np.isnan(draws)]
    if d.size == 0:
        return float("nan")
    below = (np.sum(d <= 0) + 1) / (d.size + 1)
    above = (np.sum(d >= 0) + 1) / (d.size + 1)
    return float(min(1.0, 2 * min(below, above)))


def fe_slope(proxy: pd.DataFrame, truth: pd.DataFrame) -> tuple[float, float]:
    x = cast(pd.Series, proxy.stack(future_stack=True))
    y = cast(pd.Series, truth.reindex(index=proxy.index, columns=proxy.columns).stack(future_stack=True))
    frame = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(frame) < 10 or frame["x"].std() == 0:
        return float("nan"), float("nan")
    g1 = pd.factorize(frame.index.get_level_values(1))[0]
    g2 = pd.factorize(frame.index.get_level_values(0))[0]
    if len(set(g1)) < 2 or len(set(g2)) < 2:
        return float("nan"), float("nan")
    return stats.twoway_fe_cluster(frame["y"].to_numpy(), frame["x"].to_numpy(), g1, g2)


def _align(frames: dict[str, pd.DataFrame], truth: pd.DataFrame) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    idx, cols = truth.index, truth.columns
    for f in frames.values():
        idx, cols = idx.union(f.index), cols.intersection(f.columns)
    idx = idx.sort_values()
    return ({k: f.reindex(index=idx, columns=cols) for k, f in frames.items()}, truth.reindex(index=idx, columns=cols))


def primary_family(
    truth_long: pl.DataFrame,
    proxies_long: pl.DataFrame,
    panel_markets: Sequence[str],
    proxies: Sequence[str],
    prereg: PreRegistration = PREREG,
    truth_name: str = "ni",
    cohort: str = "non_mm",
    expected_sign: int = 1,
) -> dict:
    truth_w = wide(truth_long.filter(pl.col("cohort") == cohort), truth_name, panel_markets)
    frames = {p: wide(proxies_long, p, panel_markets) for p in proxies}
    frames, truth_w = _align(frames, truth_w)
    mask = truth_w.notna()
    for f in frames.values():
        mask &= f.notna()
    keep = [c for c in mask.columns if mask[c].sum() >= prereg.min_market_days]
    mask = mask[keep]
    truth_w = truth_w[keep].where(mask)
    frames = {p: f[keep].where(mask) for p, f in frames.items()}
    used_dates = mask.index[mask.any(axis=1)]
    truth_w, frames = truth_w.loc[used_dates], {p: f.loc[used_dates] for p, f in frames.items()}

    series = [truth_w[c].to_numpy() for c in keep] + [f[c].to_numpy() for f in frames.values() for c in keep]
    block = inference.block_length(series, prereg.bootstrap_block_min, prereg.bootstrap_block_max)
    joint = inference.joint_ts(frames, truth_w, prereg.min_market_days, prereg.bootstrap_reps, block, prereg.seed)

    rows = []
    for p in proxies:
        draws = joint.draws[p]
        rho_draws = np.tanh(draws[~np.isnan(draws)]) if draws.size else np.array([])
        null = inference.rotation_null(
            frames[p],
            truth_w,
            prereg.min_market_days,
            prereg.rotation_draws,
            prereg.rotation_min_shift,
            prereg.seed + 1,
        )
        null = null[~np.isnan(null)]
        beta, se = fe_slope(frames[p], truth_w)
        rows.append(
            {
                "proxy": p,
                "z": joint.z[p],
                "rho": joint.rho[p],
                "n_markets": joint.n_markets[p],
                "n_obs": joint.n_obs[p],
                "ci_low": float(np.percentile(rho_draws, 2.5)) if rho_draws.size else float("nan"),
                "ci_high": float(np.percentile(rho_draws, 97.5)) if rho_draws.size else float("nan"),
                "ci90_low": float(np.percentile(rho_draws, 5)) if rho_draws.size else float("nan"),
                "ci90_high": float(np.percentile(rho_draws, 95)) if rho_draws.size else float("nan"),
                "p_value": boot_p(draws),
                "rot95": float(np.percentile(np.abs(null), 95)) if null.size else float("nan"),
                "fe_beta": beta,
                "fe_se": se,
            }
        )
    table = pd.DataFrame(rows)
    table["p_holm"] = stats.holm(table["p_value"].fillna(1.0).tolist())
    table["verdict"] = [
        inference.verdict(
            r.z,
            r.p_holm,
            r.rot95,
            r.fe_beta,
            (r.ci90_low, r.ci90_high),
            expected_sign,
            prereg.holm_alpha,
            prereg.equivalence_bound,
        )
        for r in table.itertuples()
    ]
    valid = {p: joint.draws[p] for p in proxies if joint.draws[p].size}
    ranks = inference.rank_table(valid, {p: joint.z[p] for p in valid}) if valid else pd.DataFrame()
    if not ranks.empty:
        table = table.merge(ranks[["rank", "rank_lo", "rank_hi"]], left_on="proxy", right_index=True, how="left")
    outranks = pd.DataFrame({a: {b: inference.outranks(valid, a, b) for b in valid} for a in valid})

    half = used_dates[len(used_dates) // 2]
    halves, ses = {}, {}
    first = {p: f.loc[f.index < half] for p, f in frames.items()}
    second = {p: f.loc[f.index >= half] for p, f in frames.items()}
    j1 = inference.joint_ts(
        first,
        truth_w.loc[truth_w.index < half],
        prereg.min_market_days,
        prereg.bootstrap_reps,
        block,
        prereg.seed + 2,
    )
    j2 = inference.joint_ts(
        second,
        truth_w.loc[truth_w.index >= half],
        prereg.min_market_days,
        prereg.bootstrap_reps,
        block,
        prereg.seed + 3,
    )
    for p in proxies:
        halves[p] = (j1.z[p], j2.z[p])
        ses[p] = (
            float(np.nanstd(j1.draws[p])) if j1.draws[p].size else float("nan"),
            float(np.nanstd(j2.draws[p])) if j2.draws[p].size else float("nan"),
        )
    stab = inference.stability(
        halves,
        ses,
        dict(zip(table["proxy"], table["verdict"], strict=True)),
        prereg.stability_tau_threshold,
        prereg.stability_min_verdicts,
    )
    return {
        "table": table,
        "outranks": outranks,
        "block_length": block,
        "n_common_days": len(used_dates),
        "markets": keep,
        "stability": stab,
        "halves": halves,
        "half_split": str(half.date()),
        "support": mask.loc[used_dates],
    }


def support_table(mask: pd.DataFrame) -> pl.DataFrame:
    """The primary family's common support as (date, market) rows, to score other proxies on the same days."""
    long = mask.stack(future_stack=True)
    long = long[long.astype(bool)]
    dates = [d.date() for d in long.index.get_level_values(0)]
    markets = [str(m) for m in long.index.get_level_values(1)]
    return pl.DataFrame({"date": dates, "market": markets}, schema={"date": pl.Date, "market": pl.String})


def _shift(truth: pd.DataFrame, days: int) -> pd.DataFrame:
    shifted = truth.copy()
    shifted.index = shifted.index + pd.Timedelta(days=days)
    return shifted.reindex(truth.index)


def _changes(frame: pd.DataFrame) -> pd.DataFrame:
    consecutive = pd.Series(frame.index, index=frame.index).diff().eq(pd.Timedelta(days=1))
    return frame.diff().where(consecutive, axis=0)


def score_pair(
    proxy: pd.DataFrame,
    truth: pd.DataFrame,
    oi: pd.DataFrame | None = None,
    prereg: PreRegistration = PREREG,
    signed: bool = True,
    expected_sign: int = 1,
    seed_offset: int = 0,
) -> dict:
    """All statistics for one proxy-truth pairing (wide frames)."""
    frames, t = _align({"p": proxy}, truth)
    p = frames["p"]
    series = [t[c].dropna().to_numpy() for c in t.columns] + [p[c].dropna().to_numpy() for c in p.columns]
    block = inference.block_length(series, prereg.bootstrap_block_min, prereg.bootstrap_block_max)
    main = stats.ts_statistic(p, t, prereg.min_market_days, prereg.bootstrap_reps, block, prereg.seed + seed_offset)
    z = math.atanh(max(-0.999999, min(0.999999, main.value))) if not math.isnan(main.value) else float("nan")
    ch = stats.ts_statistic(_changes(p), _changes(t), prereg.min_market_days)
    xs = stats.xs_statistic(p, t, prereg.min_xs_markets, prereg.nw_lags)
    plus = stats.ts_statistic(p, _shift(t, prereg.placebo_shift_days), prereg.min_market_days).value
    minus = stats.ts_statistic(p, _shift(t, -prereg.placebo_shift_days), prereg.min_market_days).value
    placebo = stats.fisher_mean([v for v in (plus, minus) if not math.isnan(v)])
    oi_rho = float("nan")
    if oi is not None and main.n_markets:
        used = main.per_market.dropna()
        weights = oi.reindex(columns=used.index).mean()
        zs = np.arctanh(np.clip(used.to_numpy(), -0.999999, 0.999999))
        w = weights.to_numpy()
        if np.nansum(w) > 0:
            oi_rho = float(np.tanh(np.nansum(zs * w) / np.nansum(w)))
    beta, se = fe_slope(p, t)
    row = {
        "z": z,
        "rho": main.value,
        "ci_low": main.ci_low,
        "ci_high": main.ci_high,
        "p_value": main.p_value,
        "n_markets": main.n_markets,
        "n_obs": main.n_obs,
        "block_length": block,
        "changes_rho": ch.value,
        "xs_rho": xs.value,
        "xs_se": xs.se,
        "xs_p": xs.p_value,
        "xs_days": xs.n_days,
        "oi_weighted_rho": oi_rho,
        "placebo_rho": placebo,
        "fe_beta": beta,
        "fe_se": se,
        "expected_sign": expected_sign,
        "sign_agreement": float("nan"),
        "sign_low": float("nan"),
        "sign_high": float("nan"),
        "tail_lift": float("nan"),
    }
    if signed:
        both = pd.concat(
            [p.stack(future_stack=True).rename("p"), t.stack(future_stack=True).rename("t")], axis=1
        ).dropna()
        both = both[(both["p"] != 0) & (both["t"] != 0)]
        if len(both):
            agree = int((np.sign(both["p"]) == np.sign(both["t"]) * expected_sign).sum())
            lo, hi = stats.wilson(agree, len(both))
            row.update(sign_agreement=agree / len(both), sign_low=lo, sign_high=hi)
        row["tail_lift"] = inference.tail_lift(
            p * expected_sign, t, prereg.tail_proxy_fraction, prereg.tail_truth_fraction
        )
    return row

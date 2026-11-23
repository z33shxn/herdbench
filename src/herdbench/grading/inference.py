"""Inference behind each proxy's verdict.

* ``joint_ts``: within-market Spearman correlation over time, averaged on Fisher's z scale across markets,
  with a circular block bootstrap whose date draws are shared by every proxy (so ranks and pairwise
  comparisons come from the same draws).
* ``politis_white_block`` / ``block_length``: automatic block length (Politis and White 2004, with the
  Patton, Politis and White 2009 correction), median across the panel's series, clipped to [20, 40].
* ``rotation_null``: shift every market's truth by the same random number of dates (keeping each
  series' own persistence and the cross-market structure) to get a null for z.
* ``verdict``: confirmed, reversed, uninformative (90% interval inside the equivalence bound) or
  inconclusive.
* ``rank_table`` / ``outranks``: ranking by |z| with bootstrap rank intervals and dominance probabilities.
* ``stability``: kill criterion 4, which applies to ranking claims only.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import stats as sps

from herdbench.grading.stats import _column_spearman, block_indices


def politis_white_block(x: np.ndarray) -> float:
    """Optimal block length for the circular bootstrap of one series."""
    v = np.asarray(x, dtype=float)
    n = v.size
    e = v - v.mean()
    b_max = math.ceil(min(3 * math.sqrt(n), n / 3))
    kn = max(5, int(math.log10(n)))
    m_max = math.ceil(math.sqrt(n)) + kn
    band = 2 * math.sqrt(math.log10(n) / n)
    acov = np.zeros(m_max + 1)
    acorr = np.zeros(m_max + 1)
    first: int | None = None
    for lag in range(m_max + 1):
        cross = float(e[lag:] @ e[: n - lag])
        acov[lag] = cross / n
        tail, head = e[lag + 1 :], e[: -(lag + 1)]
        acorr[lag] = abs(cross) / math.sqrt(float(tail @ tail) * float(head @ head))
        if lag >= kn and first is None and np.all(acorr[lag - kn : lag] < band):
            first = lag - kn
    m = min(2 * max(first, 1), m_max) if first is not None else m_max
    g = 0.0
    long_run = acov[0]
    for k in range(1, m + 1):
        w = 1.0 if k / m <= 0.5 else 2 * (1 - k / m)
        g += 2 * w * k * acov[k]
        long_run += 2 * w * acov[k]
    b = (2 * g * g / (4 / 3 * long_run * long_run)) ** (1 / 3) * n ** (1 / 3)
    return float(min(b, b_max))


def block_length(series: Iterable[np.ndarray], lo: int, hi: int) -> int:
    blocks = []
    for s in series:
        v = np.asarray(s, dtype=float)
        v = v[~np.isnan(v)]
        if v.size >= 11 and np.std(v) > 0:
            blocks.append(politis_white_block(v))
    if not blocks:
        return lo
    return int(min(hi, max(lo, round(float(np.median(blocks))))))


def _master(proxies: dict[str, pd.DataFrame], truth: pd.DataFrame) -> tuple[pd.Index, pd.Index]:
    idx = truth.index
    for p in proxies.values():
        idx = idx.union(p.index)
    return idx.sort_values(), truth.columns


def _arrays(p: pd.DataFrame, truth: pd.DataFrame, idx: pd.Index, cols: pd.Index) -> tuple[np.ndarray, np.ndarray]:
    use = cols.intersection(p.columns)
    x = p.reindex(index=idx, columns=use).to_numpy(dtype=float)
    y = truth.reindex(index=idx, columns=use).to_numpy(dtype=float)
    both = ~(np.isnan(x) | np.isnan(y))
    return np.where(both, x, np.nan), np.where(both, y, np.nan)


def _zmean(rho: np.ndarray) -> float:
    r = rho[~np.isnan(rho)]
    return float(np.mean(np.arctanh(np.clip(r, -0.999999, 0.999999)))) if r.size else float("nan")


@dataclass
class JointResult:
    z: dict[str, float]
    rho: dict[str, float]
    draws: dict[str, np.ndarray] = field(repr=False)
    n_markets: dict[str, int]
    n_obs: dict[str, int]
    per_market: dict[str, pd.Series] = field(repr=False)


def joint_ts(
    proxies: dict[str, pd.DataFrame], truth: pd.DataFrame, min_days: int, reps: int, block: int, seed: int
) -> JointResult:
    idx, cols = _master(proxies, truth)
    rows = block_indices(len(idx), block, reps, seed) if reps else None
    out = JointResult({}, {}, {}, {}, {}, {})
    for name, p in proxies.items():
        x, y = _arrays(p, truth, idx, cols)
        rho, n = _column_spearman(x, y)
        use = (n >= min_days) & ~np.isnan(rho)
        mk = cols.intersection(p.columns)
        out.per_market[name] = pd.Series(np.where(use, rho, np.nan), index=mk)
        out.z[name] = _zmean(rho[use])
        out.rho[name] = float(np.tanh(out.z[name])) if not math.isnan(out.z[name]) else float("nan")
        out.n_markets[name] = int(use.sum())
        out.n_obs[name] = int(n[use].sum())
        if rows is not None and use.any():
            xs, ys = x[:, use], y[:, use]
            out.draws[name] = np.array([_zmean(_column_spearman(xs[r], ys[r])[0]) for r in rows])
        else:
            out.draws[name] = np.array([])
    return out


def rotation_null(
    proxy: pd.DataFrame, truth: pd.DataFrame, min_days: int, draws: int, min_shift: int, seed: int
) -> np.ndarray:
    idx, cols = _master({"p": proxy}, truth)
    x_raw = proxy.reindex(index=idx, columns=cols.intersection(proxy.columns)).to_numpy(dtype=float)
    y_raw = truth.reindex(index=idx, columns=cols.intersection(proxy.columns)).to_numpy(dtype=float)
    T = len(idx)
    rng = np.random.default_rng(seed)
    out = np.empty(draws)
    for d in range(draws):
        k = int(rng.integers(min_shift, max(min_shift + 1, T - min_shift)))
        ys = np.roll(y_raw, k, axis=0)
        both = ~(np.isnan(x_raw) | np.isnan(ys))
        rho, n = _column_spearman(np.where(both, x_raw, np.nan), np.where(both, ys, np.nan))
        out[d] = _zmean(rho[(n >= min_days) & ~np.isnan(rho)])
    return out


def verdict(
    z: float,
    p_holm: float,
    rot95: float,
    fe_beta: float,
    ci90: tuple[float, float],
    expected_sign: int,
    alpha: float,
    bound: float,
) -> str:
    """confirmed / reversed / uninformative / inconclusive (see the pre-registration, section 7)."""
    if not any(math.isnan(v) for v in (z, p_holm, rot95, fe_beta)):
        strong = p_holm < alpha and abs(z) > rot95 and np.sign(fe_beta) == np.sign(z) and z != 0
        if strong and np.sign(z) == expected_sign:
            return "confirmed"
        if strong and np.sign(z) == -expected_sign:
            return "reversed"
    lo, hi = ci90
    if not (math.isnan(lo) or math.isnan(hi)) and -bound < lo and hi < bound:
        return "uninformative"
    return "inconclusive"


def rank_table(draws: dict[str, np.ndarray], z: dict[str, float]) -> pd.DataFrame:
    names = list(z)
    order = sorted(names, key=lambda n: -abs(z[n]))
    mat = np.vstack([np.abs(draws[n]) for n in names]).T  # draws x proxies
    ranks = (-mat).argsort(axis=1).argsort(axis=1) + 1
    lo = np.percentile(ranks, 2.5, axis=0)
    hi = np.percentile(ranks, 97.5, axis=0)
    table = pd.DataFrame(
        {
            "z": [z[n] for n in names],
            "abs_z": [abs(z[n]) for n in names],
            "rank_lo": np.floor(lo).astype(int),
            "rank_hi": np.ceil(hi).astype(int),
        },
        index=names,
    )
    table["rank"] = [order.index(n) + 1 for n in names]
    return table.sort_values("rank")


def outranks(draws: dict[str, np.ndarray], a: str, b: str) -> float:
    return float(np.mean(np.abs(draws[a]) > np.abs(draws[b])))


def stability(
    halves: dict[str, tuple[float, float]],
    ses: dict[str, tuple[float, float]],
    verdicts: dict[str, str],
    tau_threshold: float,
    min_verdicts: int,
) -> dict:
    decisive = [n for n, v in verdicts.items() if v in ("confirmed", "reversed") and n in halves]
    unstable = []
    for n in decisive:
        z1, z2 = halves[n]
        s1, s2 = ses[n]
        if np.sign(z1) != np.sign(z2) or abs(z1 - z2) >= 1.96 * math.sqrt(s1 * s1 + s2 * s2):
            unstable.append(n)
    tau = float("nan")
    if len(decisive) >= min_verdicts:
        first = [halves[n][0] for n in decisive]
        second = [halves[n][1] for n in decisive]
        tau = float(sps.kendalltau(first, second).statistic)
    claimed = len(decisive) >= min_verdicts and not math.isnan(tau) and tau >= tau_threshold
    return {
        "decisive": decisive,
        "tau": tau,
        "ranking_claimed": bool(claimed),
        "unstable": unstable,
        "passed": None if len(decisive) < min_verdicts else bool(tau >= tau_threshold),
    }


def tail_lift(proxy: pd.DataFrame, truth: pd.DataFrame, proxy_tail: float, truth_tail: float) -> float:
    """P(truth in its same-direction extreme fraction | proxy in its extreme fraction), divided by the null."""
    hits = events = 0
    for col in proxy.columns.intersection(truth.columns):
        pair = pd.concat([proxy[col], truth[col]], axis=1, keys=["p", "t"]).dropna()
        if len(pair) < 20:
            continue
        p_hi, p_lo = pair["p"].quantile(1 - proxy_tail), pair["p"].quantile(proxy_tail)
        t_hi, t_lo = pair["t"].quantile(1 - truth_tail), pair["t"].quantile(truth_tail)
        up, down = pair[pair["p"] >= p_hi], pair[pair["p"] <= p_lo]
        hits += int((up["t"] >= t_hi).sum() + (down["t"] <= t_lo).sum())
        events += len(up) + len(down)
    return float(hits / events / truth_tail) if events else float("nan")


def partial_spearman(x, y, z) -> float:
    """Spearman correlation of x and y after removing the rank-linear effect of z from both."""
    frame = pd.DataFrame({"x": x, "y": y, "z": z}).dropna()
    if len(frame) < 5:
        return float("nan")
    r = frame.rank()
    design = np.column_stack([np.ones(len(r)), r["z"].to_numpy()])

    def resid(v: np.ndarray) -> np.ndarray:
        beta, *_ = np.linalg.lstsq(design, v, rcond=None)
        return v - design @ beta

    a, b = resid(r["x"].to_numpy()), resid(r["y"].to_numpy())
    if a.std() == 0 or b.std() == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])

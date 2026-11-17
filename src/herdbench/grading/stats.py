"""Statistics for grading a proxy against observed positioning.

Panels are wide pandas frames: rows are snapshot dates, columns are markets. Every function treats a pair
(date, market) as usable only when both the proxy and the truth are present there.

* ``ts_statistic``: within-market Spearman correlation over time, Fisher-z averaged across markets, with a
  circular block bootstrap over dates (each replicate re-ranks its resampled rows, so ties count properly).
* ``xs_statistic``: daily cross-sectional Spearman correlation, Fama-MacBeth mean, Newey-West error.
* ``twoway_fe_cluster`` / ``ols_cluster``: slopes with cluster-robust errors, matching statsmodels.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import stats as sps

Z95 = 1.959963984540054


def spearman(x: Sequence[float], y: Sequence[float]) -> float:
    a = np.asarray(x, dtype=float)
    b = np.asarray(y, dtype=float)
    ok = ~(np.isnan(a) | np.isnan(b))
    a, b = a[ok], b[ok]
    if a.size < 3 or np.all(a == a[0]) or np.all(b == b[0]):
        return float("nan")
    return float(sps.spearmanr(a, b).statistic)


def fisher_mean(rhos: Sequence[float]) -> float:
    r = np.asarray([v for v in rhos if not math.isnan(v)], dtype=float)
    if r.size == 0:
        return float("nan")
    return float(np.tanh(np.mean(np.arctanh(np.clip(r, -0.999999, 0.999999)))))


def _aligned(proxy: pd.DataFrame, truth: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, pd.Index, pd.Index]:
    cols = proxy.columns.intersection(truth.columns)
    idx = proxy.index.union(truth.index).sort_values()
    x = proxy.reindex(index=idx, columns=cols).to_numpy(dtype=float)
    y = truth.reindex(index=idx, columns=cols).to_numpy(dtype=float)
    both = ~(np.isnan(x) | np.isnan(y))
    x = np.where(both, x, np.nan)
    y = np.where(both, y, np.nan)
    return x, y, idx, cols


def _column_spearman(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Spearman correlation of each column pair (NaNs already aligned); returns (rho, n)."""
    rx = sps.rankdata(x, axis=0, nan_policy="omit")
    ry = sps.rankdata(y, axis=0, nan_policy="omit")
    n = np.sum(~np.isnan(rx), axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        mx = np.nanmean(rx, axis=0)
        my = np.nanmean(ry, axis=0)
        dx, dy = rx - mx, ry - my
        cov = np.nansum(dx * dy, axis=0)
        vx = np.nansum(dx * dx, axis=0)
        vy = np.nansum(dy * dy, axis=0)
        r = cov / np.sqrt(vx * vy)
    r[(vx == 0) | (vy == 0)] = np.nan
    return r, n


@dataclass(frozen=True)
class TsResult:
    value: float
    ci_low: float
    ci_high: float
    p_value: float
    n_markets: int
    n_obs: int
    per_market: pd.Series = field(repr=False)

    @property
    def status(self) -> str:
        return "ok" if self.n_markets > 0 and not math.isnan(self.value) else "not_computable"


def block_indices(n_rows: int, block: int, reps: int, seed: int) -> np.ndarray:
    """Circular block bootstrap row indices, shape (reps, n_rows)."""
    rng = np.random.default_rng(seed)
    n_blocks = math.ceil(n_rows / block)
    starts = rng.integers(0, n_rows, size=(reps, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % n_rows
    return idx.reshape(reps, n_blocks * block)[:, :n_rows]


def ts_statistic(proxy: pd.DataFrame, truth: pd.DataFrame, min_days: int, bootstrap_reps: int = 0,
                 block: int = 20, seed: int = 0) -> TsResult:
    x, y, _idx, cols = _aligned(proxy, truth)
    rho, n = _column_spearman(x, y)
    use = (n >= min_days) & ~np.isnan(rho)
    per_market = pd.Series(np.where(use, rho, np.nan), index=cols, dtype=float)
    value = fisher_mean(rho[use].tolist())
    lo = hi = p = float("nan")
    if bootstrap_reps and use.any():
        xs, ys = x[:, use], y[:, use]
        boot = np.empty(bootstrap_reps)
        for r, rows in enumerate(block_indices(xs.shape[0], block, bootstrap_reps, seed)):
            rb, _ = _column_spearman(xs[rows], ys[rows])
            boot[r] = fisher_mean(rb.tolist())
        boot = boot[~np.isnan(boot)]
        lo, hi = (float(v) for v in np.percentile(boot, [2.5, 97.5]))
        below = (np.sum(boot <= 0) + 1) / (boot.size + 1)
        above = (np.sum(boot >= 0) + 1) / (boot.size + 1)
        p = float(min(1.0, 2 * min(below, above)))
    return TsResult(value, lo, hi, p, int(use.sum()), int(n[use].sum()), per_market)


@dataclass(frozen=True)
class XsResult:
    value: float
    se: float
    p_value: float
    n_days: int
    series: pd.Series = field(repr=False)


def xs_statistic(proxy: pd.DataFrame, truth: pd.DataFrame, min_markets: int, lags: int) -> XsResult:
    x, y, idx, _cols = _aligned(proxy, truth)
    daily = np.full(len(idx), np.nan)
    for i in range(len(idx)):
        ok = ~np.isnan(x[i])
        if ok.sum() >= min_markets:
            daily[i] = spearman(x[i, ok], y[i, ok])
    series = pd.Series(daily, index=idx).dropna()
    if series.size < 3:
        return XsResult(float("nan"), float("nan"), float("nan"), int(series.size), series)
    se = newey_west_se(series.to_numpy(), lags)
    value = float(series.mean())
    p = float(2 * sps.norm.sf(abs(value / se))) if se > 0 else float("nan")
    return XsResult(value, se, p, int(series.size), series)


def newey_west_se(x: Sequence[float], lags: int) -> float:
    """Standard error of the mean with a Bartlett kernel; equals statsmodels' OLS HAC on a constant."""
    v = np.asarray(x, dtype=float)
    u = v - v.mean()
    T = u.size
    s = float(u @ u)
    for lag in range(1, min(lags, T - 1) + 1):
        s += 2 * (1 - lag / (lags + 1)) * float(u[lag:] @ u[:-lag])
    return math.sqrt(s / (T * T))  # statsmodels applies no small-sample factor to HAC


def _cluster_var(xu: np.ndarray, groups: np.ndarray, sxx: float, nobs: int, k: int = 1) -> float:
    sums = pd.Series(xu).groupby(groups).sum().to_numpy()
    g = sums.size
    return float(sums @ sums) / sxx ** 2 * (g / (g - 1)) * ((nobs - 1) / (nobs - k))


def ols_cluster(y: Sequence[float], x: Sequence[float], groups: Sequence) -> tuple[float, float]:
    """Slope through the origin with a one-way cluster-robust (CR1) standard error."""
    yv, xv = np.asarray(y, float), np.asarray(x, float)
    sxx = float(xv @ xv)
    beta = float(xv @ yv) / sxx
    u = yv - beta * xv
    return beta, math.sqrt(_cluster_var(xv * u, np.asarray(groups), sxx, xv.size))


def twoway_demean(v: np.ndarray, g1: np.ndarray, g2: np.ndarray, tol: float = 1e-13, max_iter: int = 100_000) -> np.ndarray:
    """Remove both sets of group means by alternating projections (exact for unbalanced panels at convergence)."""
    out = np.asarray(v, float).copy()
    s1, s2 = pd.Series(g1), pd.Series(g2)
    for _ in range(max_iter):
        prev = out.copy()
        out = out - pd.Series(out).groupby(s1).transform("mean").to_numpy()
        out = out - pd.Series(out).groupby(s2).transform("mean").to_numpy()
        if np.max(np.abs(out - prev)) < tol:
            break
    return out


def twoway_fe_cluster(y, x, g1, g2, return_demeaned: bool = False):
    """Two-way fixed-effects slope with two-way clustered errors (Cameron, Gelbach and Miller), as statsmodels."""
    g1, g2 = np.asarray(g1), np.asarray(g2)
    yd, xd = twoway_demean(np.asarray(y, float), g1, g2), twoway_demean(np.asarray(x, float), g1, g2)
    sxx = float(xd @ xd)
    beta = float(xd @ yd) / sxx
    xu = xd * (yd - beta * xd)
    inter = pd.factorize(pd.MultiIndex.from_arrays([g1, g2]))[0]
    var = (_cluster_var(xu, g1, sxx, xd.size) + _cluster_var(xu, g2, sxx, xd.size)
           - _cluster_var(xu, inter, sxx, xd.size))
    se = math.sqrt(var) if var > 0 else float("nan")
    return (beta, se, xd, yd) if return_demeaned else (beta, se)


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    centre = (k + z * z / 2) / (n + z * z)
    half = z * math.sqrt(k * (n - k) / n + z * z / 4) / (n + z * z)
    return (centre - half, centre + half)


def holm(pvals: Sequence[float]) -> list[float]:
    p = np.asarray(pvals, float)
    order = np.argsort(p)
    m = p.size
    adj = np.empty(m)
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * p[i]))
        adj[i] = running
    return adj.tolist()


def benjamini_hochberg(pvals: Sequence[float], q: float) -> list[bool]:
    p = np.asarray(pvals, float)
    m = p.size
    order = np.argsort(p)
    passed = [i for rank, i in enumerate(order, start=1) if p[i] <= rank / m * q]
    if not passed:
        return [False] * m
    cutoff = max(p[i] for i in passed)
    return [bool(v <= cutoff) for v in p]


def kendall_tau(ranking_a: Sequence[str], ranking_b: Sequence[str]) -> float:
    common = [name for name in ranking_a if name in set(ranking_b)]
    pos_b = {name: i for i, name in enumerate(ranking_b)}
    return float(sps.kendalltau(list(range(len(common))), [pos_b[name] for name in common]).statistic)

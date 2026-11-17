"""Kill criterion 3: clustered power of the stock transfer panel, simulated on its actual shape.

Each draw builds a stock x period panel with AR(1) persistence within stock, common period shocks and
stock effects in both the proxy and the truth, keeps only the cells the real panel has, removes stock
and period fixed effects, and tests the slope with standard errors clustered by stock (t with G - 1
degrees of freedom). Power is the share of draws that reject at the 5% level.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import stats as sps


def _demean(v: np.ndarray, g1: np.ndarray, g2: np.ndarray, tol: float = 1e-12, max_iter: int = 10_000) -> np.ndarray:
    n1, n2 = np.bincount(g1), np.bincount(g2)
    out = v.copy()
    for _ in range(max_iter):
        prev = out
        out = out - (np.bincount(g1, out) / np.maximum(n1, 1))[g1]
        out = out - (np.bincount(g2, out) / np.maximum(n2, 1))[g2]
        if np.max(np.abs(out - prev)) < tol:
            break
    return out


def clustered_power(mask: np.ndarray, rho: float, ar: float, reps: int, seed: int, alpha: float = 0.05) -> float:
    """``mask``: boolean array (stocks x periods) marking the cells the real panel contains."""
    mask = np.asarray(mask, dtype=bool)
    keep_rows = mask.any(axis=1)
    mask = mask[keep_rows]
    n_stock, n_per = mask.shape
    stock_idx, per_idx = np.nonzero(mask)
    _, g1 = np.unique(stock_idx, return_inverse=True)
    _, g2 = np.unique(per_idx, return_inverse=True)
    G = int(g1.max()) + 1
    crit = sps.t.ppf(1 - alpha / 2, G - 1)
    rng = np.random.default_rng(seed)

    def ar1() -> np.ndarray:
        z = rng.normal(size=(n_stock, n_per))
        for t in range(1, n_per):
            z[:, t] = ar * z[:, t - 1] + math.sqrt(1 - ar * ar) * z[:, t]
        return z

    hits = 0
    for _ in range(reps):
        x = ar1() + rng.normal(size=(1, n_per)) + rng.normal(size=(n_stock, 1))
        e = ar1() + rng.normal(size=(1, n_per)) + rng.normal(size=(n_stock, 1))
        y = rho * x + math.sqrt(1 - rho * rho) * e
        xd = _demean(x[stock_idx, per_idx], g1, g2)
        yd = _demean(y[stock_idx, per_idx], g1, g2)
        sxx = float(xd @ xd)
        beta = float(xd @ yd) / sxx
        score = np.bincount(g1, xd * (yd - beta * xd))
        nobs = xd.size
        var = float(score @ score) / sxx ** 2 * (G / (G - 1)) * ((nobs - 1) / (nobs - 1))
        hits += abs(beta / math.sqrt(var)) > crit
    return hits / reps

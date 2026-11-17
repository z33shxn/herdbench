import math

import numpy as np
import pandas as pd
import pytest
import statsmodels.api as sm
from scipy import stats as sps

from herdbench.grading import stats


def test_spearman_handles_monotone_reversed_missing_and_constant():
    assert stats.spearman([1, 2, 3, 4, 5], [2, 4, 6, 8, 10]) == pytest.approx(1.0)
    assert stats.spearman([1, 2, 3, 4, 5], [9, 7, 5, 3, 1]) == pytest.approx(-1.0)
    assert stats.spearman([1, 2, np.nan, 4, 5], [1, 2, 99, 4, 5]) == pytest.approx(1.0)  # pair with NaN dropped
    assert math.isnan(stats.spearman([1, 1, 1, 1], [1, 2, 3, 4]))
    x, y = [3.1, 1.2, 5.5, 2.2, 4.0, 0.3], [2.0, 1.0, 3.5, 3.0, 2.5, 0.1]
    assert stats.spearman(x, y) == pytest.approx(sps.spearmanr(x, y).statistic)


def test_fisher_mean_averages_on_the_z_scale():
    assert stats.fisher_mean([0.5, 0.5]) == pytest.approx(0.5)
    assert stats.fisher_mean([0.9, -0.9]) == pytest.approx(0.0)
    assert stats.fisher_mean([0.2, 0.6]) == pytest.approx(math.tanh((math.atanh(0.2) + math.atanh(0.6)) / 2))
    assert math.isnan(stats.fisher_mean([]))


def panel(T, M, rho, ar=0.0, seed=0):
    """Wide date x market frames whose within-market correlation is rho."""
    rng = np.random.default_rng(seed)
    def ar1(shape):
        z = rng.normal(size=shape)
        for t in range(1, shape[0]):
            z[t] = ar * z[t - 1] + math.sqrt(1 - ar ** 2) * z[t]
        return z
    x = ar1((T, M))
    y = rho * x + math.sqrt(1 - rho ** 2) * ar1((T, M))
    idx = pd.date_range("2025-08-28", periods=T, freq="D")
    cols = [f"m{i}" for i in range(M)]
    return pd.DataFrame(x, idx, cols), pd.DataFrame(y, idx, cols)


def test_time_series_statistic_recovers_a_planted_ranking():
    truth = None
    rhos = {}
    for name, rel, seed in (("good", 0.7, 1), ("mid", 0.5, 2), ("weak", 0.3, 3)):
        p, t = panel(400, 5, rel, seed=seed)
        truth = t if truth is None else truth
        rhos[name] = stats.ts_statistic(p, t, min_days=60).value
    assert rhos["good"] > rhos["mid"] > rhos["weak"] > 0


def test_time_series_statistic_skips_markets_with_too_few_days_or_no_variation():
    p, t = panel(100, 3, 0.6, seed=4)
    p.iloc[50:, 1] = np.nan          # market m1 keeps only 50 days
    p.iloc[:, 2] = 1.0               # market m2 never varies
    res = stats.ts_statistic(p, t, min_days=60)
    assert res.n_markets == 1
    assert list(res.per_market.dropna().index) == ["m0"]


def test_block_bootstrap_interval_has_honest_coverage_under_autocorrelation():
    covered = 0
    sims = 40
    for s in range(sims):
        p, t = panel(300, 6, 0.4, ar=0.5, seed=100 + s)
        res = stats.ts_statistic(p, t, min_days=60, bootstrap_reps=300, block=20, seed=s)
        covered += res.ci_low <= 0.4 <= res.ci_high
    assert covered >= 0.85 * sims


def test_bootstrap_p_value_is_small_for_a_strong_effect_and_large_for_none():
    p, t = panel(300, 6, 0.5, seed=7)
    assert stats.ts_statistic(p, t, min_days=60, bootstrap_reps=400, block=20, seed=1).p_value < 0.01
    p0, t0 = panel(300, 6, 0.0, seed=8)
    assert stats.ts_statistic(p0, t0, min_days=60, bootstrap_reps=400, block=20, seed=1).p_value > 0.05


def test_newey_west_matches_statsmodels_hac():
    rng = np.random.default_rng(3)
    e = rng.normal(size=500)
    x = np.empty(500)
    x[0] = e[0]
    for i in range(1, 500):
        x[i] = 0.6 * x[i - 1] + e[i]
    ref = sm.OLS(x, np.ones_like(x)).fit(cov_type="HAC", cov_kwds={"maxlags": 10}).bse[0]
    assert stats.newey_west_se(x, lags=10) == pytest.approx(ref, rel=1e-10)


def test_twoway_cluster_matches_statsmodels_on_the_demeaned_regression():
    rng = np.random.default_rng(11)
    n_m, n_t = 12, 40
    g1 = np.repeat(np.arange(n_m), n_t)
    g2 = np.tile(np.arange(n_t), n_m)
    x = rng.normal(size=g1.size) + rng.normal(size=n_m)[g1] + rng.normal(size=n_t)[g2]
    y = 0.3 * x + rng.normal(size=g1.size) + rng.normal(size=n_t)[g2]
    keep = rng.random(g1.size) > 0.1  # unbalanced
    g1, g2, x, y = g1[keep], g2[keep], x[keep], y[keep]
    beta, se, xd, yd = stats.twoway_fe_cluster(y, x, g1, g2, return_demeaned=True)
    fit = sm.OLS(yd, xd).fit(cov_type="cluster", cov_kwds={"groups": np.column_stack([g1, g2])})
    assert beta == pytest.approx(fit.params[0], rel=1e-10)
    assert se == pytest.approx(fit.bse[0], rel=1e-8)
    # two-way demeaning really removed both sets of means
    assert np.abs(pd.Series(xd).groupby(g1).mean()).max() < 1e-8
    assert np.abs(pd.Series(xd).groupby(g2).mean()).max() < 1e-8


def test_oneway_cluster_matches_statsmodels():
    rng = np.random.default_rng(5)
    g = np.repeat(np.arange(20), 15)
    x = rng.normal(size=g.size)
    y = 0.2 * x + rng.normal(size=g.size) + rng.normal(size=20)[g]
    beta, se = stats.ols_cluster(y - y.mean(), x - x.mean(), g)
    fit = sm.OLS(y - y.mean(), x - x.mean()).fit(cov_type="cluster", cov_kwds={"groups": g})
    assert se == pytest.approx(fit.bse[0], rel=1e-10)
    assert beta == pytest.approx(fit.params[0], rel=1e-10)


def test_wilson_interval_literal():
    lo, hi = stats.wilson(8, 10)
    assert lo == pytest.approx(0.4902, abs=1e-4)
    assert hi == pytest.approx(0.9433, abs=1e-4)


def test_holm_and_benjamini_hochberg_literals():
    assert stats.holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert stats.benjamini_hochberg([0.01, 0.02, 0.03, 0.5], q=0.05) == [True, True, True, False]


def test_kendall_tau_between_rankings():
    assert stats.kendall_tau(["a", "b", "c", "d"], ["a", "b", "c", "d"]) == pytest.approx(1.0)
    assert stats.kendall_tau(["a", "b", "c", "d"], ["d", "c", "b", "a"]) == pytest.approx(-1.0)
    assert stats.kendall_tau(["a", "b", "c"], ["b", "a", "c"]) == pytest.approx(1 / 3)


def test_cross_sectional_statistic_uses_days_with_enough_markets():
    p, t = panel(50, 12, 0.6, seed=9)
    p.iloc[:10, 3:] = np.nan  # first 10 days have only 3 markets
    res = stats.xs_statistic(p, t, min_markets=10, lags=10)
    assert res.n_days == 40
    assert 0.3 < res.value < 0.9

import math

import numpy as np
import pandas as pd
import pytest
from arch.bootstrap import optimal_block_length

from herdbench.grading import inference, power


def ar1(n, phi, seed):
    rng = np.random.default_rng(seed)
    x = np.empty(n)
    x[0] = rng.normal()
    for i in range(1, n):
        x[i] = phi * x[i - 1] + rng.normal()
    return x


@pytest.mark.parametrize(("phi", "seed"), [(0.0, 1), (0.5, 2), (0.9, 3)])
def test_politis_white_block_matches_the_arch_package(phi, seed):
    x = ar1(400, phi, seed)
    assert inference.politis_white_block(x) == pytest.approx(optimal_block_length(x)["circular"].iloc[0], rel=1e-6)


def test_block_length_rule_clips_to_the_preregistered_range():
    flat = {f"s{i}": ar1(300, 0.0, i) for i in range(5)}
    persistent = {f"s{i}": ar1(300, 0.98, i) for i in range(5)}
    assert inference.block_length(flat.values(), lo=20, hi=40) == 20
    assert inference.block_length(persistent.values(), lo=20, hi=25) == 25  # about 31 before the cap
    assert 29 <= inference.block_length(persistent.values(), lo=20, hi=40) <= 33


def panel(T, M, rho, seed, ar=0.0):
    rng = np.random.default_rng(seed)

    def series():
        z = rng.normal(size=(T, M))
        for t in range(1, T):
            z[t] = ar * z[t - 1] + math.sqrt(1 - ar**2) * z[t]
        return z

    x = series()
    y = rho * x + math.sqrt(1 - rho**2) * series()
    idx = pd.date_range("2025-09-01", periods=T, freq="D")
    cols = [f"m{i}" for i in range(M)]
    return pd.DataFrame(x, idx, cols), pd.DataFrame(y, idx, cols)


def test_joint_bootstrap_uses_the_same_draws_for_every_proxy():
    p1, t = panel(200, 5, 0.6, 1)
    p2 = p1 * 2.0 + 1.0  # a monotone transform: identical ranks, so identical draws
    res = inference.joint_ts(({"a": p1, "b": p2}), t, min_days=60, reps=200, block=20, seed=3)
    assert np.allclose(res.draws["a"], res.draws["b"])
    assert res.z["a"] == pytest.approx(res.z["b"])


def test_rotation_null_flags_a_planted_relation_and_not_an_unrelated_one():
    p, t = panel(300, 6, 0.5, 4, ar=0.3)
    z = inference.joint_ts({"p": p}, t, min_days=60, reps=0, block=20, seed=0).z["p"]
    null = inference.rotation_null(p, t, min_days=60, draws=300, min_shift=60, seed=5)
    assert abs(z) > np.percentile(np.abs(null), 95)
    p0, t0 = panel(300, 6, 0.0, 6, ar=0.3)
    z0 = inference.joint_ts({"p": p0}, t0, min_days=60, reps=0, block=20, seed=0).z["p"]
    null0 = inference.rotation_null(p0, t0, min_days=60, draws=300, min_shift=60, seed=7)
    assert abs(z0) < np.percentile(np.abs(null0), 99)


@pytest.mark.parametrize(
    ("z", "p_holm", "rot", "fe", "ci90", "expected"),
    [
        (0.30, 0.001, 0.10, 0.20, (0.20, 0.40), "confirmed"),
        (-0.30, 0.001, 0.10, -0.20, (-0.40, -0.20), "reversed"),
        (0.30, 0.001, 0.10, -0.20, (0.20, 0.40), "inconclusive"),  # fixed-effects slope disagrees
        (0.30, 0.20, 0.10, 0.20, (0.20, 0.40), "inconclusive"),  # fails Holm
        (0.30, 0.001, 0.40, 0.20, (0.20, 0.40), "inconclusive"),  # inside the rotation null
        (0.01, 0.80, 0.10, 0.01, (-0.05, 0.07), "uninformative"),  # 90% CI inside (-0.10, 0.10)
        (0.01, 0.80, 0.10, 0.01, (-0.12, 0.07), "inconclusive"),
    ],
)
def test_verdict_rules(z, p_holm, rot, fe, ci90, expected):
    assert inference.verdict(z, p_holm, rot, fe, ci90, expected_sign=1, alpha=0.05, bound=0.10) == expected


def test_rank_intervals_and_dominance_from_joint_draws():
    rng = np.random.default_rng(0)
    draws = {"a": rng.normal(0.5, 0.01, 1000), "b": rng.normal(-0.3, 0.01, 1000), "c": rng.normal(0.31, 0.05, 1000)}
    r = inference.rank_table(draws, {"a": 0.5, "b": -0.3, "c": 0.31})
    assert r.loc["a", "rank"] == 1
    assert (r.loc["a", "rank_lo"], r.loc["a", "rank_hi"]) == (1, 1)  # a is always first by |z|
    assert inference.outranks(draws, "a", "b") == pytest.approx(1.0)
    assert 0.2 < inference.outranks(draws, "c", "b") < 0.8  # c is too uncertain to claim either way


def test_stability_requires_enough_verdicts_and_same_signs():
    halves = {"a": (0.3, 0.32), "b": (0.2, 0.25), "c": (0.1, 0.12), "d": (-0.2, -0.18)}
    ses = dict.fromkeys(halves, (0.03, 0.03))
    out = inference.stability(
        halves,
        ses,
        verdicts={"a": "confirmed", "b": "confirmed", "c": "confirmed", "d": "reversed"},
        tau_threshold=0.4,
        min_verdicts=3,
    )
    assert out["ranking_claimed"] is True
    assert out["tau"] == pytest.approx(1.0)
    assert out["unstable"] == []
    flip = inference.stability(
        {"a": (0.3, -0.2)}, {"a": (0.03, 0.03)}, verdicts={"a": "confirmed"}, tau_threshold=0.4, min_verdicts=3
    )
    assert flip["ranking_claimed"] is False
    assert flip["unstable"] == ["a"]


def test_tail_lift_is_five_for_a_perfect_signal_and_about_one_for_noise():
    rng = np.random.default_rng(1)
    x = pd.DataFrame(rng.normal(size=(500, 4)))
    assert inference.tail_lift(x, x, proxy_tail=0.10, truth_tail=0.20) == pytest.approx(5.0)
    y = pd.DataFrame(rng.normal(size=(500, 4)))
    assert 0.6 < inference.tail_lift(x, y, proxy_tail=0.10, truth_tail=0.20) < 1.4


def test_partial_spearman_removes_a_common_driver():
    rng = np.random.default_rng(2)
    z = rng.normal(size=2000)
    x = z + 0.3 * rng.normal(size=2000)
    y = z + 0.3 * rng.normal(size=2000)
    assert inference.partial_spearman(x, y, z) == pytest.approx(0.0, abs=0.06)


def test_clustered_power_reproduces_the_balanced_reference():
    mask = np.ones((27, 16), dtype=bool)
    assert power.clustered_power(mask, rho=0.2, ar=0.5, reps=1500, seed=11) == pytest.approx(0.88, abs=0.04)


def test_clustered_power_falls_on_a_thin_unbalanced_panel():
    rng = np.random.default_rng(0)
    mask = rng.random((16, 16)) > 0.3
    assert power.clustered_power(mask, rho=0.2, ar=0.5, reps=800, seed=12) < 0.8

import math
from dataclasses import replace
from datetime import date, timedelta

import numpy as np
import polars as pl

from herdbench.config import PREREG
from herdbench.grading import scorecard

FAST = replace(PREREG, bootstrap_reps=300, rotation_draws=200, min_market_days=60)


def synthetic(n_markets=12, n_days=220, seed=0):
    """Truth follows an AR(1); 'good' tracks it, 'inverse' tracks its negative, 'noise' is independent."""
    rng = np.random.default_rng(seed)
    days = [date(2025, 9, 1) + timedelta(days=i) for i in range(n_days)]
    truth_rows, proxy_rows = [], []
    for m in range(n_markets):
        z = np.empty(n_days)
        z[0] = rng.normal()
        for t in range(1, n_days):
            z[t] = 0.7 * z[t - 1] + math.sqrt(1 - 0.49) * rng.normal()
        ni = np.tanh(0.5 * z)
        for t, d in enumerate(days):
            truth_rows.append(
                {
                    "date": d,
                    "market": f"M{m}",
                    "cohort": "non_mm",
                    "ni": float(ni[t]),
                    "ai": float(ni[t]) * 0.5,
                    "oi_notional": 1e7 + m,
                    "suppressed": False,
                }
            )
            proxy_rows.append(
                {
                    "date": d,
                    "market": f"M{m}",
                    "good": float(z[t] + 0.6 * rng.normal()),
                    "inverse": float(-z[t] + 0.6 * rng.normal()),
                    "noise": float(rng.normal()),
                }
            )
    return pl.DataFrame(truth_rows), pl.DataFrame(proxy_rows), [f"M{m}" for m in range(n_markets)]


def test_primary_family_gives_the_planted_verdicts():
    truth, proxies, markets = synthetic()
    res = scorecard.primary_family(truth, proxies, markets, ["good", "inverse", "noise"], prereg=FAST)
    table = res["table"].set_index("proxy")
    assert table.loc["good", "verdict"] == "confirmed"
    assert table.loc["inverse", "verdict"] == "reversed"
    assert table.loc["noise", "verdict"] in ("uninformative", "inconclusive")
    assert table.loc["good", "rank"] in (1, 2)
    assert table.loc["noise", "rank"] == 3
    assert 20 <= res["block_length"] <= 40
    assert res["n_common_days"] > 0
    assert table.loc["good", "n_markets"] == 12


def test_common_support_drops_a_day_for_every_proxy_when_any_is_missing():
    truth, proxies, markets = synthetic(n_markets=10, n_days=120, seed=1)
    holes = proxies.with_columns(
        pl.when(pl.col("date") < date(2025, 9, 21)).then(None).otherwise(pl.col("noise")).alias("noise")
    )
    res = scorecard.primary_family(truth, holes, markets, ["good", "noise"], prereg=FAST)
    table = res["table"].set_index("proxy")
    assert table.loc["good", "n_obs"] == table.loc["noise", "n_obs"] == 10 * 100


def test_wide_builds_a_date_by_market_frame():
    truth, _, _markets = synthetic(n_markets=2, n_days=5)
    w = scorecard.wide(truth.filter(pl.col("cohort") == "non_mm"), "ni")
    assert list(w.columns) == ["M0", "M1"]
    assert len(w) == 5


def test_secondary_row_reports_every_statistic():
    truth, proxies, _markets = synthetic(n_markets=12, n_days=200, seed=2)
    row = scorecard.score_pair(
        scorecard.wide(proxies, "good"),
        scorecard.wide(truth, "ni"),
        scorecard.wide(truth, "oi_notional"),
        prereg=FAST,
        signed=True,
    )
    for key in (
        "z",
        "rho",
        "ci_low",
        "ci_high",
        "p_value",
        "n_markets",
        "xs_rho",
        "xs_p",
        "oi_weighted_rho",
        "changes_rho",
        "sign_agreement",
        "placebo_rho",
        "tail_lift",
    ):
        assert key in row
    assert row["rho"] > 0.3
    assert row["placebo_rho"] < row["rho"]
    assert row["tail_lift"] > 1.5

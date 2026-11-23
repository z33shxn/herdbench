"""Bring your own proxy: input formats, baselines on common support, labels and input validation."""

from __future__ import annotations

import itertools
import json

import numpy as np
import pandas as pd
import polars as pl
import pytest

from herdbench import api
from tests.utils import world

P = world.PREREG_WORLD


@pytest.fixture(scope="module")
def data(built):
    results = built["paths"].results
    return pl.read_parquet(results / "truth_panel.parquet"), pl.read_parquet(results / "proxies.parquet")


def signal_from_truth(panel: pl.DataFrame, noise: float, seed: int = 0, truth: str = "ni") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    t = panel.filter((pl.col("cohort") == "non_mm") & pl.col("eligible")).select(["date", "market", truth]).to_pandas()
    t["value"] = t[truth] + rng.normal(0, noise, len(t))
    return t[["date", "market", "value"]]


def grade(signal, data, **kw):
    panel, proxies = data
    kw.setdefault("bootstrap_reps", 20)
    return api.grade(signal, panel=panel, proxies=proxies, prereg=P, **kw)


def test_a_signal_built_from_the_truth_scores_near_one(data):
    rep = grade(signal_from_truth(data[0], 1e-4), data, baselines=False)
    assert rep.table.iloc[0]["rho"] > 0.95
    assert rep.label == "in-sample, published panel"
    assert rep.n_market_days > 0


def test_long_wide_series_and_polars_inputs_give_identical_results(data):
    long = signal_from_truth(data[0], 0.05, seed=1)
    wide = long.pivot(index="date", columns="market", values="value")
    series = long.set_index(["date", "market"])["value"]
    rhos = [grade(x, data, baselines=False).table.iloc[0]["rho"] for x in (long, wide, series, pl.from_pandas(long))]
    assert max(rhos) - min(rhos) < 1e-12


def test_a_value_stamped_at_the_cutoff_belongs_to_that_snapshot(data):
    panel = data[0]
    long = signal_from_truth(panel, 0.05, seed=2)
    cut = panel.select(["date", "cutoff"]).unique().to_pandas()
    cut["date"] = pd.to_datetime(cut["date"])
    at_cutoff = long.assign(date=pd.to_datetime(long["date"])).merge(cut, on="date")
    at_cutoff = at_cutoff.assign(date=at_cutoff["cutoff"])[["date", "market", "value"]]
    a = grade(long, data, baselines=False).table.iloc[0]["rho"]
    b = grade(at_cutoff, data, baselines=False).table.iloc[0]["rho"]
    assert a == pytest.approx(b)


def test_a_value_stamped_after_the_cutoff_moves_to_the_next_snapshot(data):
    panel = data[0]
    long = signal_from_truth(panel, 0.05, seed=3)
    labels = api.to_wide(long)
    stamped = long.assign(date=pd.to_datetime(long["date"]).dt.tz_localize("UTC") + pd.Timedelta(hours=1))
    moved = api.to_wide(stamped, cutoffs=panel)
    dates = list(labels.index)
    for d, nxt in itertools.pairwise(dates):
        if nxt - d == pd.Timedelta(days=1):
            pd.testing.assert_series_equal(moved.loc[nxt], labels.loc[d], check_names=False)
            break
    else:
        pytest.fail("no consecutive snapshot dates in the synthetic panel")
    assert labels.index[0] not in moved.index  # nothing can be scored before it was known
    rep = grade(stamped, data, baselines=False)
    assert any("aligned" in n for n in rep.notes)


def test_timestamps_without_cutoffs_are_refused(data):
    long = signal_from_truth(data[0], 0.05)
    stamped = long.assign(date=pd.to_datetime(long["date"]) + pd.Timedelta(hours=5))
    with pytest.raises(ValueError, match="cutoffs"):
        api.to_wide(stamped)


def test_style_cohorts_never_get_the_baselines_that_define_them(data):
    sig = signal_from_truth(data[0], 0.05)
    rep = grade(sig, data, cohort="trend")
    assert not {"momentum_7d", "oi_flow_7d"} & set(rep.table["signal"])
    named = grade(sig, data, cohort="contrarian", baselines=["momentum_7d"])
    assert any("mechanical" in n for n in named.notes)


def test_baselines_are_scored_on_exactly_the_same_market_days(data):
    rep = grade(signal_from_truth(data[0], 0.05), data)
    names = list(rep.table["signal"])
    assert names[0] == "signal"
    assert set(names[1:]) == set(api.SIGNED_BASELINES)
    assert rep.table["n_obs"].nunique() == 1


def test_unsigned_truths_get_crowding_baselines_with_the_studys_exclusions(data):
    co = grade(signal_from_truth(data[0], 0.05, truth="coheld_share"), data, truth="coheld_share")
    assert "comovement_30d" in set(co.table["signal"])
    ab = grade(signal_from_truth(data[0], 0.05, truth="abs_ni"), data, truth="abs_ni")
    assert "comovement_30d" not in set(ab.table["signal"])
    assert ab.table["sign_agreement"].isna().all()  # sign agreement is for signed truths only


def test_expected_sign_flips_sign_agreement(data):
    sig = signal_from_truth(data[0], 0.05)
    pos = grade(sig, data, baselines=False).table.iloc[0]
    neg = grade(sig.assign(value=-sig["value"]), data, baselines=False, expected_sign=-1).table.iloc[0]
    assert pos["sign_agreement"] == pytest.approx(neg["sign_agreement"])
    assert neg["rho"] == pytest.approx(-pos["rho"])


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"truth": "volume"}, "truth must be"),
        ({"cohort": "whales"}, "cohort must be"),
        ({"expected_sign": 0}, "expected_sign"),
    ],
)
def test_invalid_arguments_are_rejected(data, kwargs, message):
    with pytest.raises(ValueError, match=message):
        grade(signal_from_truth(data[0], 0.05), data, **kwargs)


def test_bad_signals_are_rejected(data):
    sig = signal_from_truth(data[0], 0.05)
    with pytest.raises(ValueError, match="more than one value"):
        grade(pd.concat([sig, sig.head(3)]), data)
    with pytest.raises(ValueError, match="no market"):
        grade(sig.drop_duplicates("date").assign(market="NOT_A_MARKET"), data)
    with pytest.raises(ValueError, match="value="):
        grade(sig.assign(other=1.0), data)
    with pytest.raises(TypeError):
        grade([1, 2, 3], data)


def test_unknown_markets_are_reported_not_silently_dropped(data):
    sig = signal_from_truth(data[0], 0.05)
    extra = sig.drop_duplicates("date").head(5).assign(market="ZZZ")
    rep = grade(pd.concat([sig, extra]), data, baselines=False)
    assert any("ZZZ" in n for n in rep.notes)


def test_report_prints_and_serialises(data):
    rep = grade(signal_from_truth(data[0], 0.05), data)
    text = str(rep)
    assert "in-sample, published panel" in text
    assert "premium_24h" in text
    json.dumps(rep.to_dict())


def test_bundled_panel_location_can_be_overridden(built, monkeypatch):
    results = built["paths"].results
    monkeypatch.setenv("HERDBENCH_PANEL", str(results / "truth_panel.parquet"))
    monkeypatch.setenv("HERDBENCH_PROXIES", str(results / "proxies.parquet"))
    api._read.cache_clear()
    panel = api.load_truth_panel(cohort="non_mm", eligible_only=True)
    assert isinstance(panel, pl.DataFrame)
    assert set(panel["cohort"].unique()) == {"non_mm"}
    assert panel["eligible"].all()
    assert isinstance(api.load_proxies(as_pandas=True), pd.DataFrame)
    with pytest.raises(ValueError, match="cohort"):
        api.load_truth_panel(cohort="whales")

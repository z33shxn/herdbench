"""Registered rules the pipeline must enforce: common support, pinned shares, gate labels, timing guards,
settlement periods, input cross-checks, and byte-stable outputs."""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import polars as pl
import pytest

from herdbench import api, cli, fetch, pipeline
from herdbench.grading import transfer
from herdbench.sources.hfmirror import RemoteFile
from tests.utils import world

P = world.PREREG_WORLD
UTC = timezone.utc


def test_primary_panel_rows_use_the_confirmatory_familys_market_days(built):
    family = pd.read_csv(built["paths"].binance_results / "primary_family.csv").set_index("proxy")
    grid = built["res"]["tables"]["directional_primary"]
    row = grid[(grid["proxy"] == "premium_24h") & (grid["truth"] == "ni") & (grid["cohort"] == "non_mm")].iloc[0]
    assert row["n_obs"] == family.loc["premium_24h", "n_obs"]


def test_primary_rows_carry_ai_all_beside_them(built):
    family = pd.read_csv(built["paths"].binance_results / "primary_family.csv")
    assert {"ai_all_rho", "ai_all_ci_low", "ai_all_ci_high", "ai_all_p"} <= set(family.columns)
    assert family["ai_all_rho"].notna().all()


def test_funding_rows_report_their_pinned_share(built):
    grid = built["res"]["tables"]["directional_primary"]
    funding = grid[grid["proxy"].isin(["funding_24h", "funding_7d", "bn_funding_24h"])]
    assert not funding.empty
    assert funding["pinned_share"].between(0, 1).all()
    others = grid[~grid["proxy"].isin(list(pipeline.PINNED))]
    assert others["pinned_share"].isna().all()


def test_complete_window_sensitivity_is_published_for_the_flow_proxies(built):
    sens = pd.read_csv(built["paths"].results / "sensitivity_complete_flows.csv")
    assert set(sens["proxy"]) <= set(pipeline.FLOW_PROXIES)
    liq = pd.read_csv(built["paths"].results / "liquidation_target.csv")
    assert set(liq["variant"]) == {"registered", "complete windows"}
    assert liq.loc[liq["variant"] == "complete windows", "bh_flag"].isna().all()


def test_comovement_is_measured_against_the_scored_panel(built):
    tables = built["res"]["tables"]
    primary = tables["unsigned_primary"]
    all_markets = tables["unsigned_all_markets"]
    a = primary[(primary["proxy"] == "comovement_30d") & (primary["cohort"] == "non_mm")]["rho"]
    b = all_markets[(all_markets["proxy"] == "comovement_30d") & (all_markets["cohort"] == "non_mm")]["rho"]
    assert len(a) == 1
    assert len(b) == 1


def test_a_failed_gate_labels_every_result_and_promotes_ai_all(built, tmp_path):
    src = built["paths"]
    paths = pipeline.Paths(src.raw, src.derived, tmp_path / "results")
    paths.results.mkdir()
    shutil.copyfile(src.results / "truth_panel.parquet", paths.results / "truth_panel.parquet")
    gates = json.loads((src.results / "gates.json").read_text(encoding="utf-8"))
    gates["label"] = "fills incomplete"
    pipeline.write_json(paths.results / "gates.json", gates)
    panel = pipeline.stage_publish(paths, P)
    assert (panel["label"] == "fills incomplete").all()
    assert (pl.read_parquet(paths.results / "proxies.parquet")["label"] == "fills incomplete").all()
    published = json.loads((paths.results / "truth_panel_summary.json").read_text(encoding="utf-8"))
    assert published["label"] == "fills incomplete"
    report = api.grade(
        panel.filter(pl.col("cohort") == "non_mm").select(["date", "market", "ai"]),
        panel=panel,
        baselines=False,
        bootstrap_reps=10,
        prereg=P,
    )
    assert any("fills incomplete" in n for n in report.notes)
    addendum = built["root"] / "addendum.md"
    pipeline.stage_score(paths, P, addendum=addendum)
    summary = json.loads((paths.results / "score_summary.json").read_text(encoding="utf-8"))
    assert summary["label"] == "fills incomplete"
    assert summary["headline"] == "ai(all)"
    for f in [*paths.results.glob("*.csv"), *paths.binance_results.glob("*.csv"), *paths.finra_results.glob("*.csv")]:
        assert (pd.read_csv(f)["label"] == "fills incomplete").all(), f.name


def test_open_interest_must_come_from_snapshots_captured_before_the_cutoff():
    d = date(2026, 4, 24)
    cut = pl.DataFrame(
        {
            "date": [d - timedelta(days=8), d - timedelta(days=1), d],
            "capture": [
                datetime(2026, 4, 16, 0, 5, tzinfo=UTC),
                datetime(2026, 4, 23, 23, 30, tzinfo=UTC),  # captured after the cutoff of the next snapshot
                datetime(2026, 4, 23, 23, 59, 57, tzinfo=UTC),
            ],
            "cutoff": [
                datetime(2026, 4, 16, 0, tzinfo=UTC),
                datetime(2026, 4, 23, 23, tzinfo=UTC),
                datetime(2026, 4, 23, 23, tzinfo=UTC),
            ],
        }
    )
    ok = pipeline.oi_timing(cut, [d]).row(0, named=True)
    assert ok["oi_timing_ok"] is False
    good = cut.with_columns(
        pl.when(pl.col("date") == d - timedelta(days=1))
        .then(pl.lit(datetime(2026, 4, 23, 0, 5, tzinfo=UTC)))
        .otherwise(pl.col("capture"))
        .alias("capture")
    )
    assert pipeline.oi_timing(good, [d]).row(0, named=True)["oi_timing_ok"] is True


def test_each_transfer_period_spans_the_time_since_the_previous_settlement():
    assert transfer.previous_settlement(date(2025, 12, 15)) == date(2025, 11, 28)
    assert transfer.previous_settlement(date(2026, 1, 15)) == date(2025, 12, 31)
    with pytest.raises(ValueError, match="no known FINRA settlement"):
        transfer.previous_settlement(date(2025, 11, 1))


class FakeInfo:
    """Serves the synthetic funding files back, as Hyperliquid's fundingHistory would."""

    def __init__(self, paths, bump: float = 0.0):
        self.funding = pipeline.load_funding(paths, date(2026, 1, 1), date(2026, 5, 31))
        self.bump = bump

    def funding_history(self, coin, start_ms, end_ms):
        t0 = datetime.fromtimestamp(start_ms / 1000, tz=UTC)
        t1 = datetime.fromtimestamp(end_ms / 1000, tz=UTC)
        f = self.funding.filter((pl.col("market") == coin) & (pl.col("time") >= t0) & (pl.col("time") <= t1))
        out = f.select(["time", "funding", "premium"]).to_pandas()
        out["funding"] = out["funding"] + self.bump
        return out


def test_funding_cross_check_passes_on_identical_data_and_fails_on_a_difference(built, tmp_path):
    src = built["paths"]
    paths = pipeline.Paths(src.raw, src.derived, tmp_path / "results")
    good = pipeline.check_funding(paths, markets=("BTC", "ETH"), api=FakeInfo(src), prereg=P)
    assert good["hours_compared"] > 0
    assert good["share_equal"] == 1.0
    bad = pipeline.check_funding(paths, markets=("BTC",), api=FakeInfo(src, bump=1e-6), prereg=P)
    assert bad["share_equal"] == 0.0


def test_the_registered_addendum_is_never_overwritten_by_accident(built, tmp_path, monkeypatch):
    existing = tmp_path / "addendum.md"
    existing.write_text("registered\n", encoding="utf-8")
    monkeypatch.setattr(pipeline, "ADDENDUM", existing)
    with pytest.raises(SystemExit):
        cli.main(["build", "addendum", "--data", str(built["root"])])
    assert existing.read_text(encoding="utf-8") == "registered\n"


def test_json_results_use_lf_line_ends_on_every_platform(tmp_path):
    f = tmp_path / "x.json"
    pipeline.write_json(f, {"a": [1, 2]})
    assert b"\r\n" not in f.read_bytes()


def test_the_mirror_listing_is_canonical(tmp_path):
    files = [RemoteFile("b.parquet", 2, "x"), RemoteFile("a.parquet", 1, None)]
    one = fetch.write_listing(tmp_path / "1.json", "rev", files)
    two = fetch.write_listing(tmp_path / "2.json", "rev", list(reversed(files)))
    assert one == two
    assert (tmp_path / "1.json").read_bytes() == (tmp_path / "2.json").read_bytes()
    assert b"\r\n" not in (tmp_path / "1.json").read_bytes()


def test_half_samples_need_the_registered_sixty_days_per_market(monkeypatch):
    from herdbench.grading import inference, scorecard
    from tests.grading.test_scorecard import synthetic

    truth, proxies, markets = synthetic(n_markets=6, n_days=200, seed=4)
    seen = []
    real = inference.joint_ts

    def spy(frames, truth_w, min_days, *args, **kwargs):
        seen.append(min_days)
        return real(frames, truth_w, min_days, *args, **kwargs)

    monkeypatch.setattr(inference, "joint_ts", spy)
    pr = replace(P, bootstrap_reps=10, rotation_draws=10, min_market_days=60)
    scorecard.primary_family(truth, proxies, markets, ["good", "noise"], pr)
    assert seen == [60, 60, 60]  # full sample, then both halves


def test_a_date_missing_from_the_control_is_skipped_not_scored_raw():
    from herdbench.grading import liquidation

    days = pd.date_range("2026-01-01", periods=5)
    cols = [f"M{i}" for i in range(15)]
    rng = np.random.default_rng(0)
    x = pd.DataFrame(rng.normal(size=(5, 15)), index=days, columns=cols)
    y = x + rng.normal(scale=0.1, size=(5, 15))
    control = pd.DataFrame(rng.normal(size=(5, 15)), index=days, columns=cols).drop(days[2])
    out = liquidation.xs_partial(x, y, control, min_markets=10, lags=1)
    assert days[2] not in out["series"].index
    assert out["n_days"] == 4

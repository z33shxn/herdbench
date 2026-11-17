from dataclasses import replace
from datetime import date, timedelta

import polars as pl
import pytest

from herdbench.config import PREREG
from herdbench.truth import build, mm, style
from tests.utils import synth

HLP_CHILD = "0x010461c14e146ac35fe42271bdc1134ee31c703a"
MM = "0x" + "m" * 40
EVAL = date(2026, 3, 3)
PREREG_TEST = replace(PREREG, mm_min_notional=1_000.0, min_oi_notional=1_000.0)


def wallet(i: int) -> str:
    return f"0x{i:040x}"


def btc_rows():
    rows = []
    for i in range(25):  # 13 long, 12 short
        rows.append({"user": wallet(i), "market": "BTC", "size": 1.0 if i < 13 else -1.0, "notional": 100.0,
                     "account_value": 10_000.0 - i})
    rows.append({"user": wallet(25), "market": "BTC", "size": -1.0, "notional": 100.0})  # 13 v 13
    rows.append({"user": MM, "market": "BTC", "size": 2.0, "notional": 200.0})
    rows.append({"user": HLP_CHILD, "market": "BTC", "size": -2.0, "notional": 200.0})
    for i in range(5):  # thin market: must be suppressed
        rows.append({"user": wallet(100 + i), "market": "ETH", "size": 1.0 if i % 2 else -1.0, "notional": 50.0})
    return rows


@pytest.fixture
def archive(tmp_path):
    raw = tmp_path / "hf"
    for day in (EVAL - timedelta(days=8), date(2026, 3, 1), date(2026, 3, 2), EVAL):
        synth.snapshot(raw, day, btc_rows())
    for k in range(1, 31):  # 30 days of fills: MM is passive, active daily and turns its small book over often
        day = EVAL - timedelta(days=k)
        synth.fills(raw, day, [
            {"coin": "BTC", "price": 100.0, "size": 50.0, "address": MM, "crossed": False, "side": "sell"},
            {"coin": "BTC", "price": 100.0, "size": 50.0, "address": wallet(1), "crossed": True, "side": "buy"},
            {"coin": "BTC", "price": 100.0, "size": 1.0, "address": wallet(2), "crossed": True, "side": "sell",
             "is_liquidation": True, "liquidation_method": "market", "direction": "Close Long"},
        ])
    return tmp_path


def run(archive):
    raw, derived = archive / "hf", archive / "derived"
    fill_days = build.summarize_fill_days(raw, derived)
    index = build.snapshot_index(raw)
    markets = build.market_panel(index, derived)
    emap: dict[str, str] = {}
    contrib = style.entity_contributions(build.style_panel(index, markets, derived), emap)
    activity = mm.entity_activity(build.load_activity(derived), emap)
    cands = build.candidate_entities(activity, fill_days, [EVAL], PREREG_TEST)
    gross = build.entity_gross_panel(index, emap, cands)
    sets = build.market_maker_sets(activity, fill_days, [EVAL], gross, index.keys(), PREREG_TEST)
    cells = build.truth_cells(index, markets, sets, contrib, emap, [EVAL], PREREG_TEST)
    return fill_days, sets, cells, derived


def test_truth_cells_end_to_end(archive):
    fill_days, sets, cells, derived = run(archive)
    assert len(fill_days) == 30
    assert sets["non_mm"][EVAL] == {MM}  # 150k traded against a mean book of 200: turnover 750x
    btc = {r["cohort"]: r for r in cells.filter(pl.col("market") == "BTC").iter_rows(named=True)}
    assert btc["all"]["n_entities"] == 27        # 26 wallets + the market maker; HLP removed
    assert btc["non_mm"]["n_entities"] == 26
    assert btc["non_mm"]["ni"] == pytest.approx(0.0)
    assert btc["top_av"]["n_entities"] == 6      # ceil(0.2 * 26) rankable non_mm holders
    assert btc["all"]["suppressed"]              # all minus non_mm is one market maker: complement rule
    assert not btc["non_mm"]["suppressed"]
    assert btc["non_mm"]["eligible"]             # OI at the previous snapshot is 1,500 >= 1,000
    assert btc["all"]["oi_notional"] == pytest.approx(1_500.0)
    assert btc["non_mm_s070"]["suppressed"]      # robustness cohorts are never publishable
    assert cells.filter(pl.col("market") == "ETH")["suppressed"].all()
    liq = build.load_fill_daily(derived, "liquidations")
    assert liq.filter(pl.col("market") == "BTC")["liq_long_ntl"].sum() == pytest.approx(30 * 100.0)


def test_published_panel_is_rounded_and_never_contains_addresses_or_small_cells(archive):
    *_, cells, _ = run(archive)
    panel = build.publishable(cells)
    assert set(panel["cohort"].to_list()) <= {"non_mm", "top_av", "trend", "contrarian"}
    assert panel.filter(pl.col("cohort") == "non_mm")["n_entities"].to_list() == [20]  # 26 floored to 20
    assert "top2_share" not in panel.columns
    assert "0x" not in panel.write_csv()

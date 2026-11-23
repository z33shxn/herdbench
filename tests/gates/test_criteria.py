from datetime import date

import polars as pl
import pytest

from herdbench.gates import criteria


def test_gate0_counts_market_days_whose_maker_and_taker_sides_balance():
    vol = pl.DataFrame(
        {
            "day": [date(2026, 3, 1)] * 3 + [date(2026, 3, 2)],
            "market": ["A", "B", "C", "A"],
            "volume_ntl": [100.0, 100.0, 100.0, 50.0],
            "maker_ntl": [100.0, 100.5, 120.0, 50.0],
            "n_trades": [1, 1, 1, 1],
        }
    )
    out = criteria.gate0(vol, max_gap=0.01, min_share=0.99)
    assert out["market_days"] == 4
    assert out["share_balanced"] == pytest.approx(0.75)  # C is 17% apart
    assert out["passed"] is False


def test_kc1_counts_stocks_with_enough_days_between_first_and_last_fill():
    vol = pl.DataFrame(
        {
            "day": [date(2025, 11, 12), date(2026, 8, 18), date(2026, 5, 1), date(2026, 8, 18)],
            "market": ["xyz:NVDA", "xyz:NVDA", "xyz:RKLB", "xyz:RKLB"],
            "volume_ntl": [1.0] * 4,
            "maker_ntl": [1.0] * 4,
            "n_trades": [1] * 4,
        }
    )
    out = criteria.kc1(vol, ["xyz:NVDA", "xyz:RKLB"], min_days=183, min_stocks=25)
    assert out["qualifying"] == ["xyz:NVDA"]
    assert out["count"] == 1
    assert out["passed"] is False


def test_reconciliation_summary_weights_pairs_by_changed_notional():
    pairs = [
        {"reconciled_weight": 99.0, "total_weight": 100.0, "group_reconciled_weight": 9.0, "group_total_weight": 10.0},
        {"reconciled_weight": 900.0, "total_weight": 900.0, "group_reconciled_weight": 0.0, "group_total_weight": 0.0},
    ]
    out = criteria.reconciliation_summary(pairs, threshold=0.99)
    assert out["reconciled_share"] == pytest.approx(999.0 / 1000.0)
    assert out["market_maker_share"] == pytest.approx(0.9)
    assert out["passed"] is True
    assert out["pairs_below_threshold"] == []
    worse = criteria.reconciliation_summary(
        [{"date": "2026-05-23", "reconciled_weight": 1.0, "total_weight": 3.0}, *pairs], threshold=0.99
    )
    assert [p["date"] for p in worse["pairs_below_threshold"]] == ["2026-05-23"]

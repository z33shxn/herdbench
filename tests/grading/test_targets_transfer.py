from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import polars as pl
import pytest

from herdbench.grading import bridges, liquidation, transfer

UTC = timezone.utc


def test_forward_liquidations_use_the_seven_days_after_capture_and_drop_windows_with_missing_fill_days():
    cap = datetime(2026, 3, 1, 0, 5, tzinfo=UTC)
    markets = pl.DataFrame(
        {
            "date": [date(2026, 3, 1), date(2026, 3, 20)],
            "market": ["BTC", "BTC"],
            "capture_ms": [int(cap.timestamp() * 1000), int((cap + timedelta(days=19)).timestamp() * 1000)],
            "oi_notional": [1_000.0, 1_000.0],
        }
    )
    hours = [
        datetime(2026, 3, 1, 0, tzinfo=UTC),
        datetime(2026, 3, 1, 1, tzinfo=UTC),
        datetime(2026, 3, 7, 23, tzinfo=UTC),
        datetime(2026, 3, 8, 1, tzinfo=UTC),
    ]
    liq = pl.DataFrame(
        {
            "market": ["BTC"] * 4,
            "hour": hours,
            "liq_ntl": [5.0, 10.0, 20.0, 99.0],
            "liq_long_ntl": [5.0, 10.0, 0.0, 99.0],
            "liq_short_ntl": [0.0, 0.0, 20.0, 0.0],
        }
    )
    fill_days = {date(2026, 3, 1) + timedelta(days=i) for i in range(8)}  # nothing for the 20 March window
    out = liquidation.forward_liquidations(liq, markets, fill_days, horizon_days=7).sort("date")
    first = out.row(0, named=True)
    # bars starting 01:00 on 1 March to 23:00 on 7 March are inside [00:05 + 1h rounding, +7d): 10 + 20
    assert first["fwd_liq_intensity"] == pytest.approx(30.0 / 1_000.0)
    assert first["fwd_liq_signed"] == pytest.approx((10.0 - 20.0) / 30.0)
    assert out.height == 1  # the 20 March window touches days without fills


def test_cross_sectional_partial_correlation_controls_for_volatility():
    rng = np.random.default_rng(0)
    idx = pd.date_range("2026-01-01", periods=60, freq="D")
    cols = [f"m{i}" for i in range(30)]
    vol = pd.DataFrame(rng.normal(size=(60, 30)), idx, cols)
    proxy = vol + 0.3 * pd.DataFrame(rng.normal(size=(60, 30)), idx, cols)
    target = vol + 0.3 * pd.DataFrame(rng.normal(size=(60, 30)), idx, cols)
    raw = liquidation.xs_partial(proxy, target, None, min_markets=10, lags=5)
    part = liquidation.xs_partial(proxy, target, vol, min_markets=10, lags=5)
    assert raw["mean"] > 0.7
    assert abs(part["mean"]) < 0.1


def test_finra_publication_dates_follow_the_published_schedule():
    assert transfer.FINRA_PUBLICATION[date(2026, 1, 15)] == date(2026, 1, 27)
    assert transfer.FINRA_PUBLICATION[date(2026, 3, 31)] == date(2026, 4, 10)
    assert transfer.FINRA_PUBLICATION[date(2026, 8, 14)] == date(2026, 8, 25)
    assert transfer.FINRA_PUBLICATION[date(2025, 12, 31)] == date(2026, 1, 12)
    assert len(transfer.FINRA_PUBLICATION) == 17


def test_transfer_statistic_on_a_planted_negative_relation():
    rng = np.random.default_rng(1)
    rows = []
    for s in range(20):
        base = rng.normal()
        for k in range(12):
            si = base + rng.normal()
            rows.append({"stock": f"S{s}", "period": k, "ni": -0.6 * si + rng.normal() * 0.5, "days_to_cover": si})
    panel = pd.DataFrame(rows)
    out = transfer.transfer_stats(panel, ["days_to_cover"], {"days_to_cover": -1}, power=0.95, threshold=0.8)
    row = out.iloc[0]
    assert row["beta"] < 0
    assert row["p_value"] < 0.01
    assert row["direction_as_expected"]
    weak = transfer.transfer_stats(panel, ["days_to_cover"], {"days_to_cover": -1}, power=0.5, threshold=0.8)
    assert np.isnan(weak.iloc[0]["p_value"])  # underpowered: estimates and intervals only


def test_cot_bridge_pairs_each_report_with_the_next_snapshot():
    captures = pl.DataFrame(
        {
            "date": [date(2026, 1, 6), date(2026, 1, 7)],
            "capture": [datetime(2026, 1, 6, 0, 5, tzinfo=UTC), datetime(2026, 1, 7, 0, 5, tzinfo=UTC)],
        }
    )
    assert bridges.truth_date_for_report(date(2026, 1, 6), captures) == date(2026, 1, 7)

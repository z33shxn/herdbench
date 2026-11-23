import math
from datetime import date, datetime, timedelta, timezone

import polars as pl
import pytest

from herdbench.proxies import binance_px, equity, native

UTC = timezone.utc
C = datetime(2026, 3, 2, 0, tzinfo=UTC)


def metrics(rows):
    """rows: (create_time, global, top_account, top_position, open_interest)."""
    return pl.DataFrame(
        {
            "symbol": ["BTCUSDT"] * len(rows),
            "create_time": [r[0] for r in rows],
            "count_long_short_ratio": [r[1] for r in rows],
            "count_toptrader_long_short_ratio": [r[2] for r in rows],
            "sum_toptrader_long_short_ratio": [r[3] for r in rows],
            "sum_open_interest": [r[4] for r in rows],
        }
    )


def queries():
    return pl.DataFrame({"market": ["BTC"], "symbol": ["BTCUSDT"], "cutoff": [C]})


def test_ratios_use_the_last_row_before_the_cutoff():
    m = metrics(
        [
            (C - timedelta(minutes=5), 2.0, 3.0, 4.0, 100.0),
            (C, 99.0, 99.0, 99.0, 999.0),  # stamped at the cutoff: too late
            (C - timedelta(minutes=10), 1.0, 1.0, 1.0, 50.0),
            (C - timedelta(days=7) - timedelta(minutes=5), 1.0, 1.0, 1.0, 80.0),
        ]
    )
    out = binance_px.ratio_features(m, queries(), max_stale_minutes=60).row(0, named=True)
    assert out["bn_global_ls"] == pytest.approx(math.log(2.0))
    assert out["bn_top_account_ls"] == pytest.approx(math.log(3.0))
    assert out["bn_top_position_ls"] == pytest.approx(math.log(4.0))
    assert out["bn_oi_change_7d"] == pytest.approx(math.log(100.0 / 80.0))


def test_a_stale_ratio_counts_as_missing():
    m = metrics([(C - timedelta(minutes=70), 2.0, 3.0, 4.0, 100.0)])  # information 65 minutes old at the cutoff
    out = binance_px.ratio_features(m, queries(), max_stale_minutes=60).row(0, named=True)
    assert out["bn_global_ls"] is None


def test_taker_ratio_sums_volumes_over_the_last_day():
    k = pl.DataFrame(
        {
            "symbol": ["BTCUSDT"] * 4,
            "open_time": [C - timedelta(hours=24), C - timedelta(minutes=5), C, C - timedelta(hours=25)],
            "taker_buy_volume": [3.0, 1.0, 100.0, 100.0],
            "taker_sell_volume": [1.0, 1.0, 1.0, 1.0],
        }
    )
    out = binance_px.taker_features(k, queries()).row(0, named=True)
    assert out["bn_taker_ls"] == pytest.approx(math.log(4.0 / 2.0))  # bars at c-24h and c-5m only


def test_binance_funding_is_the_mean_of_settlements_in_the_last_day():
    f = pl.DataFrame(
        {
            "symbol": ["BTCUSDT"] * 4,
            "time": [C - timedelta(hours=24), C - timedelta(hours=8), C, C - timedelta(hours=30)],
            "funding": [0.0001, 0.0003, 9.0, 9.0],
        }
    )
    assert binance_px.funding_features(f, queries()).row(0, named=True)["bn_funding_24h"] == pytest.approx(0.0002)


def test_binance_pinned_share_counts_settlements_at_the_interest_rate_floor():
    f = pl.DataFrame(
        {
            "symbol": ["BTCUSDT"] * 3,
            "time": [C - timedelta(hours=24), C - timedelta(hours=16), C - timedelta(hours=8)],
            "interval_hours": [8, 8, 4],
            "funding": [0.0001, 0.0003, 0.00005],  # floor at 8h, above it, floor at 4h
        }
    )
    row = binance_px.funding_features(f, queries()).row(0, named=True)
    assert row["bn_pinned_share_24h"] == pytest.approx(2 / 3)


def test_us_close_follows_daylight_saving():
    assert equity.us_close_utc(date(2026, 1, 15)) == datetime(2026, 1, 15, 21, 0, tzinfo=UTC)  # EST
    assert equity.us_close_utc(date(2026, 7, 15)) == datetime(2026, 7, 15, 20, 0, tzinfo=UTC)  # EDT


def test_first_snapshot_after_an_instant():
    captures = pl.DataFrame(
        {
            "date": [date(2026, 1, 15), date(2026, 1, 16)],
            "capture": [datetime(2026, 1, 15, 0, 5, tzinfo=UTC), datetime(2026, 1, 16, 0, 4, tzinfo=UTC)],
        }
    )
    assert equity.first_snapshot_after(captures, datetime(2026, 1, 15, 21, 0, tzinfo=UTC)) == date(2026, 1, 16)
    assert equity.first_snapshot_after(captures, datetime(2026, 1, 16, 21, 0, tzinfo=UTC)) is None


def test_period_windows_sum_between_two_cutoffs():
    f = pl.DataFrame(
        {
            "market": ["xyz:NVDA"] * 3,
            "time": [C - timedelta(days=10), C - timedelta(days=3), C + timedelta(hours=1)],
            "funding": [1.0, 2.0, 50.0],
            "premium": [0.1, 0.2, 5.0],
        }
    )
    q = pl.DataFrame({"market": ["xyz:NVDA"], "start": [C - timedelta(days=7)], "end": [C]})
    out = native.period_means(f, "time", ["funding", "premium"], q).row(0, named=True)
    assert out["funding"] == pytest.approx(2.0)
    assert out["premium"] == pytest.approx(0.2)

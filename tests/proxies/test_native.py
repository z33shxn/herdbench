import math
from datetime import date, datetime, timedelta, timezone

import numpy as np
import polars as pl
import pytest

from herdbench.proxies import native

UTC = timezone.utc


def ms(dt):
    return int(dt.timestamp() * 1000)


@pytest.mark.parametrize(
    ("capture", "expected"),
    [
        (datetime(2025, 9, 1, 0, 5, 21, tzinfo=UTC), datetime(2025, 9, 1, 0, 0, tzinfo=UTC)),
        (datetime(2025, 9, 1, 20, 5, 0, tzinfo=UTC), datetime(2025, 9, 1, 20, 0, tzinfo=UTC)),
        (datetime(2026, 4, 23, 23, 59, 57, tzinfo=UTC), datetime(2026, 4, 23, 23, 0, tzinfo=UTC)),
    ],
)
def test_cutoff_is_the_last_full_hour_before_capture(capture, expected):
    assert native.cutoff(ms(capture)) == expected


def hourly(market, start, closes, volume=1.0):
    return pl.DataFrame(
        {
            "market": [market] * len(closes),
            "hour": [start + timedelta(hours=i) for i in range(len(closes))],
            "close": [float(c) for c in closes],
            "volume_ntl": [float(volume)] * len(closes),
        }
    )


def test_price_at_uses_the_last_trade_before_the_instant():
    h = hourly("BTC", datetime(2026, 3, 1, 0, tzinfo=UTC), [100, 101, 102])  # bars 00:00, 01:00, 02:00
    q = pl.DataFrame(
        {
            "market": ["BTC", "BTC", "BTC"],
            "t": [
                datetime(2026, 3, 1, 1, tzinfo=UTC),
                datetime(2026, 3, 1, 2, 30, tzinfo=UTC),
                datetime(2026, 3, 1, 0, 30, tzinfo=UTC),
            ],
        }
    )
    out = native.price_at(h, q).sort("t")
    # 00:30: no bar has closed yet; 01:00: bar 00:00 closed at 100; 02:30: bar 01:00 closed at 101
    assert out["price"].to_list() == [None, 100.0, 101.0]


def test_funding_window_ends_strictly_before_the_cutoff():
    c = datetime(2026, 3, 2, 0, tzinfo=UTC)
    stamps = [c - timedelta(hours=24) + timedelta(hours=i, milliseconds=168) for i in range(25)]  # last one is after c
    funding = pl.DataFrame(
        {"market": ["BTC"] * 25, "time": stamps, "funding": [0.0000125] * 24 + [9.0], "premium": [0.001] * 24 + [9.0]}
    )
    out = native.funding_features(funding, pl.DataFrame({"market": ["BTC"], "cutoff": [c]}))
    row = out.row(0, named=True)
    assert row["funding_24h"] == pytest.approx(0.0000125)
    assert row["premium_24h"] == pytest.approx(0.001)
    assert row["pinned_share_24h"] == pytest.approx(1.0)


def markets_panel(days, oi):
    rows = []
    for d, o in zip(days, oi, strict=True):
        rows.append(
            {
                "date": d,
                "market": "BTC",
                "dex": "main",
                "oi_contracts": float(o),
                "oi_notional": float(o) * 100,
                "capture_ms": ms(datetime(d.year, d.month, d.day, 0, 5, tzinfo=UTC)),
            }
        )
    return pl.DataFrame(rows)


def test_open_interest_proxies_use_the_previous_and_eighth_previous_snapshots():
    days = [date(2026, 3, 1) + timedelta(days=i) for i in range(10)]
    m = markets_panel(days, [100, 110, 120, 130, 140, 150, 160, 170, 180, 999])  # day 9's own OI must never be used
    out = native.open_interest_features(m, [days[9]])
    row = out.row(0, named=True)
    assert row["oi_change_7d"] == pytest.approx(math.log(180 / 110))  # OI(D-1) / OI(D-8)
    assert row["oi_prev_notional"] == pytest.approx(18_000)


def test_momentum_and_realised_volatility_from_hourly_prices():
    start = datetime(2026, 3, 1, tzinfo=UTC)
    closes = [100 * math.exp(0.001 * i) for i in range(24 * 10)]
    h = hourly("BTC", start, closes)
    c = start + timedelta(days=9)
    out = native.price_features(h, pl.DataFrame({"market": ["BTC"], "cutoff": [c]}))
    row = out.row(0, named=True)
    assert row["momentum_7d"] == pytest.approx(0.001 * 24 * 7)
    assert row["rv_7d"] == pytest.approx(0.0, abs=1e-9)  # constant hourly returns have no dispersion


def test_liquidation_and_volume_windows_cover_the_seven_days_before_the_cutoff():
    c = datetime(2026, 3, 10, tzinfo=UTC)
    liq = pl.DataFrame(
        {
            "market": ["BTC"] * 3,
            "hour": [c - timedelta(days=8), c - timedelta(days=2), c],
            "liq_ntl": [1_000.0, 50.0, 7_000.0],
            "liq_long_ntl": [0.0, 50.0, 0.0],
            "liq_short_ntl": [1_000.0, 0.0, 7_000.0],
        }
    )
    vol = hourly("BTC", c - timedelta(days=7), [1.0] * (24 * 7), volume=10.0)
    out = native.flow_features(liq, vol, pl.DataFrame({"market": ["BTC"], "cutoff": [c], "oi_prev_notional": [500.0]}))
    row = out.row(0, named=True)
    assert row["liq_intensity_7d"] == pytest.approx(50.0 / 500.0)  # only the liquidation inside the window
    assert row["volume_7d_mean"] == pytest.approx(240.0)  # 24 hours x $10 a day
    assert row["oi_to_volume_7d"] == pytest.approx(500.0 / 240.0)


def test_window_coverage_counts_only_archived_days():
    c = datetime(2026, 3, 10, tzinfo=UTC)
    every_day = [date(2026, 3, d) for d in range(3, 10)]
    full = native.window_coverage([c], every_day).row(0, named=True)
    assert full["coverage"] == pytest.approx(1.0)
    gap = native.window_coverage([c], [d for d in every_day if d != date(2026, 3, 6)]).row(0, named=True)
    assert gap["coverage"] == pytest.approx(6 / 7)
    late = datetime(2026, 3, 10, 20, tzinfo=UTC)  # window [03-03 20:00, 03-10 20:00) spans eight dates
    part = native.window_coverage([late], every_day).row(0, named=True)
    assert part["coverage"] == pytest.approx((7 * 24 - 20) / (7 * 24))  # 03-10 00:00-20:00 is not archived


def test_flow_windows_report_their_coverage_and_an_archived_day_volume_mean():
    c = datetime(2026, 3, 10, tzinfo=UTC)
    archived = [date(2026, 3, d) for d in range(3, 10) if d != 6]
    vol = pl.concat(
        [hourly("BTC", datetime(2026, 3, d, tzinfo=UTC), [1.0] * 24, volume=10.0) for d in range(3, 10) if d != 6]
    )
    liq = pl.DataFrame(
        schema={
            "market": pl.String,
            "hour": pl.Datetime("us", "UTC"),
            "liq_ntl": pl.Float64,
            "liq_long_ntl": pl.Float64,
            "liq_short_ntl": pl.Float64,
        }
    )
    q = pl.DataFrame({"market": ["BTC"], "cutoff": [c], "oi_prev_notional": [500.0]})
    row = native.flow_features(liq, vol, q, fill_days=archived).row(0, named=True)
    assert row["volume_7d_mean"] == pytest.approx(240.0 * 6 / 7)  # registered: the missing day counts as zero
    assert row["volume_7d_archived_mean"] == pytest.approx(240.0)  # mean over the archived days
    assert row["flow_coverage_7d"] == pytest.approx(6 / 7)
    assert row["flows_complete_7d"] is False
    assert "flows_complete_7d" not in native.flow_features(liq, vol, q).columns


def test_window_edges_include_the_first_and_last_bar_and_nothing_outside():
    c = datetime(2026, 3, 10, tzinfo=UTC)
    hours = [c - timedelta(days=7) - timedelta(hours=1), c - timedelta(days=7), c - timedelta(hours=1), c]
    liq = pl.DataFrame(
        {
            "market": ["BTC"] * 4,
            "hour": hours,
            "liq_ntl": [1.0, 10.0, 100.0, 1000.0],
            "liq_long_ntl": [0.0] * 4,
            "liq_short_ntl": [0.0] * 4,
        }
    )
    vol = hourly("BTC", c - timedelta(days=8), [1.0] * (24 * 9), volume=1.0)
    out = native.flow_features(liq, vol, pl.DataFrame({"market": ["BTC"], "cutoff": [c], "oi_prev_notional": [1.0]}))
    assert out.row(0, named=True)["liq_intensity_7d"] == pytest.approx(110.0)  # bars at c-7d and c-1h only


def test_comovement_recovers_a_market_that_moves_with_the_rest():
    rng = np.random.default_rng(0)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    common = np.cumsum(rng.normal(0, 0.01, 24 * 45))
    frames = []
    names = ["A", "B", "C", "E", "F", "D"]  # five share the common factor; D adds large noise of its own
    for name in names:
        own = np.cumsum(rng.normal(0, 0.002 if name != "D" else 0.02, 24 * 45))
        frames.append(hourly(name, start, (100 * np.exp(common + own)).tolist()))
    h = pl.concat(frames)
    c = start + timedelta(days=40)
    q = pl.DataFrame({"market": names, "cutoff": [c] * len(names)})
    out = native.comovement(h, q, dict.fromkeys(names, "crypto"), window=30, min_returns=20)
    by = {r["market"]: r["comovement_30d"] for r in out.iter_rows(named=True)}
    assert by["A"] > 0.8
    assert by["B"] > 0.8
    assert by["D"] < by["A"]  # D is mostly its own noise

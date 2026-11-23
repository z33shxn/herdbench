from datetime import date, datetime, timedelta, timezone

import polars as pl
import pytest

from herdbench.truth import market_aggregates as liquidations
from herdbench.truth import mm, style

DAY = date(2026, 3, 1)


def fills(rows):
    """rows: (address, coin, asset_class, price, size, side, crossed, is_liquidation)."""
    ts = datetime(2026, 3, 1, 12, tzinfo=timezone.utc)
    return pl.DataFrame(
        {
            "address": [r[0] for r in rows],
            "coin": [r[1] for r in rows],
            "asset_class": [r[2] for r in rows],
            "price": [float(r[3]) for r in rows],
            "size": [float(r[4]) for r in rows],
            "side": [r[5] for r in rows],
            "crossed": [r[6] for r in rows],
            "is_liquidation": [r[7] for r in rows],
            "timestamp": [ts] * len(rows),
        }
    )


def test_daily_activity_splits_maker_and_taker_notional_for_perps_only():
    f = fills(
        [
            ("a", "BTC", "perp", 10, 2, "buy", False, False),
            ("a", "BTC", "perp", 5, 2, "sell", True, False),
            ("a", "PURR/USDC", "spot", 100, 100, "buy", False, False),
        ]
    )
    out = mm.daily_activity(f, DAY).row(0, named=True)
    assert out == {"address": "a", "day": DAY, "maker_ntl": 20.0, "taker_ntl": 10.0, "n_fills": 2}


def window(rows):
    """rows: (entity, day_offset, maker_ntl, taker_ntl)."""
    return pl.DataFrame(
        {
            "entity": [r[0] for r in rows],
            "day": [DAY - timedelta(days=r[1]) for r in rows],
            "maker_ntl": [float(r[2]) for r in rows],
            "taker_ntl": [float(r[3]) for r in rows],
        }
    )


def test_entity_activity_sums_sub_accounts():
    act = pl.DataFrame(
        {
            "address": ["s1", "s2", "x"],
            "day": [DAY] * 3,
            "maker_ntl": [10.0, 5.0, 1.0],
            "taker_ntl": [0.0, 1.0, 2.0],
            "n_fills": [1, 1, 1],
        }
    )
    out = mm.entity_activity(act, {"s1": "E", "s2": "E"}).sort("entity")
    assert out.row(0, named=True) == {"entity": "E", "day": DAY, "maker_ntl": 15.0, "taker_ntl": 1.0}


def test_market_maker_rule_needs_share_size_activity_and_turnover():
    rows = []
    for d in range(1, 13):
        rows += [
            ("mm", d, 850_000, 150_000),
            ("too_taker", d, 790_000, 210_000),
            ("too_small", d, 680_000, 70_000),
            ("whale", d, 850_000, 150_000),
        ]
    for d in range(1, 10):
        rows += [("short_lived", d, 1_200_000, 100_000)]
    gross = pl.DataFrame({"entity": ["mm", "whale"], "mean_gross": [100_000.0, 2_000_000.0]})
    flagged = mm.market_makers(
        window(rows),
        maker_share=0.80,
        min_notional=10_000_000,
        min_active_days=10,
        mean_gross=gross,
        turnover_multiple=50,
    )
    # mm trades 12M against a 0.1M book (120x); the passive whale trades 12M against a 2M book (6x)
    assert flagged == {"mm"}


def test_market_maker_set_is_unknown_when_the_window_lacks_fill_days():
    activity = window([("x", d, 1e6, 0) for d in range(1, 15)])
    available = {DAY - timedelta(days=d) for d in range(1, 15)}
    assert mm.set_for(activity, available, asof=DAY) is None
    available.add(DAY - timedelta(days=20))
    assert mm.set_for(activity, available, asof=DAY) is not None


def test_carry_forward_reuses_the_last_set_for_at_most_fifteen_days():
    known = {date(2026, 3, 1): {"a"}}
    assert mm.carry_forward(known, date(2026, 3, 16), max_days=15) == {"a"}
    assert mm.carry_forward(known, date(2026, 3, 17), max_days=15) is None
    assert mm.carry_forward({}, date(2026, 3, 2), max_days=15) is None


def test_liquidations_count_only_the_liquidated_party_and_split_by_side():
    f = fills(
        [
            ("victim", "BTC", "perp", 10, 3, "sell", True, True),  # long liquidated: 30
            ("victim2", "BTC", "perp", 5, 2, "buy", False, True),  # short liquidated: 10
            ("maker", "BTC", "perp", 10, 3, "buy", False, False),  # counterparty: ignored
            ("v3", "ETH", "perp", 1, 7, "sell", True, True),
            ("spot", "PURR/USDC", "spot", 1, 1, "sell", True, True),
        ]
    )
    out = liquidations.daily_liquidations(f, DAY).sort("market")
    btc = out.filter(pl.col("market") == "BTC").row(0, named=True)
    assert btc["liq_ntl"] == 40.0
    assert btc["liq_long_ntl"] == 30.0
    assert btc["liq_short_ntl"] == 10.0
    assert out["market"].to_list() == ["BTC", "ETH"]


def test_daily_volume_counts_each_trade_once_from_the_taker_side():
    f = fills([("t", "BTC", "perp", 10, 3, "buy", True, False), ("m", "BTC", "perp", 10, 3, "sell", False, False)])
    row = liquidations.daily_volume(f, DAY).row(0, named=True)
    assert row["volume_ntl"] == 30.0
    assert row["maker_ntl"] == 30.0


def test_hourly_bars_use_each_trade_once_and_close_on_the_last_trade():
    base = datetime(2026, 3, 1, 10, tzinfo=timezone.utc)
    f = pl.DataFrame(
        {
            "coin": ["BTC"] * 5,
            "asset_class": ["perp"] * 5,
            "crossed": [True, False, True, True, True],
            "price": [100.0, 999.0, 103.0, 99.0, 110.0],
            "size": [1.0, 1.0, 2.0, 1.0, 1.0],
            "timestamp": [base + timedelta(minutes=m) for m in (1, 2, 30, 59, 61)],
        }
    )
    bars = liquidations.hourly_bars(f).sort("hour")
    first = bars.row(0, named=True)
    assert first["hour"] == base  # bar labelled by its start
    assert (first["open"], first["high"], first["low"], first["close"]) == (100.0, 103.0, 99.0, 99.0)
    assert first["volume_ntl"] == 100.0 + 206.0 + 99.0  # the maker row (999) is not a second trade
    assert bars.row(1, named=True)["close"] == 110.0


def test_hourly_liquidations_split_by_side_and_hour():
    base = datetime(2026, 3, 1, 10, tzinfo=timezone.utc)
    f = pl.DataFrame(
        {
            "coin": ["BTC", "BTC", "BTC", "BTC"],
            "asset_class": ["perp"] * 4,
            "is_liquidation": [True, True, False, True],
            "side": ["sell", "buy", "sell", "sell"],
            "price": [10.0, 5.0, 99.0, 2.0],
            "size": [3.0, 2.0, 1.0, 1.0],
            "timestamp": [base + timedelta(minutes=m) for m in (5, 20, 30, 70)],
        }
    )
    out = liquidations.hourly_liquidations(f).sort("hour")
    assert out["hour"].to_list() == [base, base + timedelta(hours=1)]
    first = out.row(0, named=True)
    assert (first["liq_ntl"], first["liq_long_ntl"], first["liq_short_ntl"]) == (40.0, 30.0, 10.0)
    assert out.row(1, named=True)["liq_long_ntl"] == 2.0


def snap(day, rows):
    """rows: (user, market, size); every market's mark is given separately."""
    return pl.DataFrame(
        {
            "date": [day] * len(rows),
            "user": [r[0] for r in rows],
            "market": [r[1] for r in rows],
            "size": [float(r[2]) for r in rows],
        }
    )


def marks(rows):
    return pl.DataFrame(
        {"date": [r[0] for r in rows], "market": [r[1] for r in rows], "mark": [float(r[2]) for r in rows]}
    )


def test_trend_contributions_score_a_buyer_after_up_days_as_plus_one():
    d0, d1, d2, d3 = (date(2026, 3, i) for i in (1, 2, 3, 4))
    s = pl.concat(
        [
            snap(d0, [("w", "BTC", 1)]),
            snap(d1, [("w", "BTC", 1)]),
            snap(d2, [("w", "BTC", 2)]),
            snap(d3, [("w", "BTC", 3)]),
        ]
    )
    m = marks([(d0, "BTC", 100), (d1, "BTC", 110), (d2, "BTC", 121), (d3, "BTC", 133.1)])
    contrib = style.contributions(s, m)
    # d2: flow +1 * 121 after the d0->d1 rise; d3: flow +1 * 133.1 after the d1->d2 rise
    assert contrib.filter(pl.col("user") == "w")["n_flows"].sum() == 2
    score = style.trend_scores(contrib, asof=date(2026, 3, 5), window_days=30, min_flows=1)
    assert score.row(0, named=True)["score"] == pytest.approx(1.0)


def test_no_flow_is_computed_across_a_missing_snapshot_day():
    d0, d1, d3 = date(2026, 3, 1), date(2026, 3, 2), date(2026, 3, 4)  # 2026-03-03 is missing
    s = pl.concat([snap(d0, [("w", "BTC", 1)]), snap(d1, [("w", "BTC", 1)]), snap(d3, [("w", "BTC", 5)])])
    m = marks([(d0, "BTC", 100), (d1, "BTC", 110), (d3, "BTC", 120)])
    assert style.contributions(s, m).height == 0


def test_trend_score_needs_the_minimum_number_of_flows():
    contrib = pl.DataFrame(
        {
            "user": ["w"] * 4,
            "date": [date(2026, 3, i) for i in range(1, 5)],
            "c": [1.0] * 4,
            "a": [1.0] * 4,
            "n_flows": [1] * 4,
        }
    )
    assert style.trend_scores(contrib, asof=date(2026, 3, 10), window_days=30, min_flows=5).height == 0
    assert style.trend_scores(contrib, asof=date(2026, 3, 10), window_days=30, min_flows=4).height == 1


def test_trend_score_ignores_flows_on_or_after_the_snapshot_it_classifies():
    contrib = pl.DataFrame(
        {
            "user": ["w", "w"],
            "date": [date(2026, 3, 1), date(2026, 3, 10)],
            "c": [1.0, -100.0],
            "a": [1.0, 100.0],
            "n_flows": [5, 5],
        }
    )
    out = style.trend_scores(contrib, asof=date(2026, 3, 10), window_days=30, min_flows=5)
    assert out.row(0, named=True)["score"] == pytest.approx(1.0)

from datetime import datetime, timezone

import polars as pl
import pytest

from herdbench.gates import reconcile

T0 = datetime(2026, 3, 1, 0, 5, tzinfo=timezone.utc)
T1 = datetime(2026, 3, 2, 0, 6, tzinfo=timezone.utc)


def snap(rows):
    return pl.DataFrame({"user": [r[0] for r in rows], "market": [r[1] for r in rows],
                         "size": [float(r[2]) for r in rows]})


def fills(rows):
    """rows: (address, coin, side, size, timestamp)."""
    return pl.DataFrame({"address": [r[0] for r in rows], "coin": [r[1] for r in rows],
                         "asset_class": ["perp"] * len(rows), "side": [r[2] for r in rows],
                         "size": [float(r[3]) for r in rows], "timestamp": [r[4] for r in rows]})


MID = datetime(2026, 3, 1, 12, tzinfo=timezone.utc)
MARKS = pl.DataFrame({"market": ["BTC", "ETH"], "mark": [100.0, 10.0]})


def test_position_changes_that_match_fills_reconcile():
    prev = snap([("a", "BTC", 1.0), ("b", "BTC", -2.0)])
    cur = snap([("a", "BTC", 3.0), ("c", "ETH", -5.0)])  # b closed, c opened
    f = fills([("a", "BTC", "buy", 2.0, MID), ("b", "BTC", "buy", 2.0, MID), ("c", "ETH", "sell", 5.0, MID)])
    out = reconcile.pair(prev, cur, f, T0, T1, MARKS)
    assert out["reconciled_share"] == pytest.approx(1.0)
    assert out["n_changes"] == 3


def test_fills_outside_the_capture_window_do_not_count():
    prev = snap([("a", "BTC", 1.0)])
    cur = snap([("a", "BTC", 2.0)])
    early = datetime(2026, 3, 1, 0, 1, tzinfo=timezone.utc)  # before the first capture: already in prev
    f = fills([("a", "BTC", "buy", 5.0, early), ("a", "BTC", "buy", 1.0, MID)])
    assert reconcile.pair(prev, cur, f, T0, T1, MARKS)["reconciled_share"] == pytest.approx(1.0)


def test_a_missing_fill_is_reported_weighted_by_notional():
    prev = snap([("a", "BTC", 1.0), ("b", "ETH", 0.0)])
    cur = snap([("a", "BTC", 2.0), ("b", "ETH", 4.0)])
    f = fills([("a", "BTC", "buy", 1.0, MID)])  # b's 4 ETH (notional 40) has no fill
    out = reconcile.pair(prev, cur, f, T0, T1, MARKS)
    assert out["reconciled_share"] == pytest.approx(100 / (100 + 40))
    assert out["n_mismatched"] == 1


def test_fills_without_any_position_change_count_as_mismatch():
    prev = snap([("a", "BTC", 1.0)])
    cur = snap([("a", "BTC", 1.0)])
    f = fills([("a", "BTC", "buy", 3.0, MID)])  # net +3 but the position did not move
    out = reconcile.pair(prev, cur, f, T0, T1, MARKS)
    assert out["reconciled_share"] == pytest.approx(0.0)


def test_shares_are_reported_separately_for_a_given_group():
    prev = snap([("mm", "BTC", 0.0), ("x", "BTC", 0.0)])
    cur = snap([("mm", "BTC", 1.0), ("x", "BTC", 1.0)])
    f = fills([("mm", "BTC", "buy", 1.0, MID)])
    out = reconcile.pair(prev, cur, f, T0, T1, MARKS, group={"mm"})
    assert out["reconciled_share_group"] == pytest.approx(1.0)
    assert out["reconciled_share_rest"] == pytest.approx(0.0)


def test_maker_taker_balance_per_market():
    f = pl.DataFrame({"coin": ["BTC", "BTC", "ETH", "ETH"], "asset_class": ["perp"] * 4, "crossed": [True, False, True, False],
                      "price": [100.0, 100.0, 10.0, 10.0], "size": [1.0, 1.0, 2.0, 1.0]})
    out = reconcile.maker_taker_balance(f).sort("market")
    assert out["gap"].to_list() == pytest.approx([0.0, (20 - 10) / 20])

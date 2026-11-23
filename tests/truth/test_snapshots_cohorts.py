from datetime import date

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from herdbench.config import PREREG
from herdbench.truth import cohorts, snapshots

HLP_CHILD = "0x010461c14e146ac35fe42271bdc1134ee31c703a"
LIQUIDATOR = "0x4000000000000000000000000000000000000001"


def write_snapshot(path, rows):
    cols = [
        "user",
        "market",
        "size",
        "notional",
        "entry_price",
        "liquidation_price",
        "leverage_type",
        "leverage",
        "funding_pnl",
        "account_value",
        "account_mode",
    ]
    table = pa.table({c: [r.get(c) for r in rows] for c in cols})
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)


def pos(user, market, size, notional, av=1000.0, liq=None, lev=3):
    return {
        "user": user,
        "market": market,
        "size": size,
        "notional": notional,
        "entry_price": 1.0,
        "liquidation_price": liq,
        "leverage_type": "cross",
        "leverage": lev,
        "funding_pnl": 0.0,
        "account_value": av,
        "account_mode": None,
    }


def test_index_picks_the_earliest_file_of_each_date(tmp_path):
    root = tmp_path / "perp_archive"
    write_snapshot(root / "date=2026-03-24" / "933720000_1774346166351.parquet", [pos("a", "BTC", 1, 1)])
    write_snapshot(root / "date=2026-03-24" / "933300000_1774310589265.parquet", [pos("a", "BTC", 1, 1)])
    write_snapshot(root / "date=2026-03-25" / "934000000_1774396800000.parquet", [pos("a", "BTC", 1, 1)])
    index = snapshots.local_index(tmp_path)
    assert list(index) == [date(2026, 3, 24), date(2026, 3, 25)]
    assert index[date(2026, 3, 24)].name == "933300000_1774310589265.parquet"


def test_read_removes_protocol_accounts_but_market_table_keeps_them_in_open_interest(tmp_path):
    p = tmp_path / "perp_archive" / "date=2026-03-02" / "1_1772410379439.parquet"
    write_snapshot(
        p,
        [
            pos("0xaaa", "BTC", 2.0, 200.0),
            pos(HLP_CHILD, "BTC", -1.0, 100.0),
            pos(LIQUIDATOR, "BTC", -1.0, 100.0),
            pos("0xbbb", "xyz:NVDA", -4.0, 800.0),
            pos("0xccc", "xyz:NVDA", 4.0, 800.0),
        ],
    )
    raw = snapshots.read_raw(p)
    market = snapshots.market_table(raw).sort("market")
    btc = market.filter(pl.col("market") == "BTC").row(0, named=True)
    assert btc["oi_notional"] == 200.0
    assert btc["mark"] == pytest.approx(100.0)
    assert btc["long_short_gap"] == pytest.approx(0.0)
    assert market.filter(pl.col("market") == "xyz:NVDA")["dex"].to_list() == ["xyz"]
    assert btc["dex"] == "main"
    assert set(snapshots.without_protocol(raw)["user"].to_list()) == {"0xaaa", "0xbbb", "0xccc"}


def test_entity_account_values_take_each_address_maximum_then_sum_across_addresses():
    raw = pl.DataFrame(
        {"user": ["a", "a", "b"], "market": ["BTC", "xyz:NVDA", "BTC"], "account_value": [500.0, 700.0, 300.0]}
    )
    out = snapshots.entity_account_values(raw, {"a": "E", "b": "E"})
    assert out.row(0, named=True) == {"entity": "E", "av": 1000.0}  # max(500, 700) + 300


def holdings(n, market="xyz:NVDA"):
    return pl.DataFrame(
        {
            "market": [market] * n,
            "entity": [f"e{i:03d}" for i in range(n)],
            "notional": [10.0 + i for i in range(n)],
            "is_long": [bool(i % 2) for i in range(n)],
        }
    )


def av(n, missing=()):
    return pl.DataFrame(
        {
            "entity": [f"e{i:03d}" for i in range(n) if i not in missing],
            "av": [float(1000 - i) for i in range(n) if i not in missing],
        }
    )


@pytest.mark.parametrize(("n", "k"), [(10, 2), (11, 3), (100, 20), (1, 1)])
def test_top_av_selects_the_top_fifth_of_rankable_non_mm_holders_by_lagged_account_value(n, k):
    out = cohorts.assign(holdings(n), mm=set(), mm_robust={}, trend=None, lagged_av=av(n))
    assert set(out.filter(pl.col("top_av"))["entity"].to_list()) == {f"e{i:03d}" for i in range(k)}


def test_top_av_excludes_market_makers_and_entities_without_a_lagged_account_value():
    out = cohorts.assign(holdings(10), mm={"e000"}, mm_robust={}, trend=None, lagged_av=av(10, missing=(1,)))
    # e000 is a market maker, e001 has no lagged value: 8 rankable non_mm holders -> ceil(1.6) = 2
    assert set(out.filter(pl.col("top_av"))["entity"].to_list()) == {"e002", "e003"}


def test_top_av_breaks_account_value_ties_by_entity():
    tie = av(5).with_columns(pl.lit(500.0).alias("av"))
    out = cohorts.assign(holdings(5), mm=set(), mm_robust={}, trend=None, lagged_av=tie)
    assert out.filter(pl.col("top_av"))["entity"].to_list() == ["e000"]


def test_non_mm_drops_market_makers_and_is_undefined_without_a_known_set():
    out = cohorts.assign(holdings(4), mm={"e001"}, mm_robust={}, trend=None, lagged_av=av(4))
    assert out.filter(pl.col("non_mm"))["entity"].to_list() == ["e000", "e002", "e003"]
    assert out["all"].all()
    unknown = cohorts.assign(holdings(4), mm=None, mm_robust={}, trend=None, lagged_av=av(4))
    assert not unknown["non_mm"].any()
    assert not unknown["top_av"].any()


def test_style_cohorts_are_drawn_from_non_mm_and_follow_the_threshold():
    scores = pl.DataFrame({"entity": ["e000", "e001", "e002", "e003"], "score": [0.2, 0.19, -0.2, -0.5]})
    out = cohorts.assign(holdings(4), mm={"e003"}, mm_robust={}, trend=scores, lagged_av=av(4))
    assert out.filter(pl.col("trend"))["entity"].to_list() == ["e000"]
    assert out.filter(pl.col("contrarian"))["entity"].to_list() == ["e002"]  # e003 is a market maker


def test_robustness_market_maker_cohorts_use_their_own_sets():
    out = cohorts.assign(
        holdings(3), mm=set(), mm_robust={"non_mm_s070": {"e000"}, "non_mm_s090": set()}, trend=None, lagged_av=av(3)
    )
    assert out.filter(pl.col("non_mm_s070"))["entity"].to_list() == ["e001", "e002"]
    assert out["non_mm_s090"].all()
    assert not out["non_mm_t020"].any()  # a robustness set that was not supplied is unknown, so empty


def test_protocol_check_covers_hlp_family_and_backstop_liquidators():
    assert PREREG.is_protocol(HLP_CHILD)
    assert PREREG.is_protocol(LIQUIDATOR)
    assert PREREG.is_protocol("0x40000000000000000000000000000000000000ff")
    assert not PREREG.is_protocol("0x4000000000000000000000000000000000001000")
    assert not PREREG.is_protocol("0x0000000000000000000000000000000000000001")

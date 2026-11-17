from datetime import date

import polars as pl
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from herdbench.truth.metrics import cell_metrics, coheld_flags, entity_positions
from herdbench.truth.privacy import apply_complement_rule, apply_disclosure, publish_rounding, published

D = date(2026, 3, 2)


def holders(rows, market="BTC", cohort="non_mm"):
    """rows: (entity, signed_notional, weight, gross_addr, fragile_ntl, coheld)."""
    return pl.DataFrame({
        "date": [D] * len(rows), "market": [market] * len(rows), "cohort": [cohort] * len(rows),
        "entity": [r[0] for r in rows], "notional": [abs(float(r[1])) for r in rows],
        "is_long": [r[1] > 0 for r in rows], "w": [float(r[2]) for r in rows],
        "gross_addr": [float(r[3]) for r in rows], "fragile_ntl": [float(r[4]) for r in rows],
        "coheld": [bool(r[5]) for r in rows],
    })


def test_entity_positions_net_sub_accounts_and_keep_address_level_fragility():
    pos = pl.DataFrame({
        "market": ["BTC", "BTC", "BTC"], "user": ["s1", "s2", "x"], "entity": ["E", "E", "x"],
        "size": [1.0, -0.4, -2.0], "notional": [100.0, 40.0, 200.0],
        "liquidation_price": [95.0, None, 150.0], "mark": [100.0] * 3})
    out = entity_positions(pos, fragile_distance=0.10).sort("entity")
    e = out.filter(pl.col("entity") == "E").row(0, named=True)
    assert e["notional"] == pytest.approx(60.0)
    assert e["is_long"]
    assert e["gross_addr"] == pytest.approx(140.0)
    assert e["fragile_ntl"] == pytest.approx(100.0)  # s1 is 5% from liquidation
    assert e["n_addr"] == 2
    assert out.filter(pl.col("entity") == "x")["is_long"].to_list() == [False]


def test_fully_hedged_entities_hold_no_net_position():
    pos = pl.DataFrame({"market": ["BTC", "BTC"], "user": ["s1", "s2"], "entity": ["E", "E"], "size": [1.0, -1.0],
                        "notional": [100.0, 100.0], "liquidation_price": [None, None], "mark": [100.0, 100.0]})
    assert entity_positions(pos, fragile_distance=0.10).height == 0


def test_hand_computed_cell():
    out = cell_metrics(holders([("u1", 100, 2, 100, 0, True), ("u2", 50, 10, 50, 50, False),
                                ("u3", -150, 1, 150, 0, True)]))
    row = out.row(0, named=True)
    assert row["n_entities"] == 3
    assert row["gross_notional"] == 300
    assert row["net_notional"] == pytest.approx(0.0)
    assert row["ni"] == pytest.approx(0.0)
    assert row["ai"] == pytest.approx(1 / 3)
    assert row["lwi"] == pytest.approx(550 / 850)
    assert row["top10_share"] == pytest.approx(0.5)
    assert row["top2_share"] == pytest.approx(250 / 300)
    assert row["fragile_share"] == pytest.approx(50 / 300)
    assert row["coheld_share"] == pytest.approx(250 / 300)


def test_account_imbalance_is_negative_when_shorts_outnumber_longs():
    row = cell_metrics(holders([("u1", 300, 1, 300, 0, 0), ("u2", -100, 1, 100, 0, 0), ("u3", -100, 1, 100, 0, 0),
                                ("u4", -100, 1, 100, 0, 0)])).row(0, named=True)
    assert row["ai"] == pytest.approx(-0.5)
    assert row["ni"] == pytest.approx(0.0)


def test_top_share_uses_exact_ceiling_without_float_noise():
    rows = [(f"u{i}", (100 + i) * (1 if i % 2 else -1), 1, 100 + i, 0, 0) for i in range(30)]
    row = cell_metrics(holders(rows)).row(0, named=True)
    assert row["top10_share"] == pytest.approx((129 + 128 + 127) / sum(100 + i for i in range(30)))


def test_coheld_flags_count_same_direction_positions_in_other_eligible_markets():
    rows = pl.DataFrame({"market": ["A", "B", "C", "D", "E", "F", "G"],
                         "entity": ["e"] * 7, "is_long": [True] * 6 + [False]})
    eligible = {"A", "B", "C", "D", "E", "F"}
    out = coheld_flags(rows, eligible, min_other=5).sort("market")
    # each long in A..F has 5 other eligible longs; the short in G has none
    assert out["coheld"].to_list() == [True] * 6 + [False]
    out4 = coheld_flags(rows, eligible - {"F"}, min_other=5).sort("market")
    assert not out4.filter(pl.col("market") == "A")["coheld"].item()  # only 4 other eligible longs left


notional = st.floats(min_value=1.0, max_value=1e7, allow_nan=False)
holder = st.tuples(st.booleans(), notional, st.floats(min_value=0.1, max_value=50.0), st.booleans())


@settings(max_examples=60, deadline=None)
@given(st.lists(holder, min_size=2, max_size=40), st.floats(min_value=0.01, max_value=1000.0))
def test_metrics_do_not_depend_on_scale_or_row_order(rows, scale):
    base_rows = [(f"u{i}", n if long else -n, w, n, n / 2, c) for i, (long, n, w, c) in enumerate(rows)]
    base = cell_metrics(holders(base_rows)).row(0, named=True)
    scaled = cell_metrics(holders([(e, s * scale, w, g * scale, f * scale, c) for e, s, w, g, f, c in base_rows])).row(0, named=True)
    shuffled = cell_metrics(holders(list(reversed(base_rows)))).row(0, named=True)
    for key in ("ni", "ai", "lwi", "abs_ni", "top10_share", "top2_share", "fragile_share", "coheld_share"):
        assert scaled[key] == pytest.approx(base[key], abs=1e-9)
        assert shuffled[key] == pytest.approx(base[key], abs=1e-9)
    assert -1.0 <= base["ni"] <= 1.0
    assert -1.0 <= base["ai"] <= 1.0


def cells_frame(n, top2, cohort="non_mm", market="BTC", gross=1e6):
    return pl.DataFrame({"date": [D], "market": [market], "cohort": [cohort], "n_entities": [n],
                         "gross_notional": [gross], "net_notional": [0.0], "ni": [0.1234567], "ai": [0.0], "lwi": [0.0],
                         "abs_ni": [0.1234567], "top10_share": [0.3], "top2_share": [top2], "fragile_share": [0.0],
                         "coheld_share": [0.0]})


@pytest.mark.parametrize(("n", "top2", "suppressed"), [
    (19, 0.10, True), (20, 0.10, False), (20, 0.80, False), (20, 0.81, True), (500, 0.95, True)])
def test_disclosure_rule_boundaries(n, top2, suppressed):
    assert apply_disclosure(cells_frame(n, top2))["suppressed"].to_list() == [suppressed]


def test_complement_rule_suppresses_the_parent_when_the_difference_is_too_small():
    cells = apply_disclosure(pl.concat([cells_frame(130, 0.2, "all"), cells_frame(120, 0.2, "non_mm")]))
    diff = pl.DataFrame({"date": [D], "market": ["BTC"], "parent": ["all"], "child": ["non_mm"],
                         "n_entities": [10], "top2_share": [0.9]})
    out = apply_complement_rule(cells, diff).sort("cohort")
    assert out.filter(pl.col("cohort") == "all")["suppressed"].item()
    assert not out.filter(pl.col("cohort") == "non_mm")["suppressed"].item()


def test_complement_rule_keeps_both_when_the_difference_passes():
    cells = apply_disclosure(pl.concat([cells_frame(130, 0.2, "non_mm"), cells_frame(30, 0.2, "top_av")]))
    diff = pl.DataFrame({"date": [D], "market": ["BTC"], "parent": ["non_mm"], "child": ["top_av"],
                         "n_entities": [100], "top2_share": [0.3]})
    assert not apply_complement_rule(cells, diff)["suppressed"].any()


def test_published_view_rounds_and_drops_suppressed_cells_and_internal_columns():
    cells = apply_disclosure(pl.concat([cells_frame(19, 0.1), cells_frame(47, 0.2, gross=1_234_567.0)]))
    out = publish_rounding(published(cells))
    assert out.height == 1
    row = out.row(0, named=True)
    assert row["n_entities"] == 40          # floored to a multiple of 10
    assert row["gross_notional"] == 1_200_000.0  # two significant figures
    assert row["ni"] == 0.123               # three decimals
    assert "top2_share" not in out.columns
    assert "suppressed" not in out.columns

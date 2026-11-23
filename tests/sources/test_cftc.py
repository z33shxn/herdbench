import json
from pathlib import Path

import pandas as pd
import pytest

from herdbench.sources import cftc

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_disaggregated_rows_give_managed_money_net_share():
    df = cftc.parse_disaggregated(json.loads((FIXTURES / "cftc_disagg_gold.json").read_text()))
    first = df.iloc[0]
    assert first["report_date"] == pd.Timestamp("2026-01-06")
    assert first["open_interest"] == 488116
    assert first["long"] == 146445
    assert first["short"] == 23232
    assert first["net_share"] == pytest.approx((146445 - 23232) / 488116)
    assert df["report_date"].is_monotonic_increasing


def test_tff_rows_give_leveraged_funds_net_share():
    df = cftc.parse_tff(json.loads((FIXTURES / "cftc_tff_es.json").read_text()))
    first = df.iloc[0]
    assert first["report_date"] == pd.Timestamp("2026-01-06")
    assert first["open_interest"] == 1945513
    assert first["net_share"] == pytest.approx((128725 - 544629) / 1945513)


def test_tff_rows_also_give_asset_manager_net_share():
    df = cftc.parse_tff(json.loads((FIXTURES / "cftc_tff_es.json").read_text()))
    assert df.iloc[0]["asset_net_share"] == pytest.approx((1174764 - 213425) / 1945513)


def test_bridge_table_covers_metals_energy_indices_and_currencies():
    codes = {m: spec.code for m, spec in cftc.BRIDGE.items()}
    assert codes["xyz:GOLD"] == "088691"
    assert codes["xyz:SILVER"] == "084691"
    assert codes["xyz:SP500"] == "13874A"
    assert codes["xyz:EUR"] == "099741"
    assert cftc.BRIDGE["xyz:SP500"].report == "tff"
    assert cftc.BRIDGE["xyz:GOLD"].report == "disaggregated"

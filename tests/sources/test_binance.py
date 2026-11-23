import io
import random
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from herdbench._http import HttpClient
from herdbench.sources import binance
from tests.utils.server import Routes, serve

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def metrics_text() -> str:
    return (FIXTURES / "binance_metrics_BTCUSDT_2025-12-15.csv").read_text()


def test_metrics_url_follows_the_vision_layout():
    assert binance.metrics_url("BTCUSDT", date(2025, 12, 15)) == (
        "https://data.binance.vision/data/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-2025-12-15.zip"
    )
    assert binance.funding_url("BTCUSDT", 2025, 12) == (
        "https://data.binance.vision/data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2025-12.zip"
    )


def test_parse_metrics_sorts_unsorted_rows_and_types_every_column():
    header, *rows = metrics_text().splitlines()
    random.Random(7).shuffle(rows)  # Vision files are not always in time order
    df = binance.parse_metrics("\n".join([header, *rows]))
    assert len(df) == 288
    assert df["create_time"].is_monotonic_increasing
    first = df.iloc[0]
    assert first["create_time"] == pd.Timestamp("2025-12-15 00:00:00", tz="UTC")
    assert first["sum_open_interest"] == pytest.approx(88584.422)
    assert first["count_toptrader_long_short_ratio"] == pytest.approx(2.78343406)
    assert first["sum_toptrader_long_short_ratio"] == pytest.approx(2.27667)
    assert first["count_long_short_ratio"] == pytest.approx(2.37012903)
    assert first["sum_taker_long_short_vol_ratio"] == pytest.approx(1.831278)


def test_parse_funding_reads_rates_and_intervals():
    df = binance.parse_funding((FIXTURES / "binance_funding_BTCUSDT_2025-12.csv").read_text())
    assert list(df.columns) == ["time", "interval_hours", "funding"]
    assert df["time"].iloc[0] == pd.Timestamp("2025-12-01 00:00:00", tz="UTC")
    assert set(df["interval_hours"]) == {8}
    assert df["time"].is_monotonic_increasing


def test_parse_klines_reads_taker_volumes_from_the_header_file():
    df = binance.parse_klines((FIXTURES / "binance_klines_5m_BTCUSDT_2025-12_head.csv").read_text())
    first = df.iloc[0]
    assert first["open_time"] == pd.Timestamp(1764547200000, unit="ms", tz="UTC")
    assert first["volume"] == pytest.approx(5804.585)
    assert first["taker_buy_volume"] == pytest.approx(2043.971)
    assert first["taker_sell_volume"] == pytest.approx(5804.585 - 2043.971)
    assert len(df) == 12
    assert binance.klines_url("BTCUSDT", "5m", 2025, 12) == (
        "https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/5m/BTCUSDT-5m-2025-12.zip"
    )


def test_binance_symbol_maps_the_thousand_prefix():
    assert binance.binance_symbol("BTC") == "BTCUSDT"
    assert binance.binance_symbol("kPEPE") == "1000PEPEUSDT"
    assert binance.binance_symbol("kBONK") == "1000BONKUSDT"


def _zip(name: str, text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, text)
    return buf.getvalue()


def test_vision_downloads_a_day_and_returns_none_when_the_day_is_missing(tmp_path):
    day = date(2025, 12, 15)
    path = "/data/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-2025-12-15.zip"
    routes = Routes(files={path: _zip("BTCUSDT-metrics-2025-12-15.csv", metrics_text())})
    with serve(routes) as base:
        v = binance.Vision(base=base, http=HttpClient(retries=2, backoff=0.0), cache_dir=tmp_path)
        df = v.metrics("BTCUSDT", day)
        assert df is not None
        assert len(df) == 288
        assert v.metrics("BTCUSDT", date(2025, 12, 16)) is None
        before = len(routes.log)
        assert len(v.metrics("BTCUSDT", day)) == 288  # served from the local cache
        assert len(routes.log) == before

import json
from pathlib import Path

import pandas as pd
import pytest

from herdbench._http import HttpClient
from herdbench.sources.hyperliquid import InfoApi
from tests.utils.server import Routes, serve

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
HOUR = 3_600_000
T0 = 1_765_756_800_088  # 2025-12-15 00:00:00.088 UTC, the venue's real funding stamp offset


def funding_server(n_rows: int, page: int = 500):
    """Answer fundingHistory like the venue: rows with time >= startTime, at most `page` per reply."""
    stamps = [T0 + i * HOUR for i in range(n_rows)]

    def handle(body):
        assert body["type"] == "fundingHistory"
        start, end = body["startTime"], body.get("endTime", 10**15)
        rows = [
            {"coin": body["coin"], "fundingRate": f"{i * 1e-6:.10f}", "premium": f"{-i * 1e-6:.10f}", "time": t}
            for i, t in enumerate(stamps)
            if start <= t <= end
        ][:page]
        return 200, rows

    return Routes(post={"/info": handle}), stamps


def api(base):
    return InfoApi(url=base + "/info", http=HttpClient(retries=4, backoff=0.0, timeout=10.0))


def test_funding_history_pages_through_without_gaps_or_duplicates():
    routes, stamps = funding_server(1_203)
    with serve(routes) as base:
        df = api(base).funding_history("xyz:NVDA", T0, T0 + 2_000 * HOUR)
    assert len(df) == 1_203
    assert df["time"].is_unique
    assert df["time"].is_monotonic_increasing
    assert df["time"].iloc[0] == pd.Timestamp(T0, unit="ms", tz="UTC")
    assert df["time"].iloc[-1] == pd.Timestamp(stamps[-1], unit="ms", tz="UTC")
    assert df["funding"].iloc[2] == pytest.approx(2e-6)
    assert df["premium"].iloc[2] == pytest.approx(-2e-6)
    calls = [json.loads(h["body"]) for method, _p, h in routes.log if method == "POST"]
    assert len(calls) == 3  # 500 + 500 + 203
    assert calls[1]["startTime"] == stamps[499] + 1


def test_funding_history_stops_at_the_end_time():
    routes, _stamps = funding_server(1_000)
    with serve(routes) as base:
        df = api(base).funding_history("BTC", T0, T0 + 9 * HOUR)
    assert len(df) == 10


def test_daily_candles_parse_prices_volume_and_utc_times():
    candles = json.loads((FIXTURES / "hl_candles_btc.json").read_text())
    routes = Routes(post={"/info": lambda body: (200, candles)})
    with serve(routes) as base:
        df = api(base).daily_candles("BTC", 1_765_756_800_000, 1_766_102_400_000)
    assert list(df.columns) == ["t", "open", "high", "low", "close", "volume", "trades"]
    assert df["t"].iloc[0] == pd.Timestamp("2025-12-15", tz="UTC")
    assert df["open"].iloc[0] == 88129.0
    assert df["close"].iloc[0] == 86427.0
    assert df["high"].iloc[0] == 90007.0
    assert df["low"].iloc[0] == 85123.0
    assert df["volume"].iloc[0] == pytest.approx(45276.57871)
    assert df["trades"].iloc[0] == 568275


def test_info_calls_survive_a_rate_limit_reply():
    routes, _ = funding_server(5)
    routes.fail_first["/info"] = (2, 429)
    with serve(routes) as base:
        df = api(base).funding_history("BTC", T0, T0 + 10 * HOUR)
    assert len(df) == 5

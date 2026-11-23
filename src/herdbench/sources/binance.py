"""Binance Vision public archive: 5-minute USDT-margined futures positioning metrics and funding.

Licence: Binance Vision data is CC BY-NC-SA 4.0, which reaches derived indicators. HerdBench never
redistributes these files; statistics computed from them are published in a separate file under the
same licence.

Column mapping (verified against Binance's REST endpoints on 1,440 bars, correlation 1.0000):
``count_long_short_ratio`` = global account ratio, ``count_toptrader_long_short_ratio`` = top-trader
account ratio, ``sum_toptrader_long_short_ratio`` = top-trader position ratio,
``sum_taker_long_short_vol_ratio`` = taker buy/sell volume ratio. A Vision row stamped T equals the REST
row stamped T + 5 minutes for every series except the taker ratio.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd

from herdbench._http import HttpClient

VISION = "https://data.binance.vision"
METRIC_COLUMNS = (
    "sum_open_interest",
    "sum_open_interest_value",
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "count_long_short_ratio",
    "sum_taker_long_short_vol_ratio",
)


def metrics_url(symbol: str, day: date, base: str = VISION) -> str:
    return f"{base}/data/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{day:%Y-%m-%d}.zip"


def funding_url(symbol: str, year: int, month: int, base: str = VISION) -> str:
    return f"{base}/data/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{year:04d}-{month:02d}.zip"


def klines_url(symbol: str, interval: str, year: int, month: int, base: str = VISION) -> str:
    return f"{base}/data/futures/um/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{year:04d}-{month:02d}.zip"


def parse_klines(text: str) -> pd.DataFrame:
    """Kline rows (with or without a header line): times, volume, and taker buy and sell volume."""
    first = text.split("\n", 1)[0]
    names = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_volume",
        "count",
        "taker_buy_volume",
        "taker_buy_quote_volume",
        "ignore",
    ]
    df = pd.read_csv(io.StringIO(text), header=0 if first.startswith("open_time") else None, names=names)
    out = pd.DataFrame(
        {
            "open_time": pd.to_datetime(df["open_time"].astype("int64"), unit="ms", utc=True),
            "close_time": pd.to_datetime(df["close_time"].astype("int64"), unit="ms", utc=True),
            "volume": df["volume"].astype(float),
            "taker_buy_volume": df["taker_buy_volume"].astype(float),
        }
    )
    out["taker_sell_volume"] = out["volume"] - out["taker_buy_volume"]
    return out.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)


def binance_symbol(coin: str) -> str:
    """Hyperliquid coin name to Binance USDT-margined symbol (``kPEPE`` is 1,000 PEPE, as ``1000PEPEUSDT``)."""
    if len(coin) > 1 and coin[0] == "k" and coin[1:].isupper():
        return f"1000{coin[1:]}USDT"
    return f"{coin}USDT"


def parse_metrics(text: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text))
    out = pd.DataFrame({"create_time": pd.to_datetime(df["create_time"], format="%Y-%m-%d %H:%M:%S", utc=True)})
    for col in METRIC_COLUMNS:
        out[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
    return out.sort_values("create_time").drop_duplicates("create_time").reset_index(drop=True)


def parse_funding(text: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text))
    out = pd.DataFrame(
        {
            "time": pd.to_datetime(df["calc_time"].astype("int64"), unit="ms", utc=True),
            "interval_hours": df["funding_interval_hours"].astype("int64"),
            "funding": df["last_funding_rate"].astype(float),
        }
    )
    return out.sort_values("time").reset_index(drop=True)


class Vision:
    """Downloads Vision zip files, caching them on disk; a day Binance never published returns None."""

    def __init__(self, base: str = VISION, http: HttpClient | None = None, cache_dir: Path | None = None):
        self.base = base.rstrip("/")
        self.http = http or HttpClient(min_interval=0.1)
        self.cache_dir = Path(cache_dir) if cache_dir else None

    def _text(self, url: str) -> str | None:
        cached = self.cache_dir / url.split("/data/", 1)[1] if self.cache_dir else None
        if cached is not None and cached.exists():
            raw = cached.read_bytes()
        else:
            r = self.http.get(url)
            if r.status_code in (403, 404):
                return None
            if r.status_code != 200:
                raise RuntimeError(f"Binance Vision returned HTTP {r.status_code} for {url}")
            raw = r.content
            if cached is not None:
                cached.parent.mkdir(parents=True, exist_ok=True)
                tmp = cached.with_name(cached.name + ".part")
                tmp.write_bytes(raw)
                tmp.replace(cached)  # never leave a cut-short file that later runs would reuse
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            return z.read(z.namelist()[0]).decode("utf-8")

    def metrics(self, symbol: str, day: date) -> pd.DataFrame | None:
        text = self._text(metrics_url(symbol, day, self.base))
        return None if text is None else parse_metrics(text)

    def funding(self, symbol: str, year: int, month: int) -> pd.DataFrame | None:
        text = self._text(funding_url(symbol, year, month, self.base))
        return None if text is None else parse_funding(text)

    def klines(self, symbol: str, interval: str, year: int, month: int) -> pd.DataFrame | None:
        text = self._text(klines_url(symbol, interval, year, month, self.base))
        return None if text is None else parse_klines(text)

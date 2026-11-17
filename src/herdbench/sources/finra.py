"""FINRA equity short interest (twice-monthly settlement files, exchange-listed and OTC securities).

Terms: FINRA's website terms restrict redistribution and derived databases, while its API terms allow
derived data with FINRA named as owner and source, for non-commercial use. HerdBench therefore never
redistributes these files; only summary statistics computed from them are published, in a separate file
that names FINRA as the source.
"""

from __future__ import annotations

import io
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from herdbench._http import HttpClient

CDN = "https://cdn.finra.org"

_RENAME = {
    "symbolCode": "symbol",
    "issueName": "issue_name",
    "issuerServicesGroupExchangeCode": "exchange_code",
    "marketClassCode": "market_class",
    "currentShortPositionQuantity": "short_interest",
    "previousShortPositionQuantity": "previous_short_interest",
    "averageDailyVolumeQuantity": "avg_daily_volume",
    "daysToCoverQuantity": "days_to_cover",
    "revisionFlag": "revision_flag",
    "changePercent": "change_pct",
    "changePreviousNumber": "change_shares",
    "settlementDate": "settlement_date",
}


def short_interest_url(day: date, base: str = CDN) -> str:
    return f"{base}/equity/otcmarket/biweekly/shrt{day:%Y%m%d}.csv"


def parse_short_interest(text: str) -> pd.DataFrame:
    raw = pd.read_csv(io.StringIO(text), sep="|", dtype=str, keep_default_na=False)
    df = raw.rename(columns=_RENAME)[list(_RENAME.values())].copy()
    for col in ("short_interest", "previous_short_interest", "avg_daily_volume", "change_shares"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    for col in ("days_to_cover", "change_pct"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
    df["settlement_date"] = pd.to_datetime(df["settlement_date"], format="%Y-%m-%d").dt.date
    return df.reset_index(drop=True)


def _candidates(start: date, end: date):
    day = start
    while day <= end:
        if day.weekday() < 5 and (10 <= day.day <= 20 or day.day >= 24):
            yield day
        day += timedelta(days=1)


def discover_settlement_dates(start: date, end: date, base: str = CDN, http: HttpClient | None = None) -> list[date]:
    """Settlement dates in [start, end] for which FINRA has published a file (probed with HEAD)."""
    http = http or HttpClient(min_interval=0.2)
    return [d for d in _candidates(start, end) if http.head(short_interest_url(d, base)).status_code == 200]


class Finra:
    def __init__(self, base: str = CDN, http: HttpClient | None = None, cache_dir: Path | None = None):
        self.base = base.rstrip("/")
        self.http = http or HttpClient(min_interval=0.2)
        self.cache_dir = Path(cache_dir) if cache_dir else None

    def short_interest(self, day: date) -> pd.DataFrame | None:
        cached = self.cache_dir / f"shrt{day:%Y%m%d}.csv" if self.cache_dir else None
        if cached is not None and cached.exists():
            return parse_short_interest(cached.read_text(encoding="utf-8"))
        r = self.http.get(short_interest_url(day, self.base))
        if r.status_code in (403, 404):
            return None
        if r.status_code != 200:
            raise RuntimeError(f"FINRA returned HTTP {r.status_code} for {day}")
        text = r.content.decode("utf-8", errors="replace")
        if cached is not None:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(text, encoding="utf-8")
        return parse_short_interest(text)

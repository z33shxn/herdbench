"""CFTC Commitments of Traders through the public Socrata API (no key; US government work, public domain).

Positions are as of Tuesday and released on Friday. Metals and energy use the Disaggregated report
(managed money); equity indices and currencies use Traders in Financial Futures (leveraged funds).
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

import pandas as pd

from herdbench._http import HttpClient

SOCRATA = "https://publicreporting.cftc.gov/resource"
VIEWS = {"disaggregated": "72hh-3qpy", "tff": "gpe5-46if"}  # futures-only reports


@dataclass(frozen=True)
class BridgeSpec:
    code: str
    report: str
    label: str


#: Hyperliquid ``xyz`` perpetual -> CFTC contract that carries the same exposure.
BRIDGE: dict[str, BridgeSpec] = {
    "xyz:GOLD": BridgeSpec("088691", "disaggregated", "Gold, COMEX"),
    "xyz:SILVER": BridgeSpec("084691", "disaggregated", "Silver, COMEX"),
    "xyz:COPPER": BridgeSpec("085692", "disaggregated", "Copper, COMEX"),
    "xyz:PLATINUM": BridgeSpec("076651", "disaggregated", "Platinum, NYMEX"),
    "xyz:PALLADIUM": BridgeSpec("075651", "disaggregated", "Palladium, NYMEX"),
    "xyz:CL": BridgeSpec("067651", "disaggregated", "WTI crude oil, NYMEX"),
    "xyz:NATGAS": BridgeSpec("023651", "disaggregated", "Natural gas, NYMEX"),
    "xyz:SP500": BridgeSpec("13874A", "tff", "E-mini S&P 500, CME"),
    "xyz:XYZ100": BridgeSpec("209742", "tff", "Nasdaq-100 mini, CME"),
    "xyz:EUR": BridgeSpec("099741", "tff", "Euro FX, CME"),
    "xyz:JPY": BridgeSpec("097741", "tff", "Japanese yen, CME"),
    "xyz:GBP": BridgeSpec("096742", "tff", "British pound, CME"),
}

_GROUPS = {"disaggregated": ("m_money_positions_long_all", "m_money_positions_short_all"),
           "tff": ("lev_money_positions_long", "lev_money_positions_short")}


def _parse(rows: list[dict], report: str) -> pd.DataFrame:
    long_col, short_col = _GROUPS[report]
    df = pd.DataFrame(rows)
    out = pd.DataFrame({
        "report_date": pd.to_datetime(df["report_date_as_yyyy_mm_dd"].str.slice(0, 10)),
        "open_interest": pd.to_numeric(df["open_interest_all"]).astype("int64"),
        "long": pd.to_numeric(df[long_col]).astype("int64"),
        "short": pd.to_numeric(df[short_col]).astype("int64"),
    })
    out["net_share"] = (out["long"] - out["short"]) / out["open_interest"]
    return out.sort_values("report_date").reset_index(drop=True)


def parse_disaggregated(rows: list[dict]) -> pd.DataFrame:
    """Managed-money long, short and net share of open interest."""
    return _parse(rows, "disaggregated")


def parse_tff(rows: list[dict]) -> pd.DataFrame:
    """Leveraged-funds long, short and net share of open interest."""
    return _parse(rows, "tff")


class COT:
    def __init__(self, base: str = SOCRATA, http: HttpClient | None = None):
        self.base = base.rstrip("/")
        self.http = http or HttpClient(min_interval=0.2)

    def positions(self, spec: BridgeSpec, start: str, end: str) -> pd.DataFrame:
        query = urlencode({
            "$where": (f"cftc_contract_market_code='{spec.code}' AND report_date_as_yyyy_mm_dd >= '{start}T00:00:00'"
                       f" AND report_date_as_yyyy_mm_dd <= '{end}T00:00:00'"),
            "$order": "report_date_as_yyyy_mm_dd", "$limit": 5000})
        r = self.http.get(f"{self.base}/{VIEWS[spec.report]}.json?{query}")
        if r.status_code != 200:
            raise RuntimeError(f"CFTC returned HTTP {r.status_code} for {spec.code}")
        rows = r.json()
        if not rows:
            return pd.DataFrame(columns=["report_date", "open_interest", "long", "short", "net_share"])
        return _parse(rows, spec.report)

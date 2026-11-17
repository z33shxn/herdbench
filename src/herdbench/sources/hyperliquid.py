"""Read-only client for Hyperliquid's public info API (no key, no account).

Only aggregate, public market data is read here: hourly funding and premium, daily candles and the
list of builder-deployed dexes. Requests are spaced to stay well inside the public limit of
1,200 weight per minute (an info request weighs 20).
"""

from __future__ import annotations

import pandas as pd

from herdbench._http import HttpClient

INFO_URL = "https://api.hyperliquid.xyz/info"
FUNDING_PAGE = 500  # the venue returns at most 500 funding rows per request


class InfoApi:
    def __init__(self, url: str = INFO_URL, http: HttpClient | None = None):
        self.url = url
        self.http = http or HttpClient(min_interval=1.1)

    def _post(self, body: dict):
        return self.http.post_json(self.url, body)

    def funding_history(self, coin: str, start_ms: int, end_ms: int) -> pd.DataFrame:
        """Hourly funding rate and premium for ``coin`` with ``start_ms <= time <= end_ms``."""
        rows: list[dict] = []
        cursor = start_ms
        while cursor <= end_ms:
            page = self._post({"type": "fundingHistory", "coin": coin, "startTime": cursor, "endTime": end_ms})
            if not page:
                break
            rows.extend(page)
            cursor = int(page[-1]["time"]) + 1
            if len(page) < FUNDING_PAGE:
                break
        df = pd.DataFrame(rows, columns=["coin", "fundingRate", "premium", "time"])
        out = pd.DataFrame({
            "time": pd.to_datetime(df["time"].astype("int64"), unit="ms", utc=True),
            "funding": df["fundingRate"].astype(float),
            "premium": df["premium"].astype(float),
        })
        return out.drop_duplicates("time").sort_values("time").reset_index(drop=True)

    def daily_candles(self, coin: str, start_ms: int, end_ms: int) -> pd.DataFrame:
        raw = self._post({"type": "candleSnapshot",
                          "req": {"coin": coin, "interval": "1d", "startTime": start_ms, "endTime": end_ms}})
        df = pd.DataFrame(raw, columns=["t", "T", "s", "i", "o", "c", "h", "l", "v", "n"])
        return pd.DataFrame({
            "t": pd.to_datetime(df["t"].astype("int64"), unit="ms", utc=True),
            "open": df["o"].astype(float),
            "high": df["h"].astype(float),
            "low": df["l"].astype(float),
            "close": df["c"].astype(float),
            "volume": df["v"].astype(float),
            "trades": df["n"].astype("int64"),
        }).sort_values("t").reset_index(drop=True)

    def user_role(self, address: str) -> tuple[str, str | None]:
        """(role, master): role is user, agent, vault, subAccount or missing; master only for sub-accounts.

        Weight 60, so at most 20 calls a minute per IP.
        """
        reply = self._post({"type": "userRole", "user": address}) or {}
        role = str(reply.get("role", "missing"))
        master = (reply.get("data") or {}).get("master") if role == "subAccount" else None
        return role, (master.lower() if master else None)

    def sub_accounts(self, master: str) -> list[str]:
        reply = self._post({"type": "subAccounts", "user": master}) or []
        return [str(item["subAccountUser"]).lower() for item in reply if item.get("subAccountUser")]

    def perp_dexs(self) -> list[str]:
        """Builder-deployed perp dexes, in index order (the main dex is index 0 and has no name)."""
        return [d["name"] for d in self._post({"type": "perpDexs"}) if d]

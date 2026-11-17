"""Optional Coinalyze client, used only for cross-source checks (free key in ``COINALYZE_API_KEY``).

The published results never depend on this source. The key is sent as the ``api_key`` URL parameter
(the only form the service accepts), so every error path is rebuilt without the URL and without the
chained exception: no message, traceback or ``repr`` can contain the key.
"""

from __future__ import annotations

import os
from urllib.parse import urlencode

import pandas as pd

from herdbench._http import HttpClient, HttpError

BASE = "https://api.coinalyze.net/v1/"


class MissingKey(RuntimeError):
    pass


class CoinalyzeError(RuntimeError):
    pass


class Coinalyze:
    def __init__(self, key: str | None = None, base: str = BASE, http: HttpClient | None = None):
        key = key or os.environ.get("COINALYZE_API_KEY")
        if not key:
            raise MissingKey("set COINALYZE_API_KEY to use Coinalyze cross-checks (free key at coinalyze.net)")
        self._key = key
        self.base = base if base.endswith("/") else base + "/"
        self.http = http or HttpClient(min_interval=1.6)  # free tier: 40 calls per minute

    def __repr__(self) -> str:
        return f"Coinalyze(base={self.base!r})"

    def history(self, endpoint: str, symbols: str, interval: str, start: int, end: int, **params) -> pd.DataFrame:
        query = urlencode({"symbols": symbols, "interval": interval, "from": start, "to": end,
                           **params, "api_key": self._key})
        try:
            r = self.http.get(f"{self.base}{endpoint}?{query}")
        except HttpError:
            raise CoinalyzeError(f"{endpoint} failed after retries") from None
        if r.status_code != 200:
            raise CoinalyzeError(f"{endpoint} returned HTTP {r.status_code}")
        rows = []
        for item in r.json():
            for point in item.get("history", []):
                rows.append({"symbol": item.get("symbol"), **point})
        return pd.DataFrame(rows)

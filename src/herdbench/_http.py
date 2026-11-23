"""One HTTP client for every public source: retries, backoff, Retry-After, and request spacing."""

from __future__ import annotations

import threading
import time
from typing import Any

import requests

USER_AGENT = "herdbench/1.0 (+https://github.com/z33shxn/herdbench)"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class HttpError(RuntimeError):
    """A request still failed after every retry."""


class HttpClient:
    """Thin wrapper over a ``requests.Session``.

    Transient failures (connection errors, timeouts, HTTP 429 and 5xx) are retried with exponential
    backoff, honouring ``Retry-After``. Any other status is returned to the caller unchanged.
    ``min_interval`` spaces consecutive requests to stay inside public rate limits.
    """

    def __init__(
        self,
        user_agent: str = USER_AGENT,
        timeout: float = 60.0,
        retries: int = 6,
        backoff: float = 2.0,
        max_backoff: float = 60.0,
        min_interval: float = 0.0,
        session: requests.Session | None = None,
    ):
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.max_backoff = max_backoff
        self.min_interval = min_interval
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self._last = 0.0
        self._lock = threading.Lock()

    def _space(self) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()

    def _sleep(self, attempt: int, response: requests.Response | None) -> None:
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after is not None:
                try:
                    time.sleep(min(self.max_backoff, max(0.0, float(retry_after))))
                    return
                except ValueError:
                    pass
        delay = min(self.max_backoff, self.backoff * (2**attempt))
        if delay > 0:
            time.sleep(delay)

    def request(self, method: str, url: str, **kw: Any) -> requests.Response:
        kw.setdefault("timeout", self.timeout)
        last: str = ""
        for attempt in range(self.retries):
            self._space()
            response: requests.Response | None = None
            try:
                response = self.session.request(method, url, **kw)
            except (requests.ConnectionError, requests.Timeout) as exc:
                last = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code not in RETRY_STATUSES:
                    return response
                last = f"HTTP {response.status_code}"
                response.close()
            if attempt + 1 < self.retries:
                self._sleep(attempt, response)
        raise HttpError(f"{method} {url} failed after {self.retries} attempts ({last})")

    def get(self, url: str, **kw: Any) -> requests.Response:
        return self.request("GET", url, **kw)

    def head(self, url: str, **kw: Any) -> requests.Response:
        return self.request("HEAD", url, **kw)

    def post_json(self, url: str, body: Any) -> Any:
        response = self.request("POST", url, json=body)
        if response.status_code != 200:
            raise HttpError(f"POST {url} returned HTTP {response.status_code}: {response.text[:200]}")
        return response.json()

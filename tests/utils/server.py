"""A small local HTTP server that behaves like the real data hosts, for network-free tests.

It serves byte ranges (HTTP 206), follows a redirect table like the Hugging Face ``resolve`` endpoint,
can fail the first N requests to a path (to exercise retries), serves paginated JSON with a ``Link``
header, answers HEAD, and routes POST bodies to handler functions.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit


@dataclass
class Routes:
    files: dict[str, bytes] = field(default_factory=dict)
    json_pages: dict[str, tuple[Any, str | None]] = field(default_factory=dict)  # path?query -> (payload, next link)
    redirects: dict[str, str] = field(default_factory=dict)
    fail_first: dict[str, tuple[int, int]] = field(default_factory=dict)  # path -> (remaining, status)
    post: dict[str, Callable[[dict], tuple[int, Any]]] = field(default_factory=dict)
    head_ok: set[str] = field(default_factory=set)
    log: list[tuple[str, str, dict[str, str]]] = field(default_factory=list)  # (method, path+query, headers)


def _handler(routes: Routes, base: list[str]):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:  # keep test output clean
            pass

        def _fail_if_scheduled(self, key: str) -> bool:
            if key in routes.fail_first:
                remaining, status = routes.fail_first[key]
                if remaining > 0:
                    routes.fail_first[key] = (remaining - 1, status)
                    body = b"try again"
                    self.send_response(status)
                    if status == 429:
                        self.send_header("Retry-After", "0")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return True
            return False

        def do_HEAD(self) -> None:
            path = urlsplit(self.path).path
            routes.log.append(("HEAD", self.path, dict(self.headers)))
            ok = path in routes.head_ok or path in routes.files
            self.send_response(200 if ok else 403)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:
            parts = urlsplit(self.path)
            path, full = parts.path, self.path
            routes.log.append(("GET", full, dict(self.headers)))
            if self._fail_if_scheduled(path):
                return
            if path in routes.redirects:
                self.send_response(302)
                self.send_header("Location", base[0] + routes.redirects[path])
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if full in routes.json_pages or path in routes.json_pages:
                payload, nxt = routes.json_pages.get(full) or routes.json_pages[path]
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                if nxt:
                    self.send_header("Link", f'<{base[0]}{nxt}>; rel="next"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path in routes.files:
                data = routes.files[path]
                rng = self.headers.get("Range")
                if rng:
                    m = re.fullmatch(r"bytes=(\d+)-(\d*)", rng.strip())
                    assert m, f"bad Range header {rng!r}"
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else len(data) - 1
                    end = min(end, len(data) - 1)
                    chunk = data[start : end + 1]
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
                else:
                    chunk = data
                    self.send_response(200)
                self.send_header("Content-Length", str(len(chunk)))
                self.end_headers()
                self.wfile.write(chunk)
                return
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            routes.log.append(("POST", self.path, {"body": json.dumps(body)}))
            if self._fail_if_scheduled(path):
                return
            status, payload = routes.post[path](body)
            out = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    return Handler


@contextmanager
def serve(routes: Routes):
    """Run the server on a free local port; yields its base URL, e.g. ``http://127.0.0.1:54321``."""
    base: list[str] = [""]
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(routes, base))
    base[0] = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield base[0]
    finally:
        server.shutdown()
        server.server_close()

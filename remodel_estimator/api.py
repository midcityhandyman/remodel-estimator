"""Small JSON API plus static file serving for the web UI. Standard library only.

    GET  /api/health            liveness and price book header
    GET  /api/catalog           selectable work items
    GET  /api/examples          list of example scopes
    GET  /api/examples/<name>   one example scope
    POST /api/estimate          scope JSON in, estimate JSON out
    GET  /                      the single page web UI
"""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .catalog import Catalog, load_catalog
from .engine import estimate
from .models import ScopeError
from .paths import EXAMPLES_DIR, WEB_DIR
from .pricebook import PriceBook, PriceBookError, load_price_book

MAX_BODY_BYTES = 1_000_000
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
}
EXAMPLE_NAME = re.compile(r"^[a-z0-9_-]{1,40}$")


class EstimatorServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], catalog: Catalog, price_book: PriceBook, quiet: bool):
        super().__init__(address, EstimatorHandler)
        self.catalog = catalog
        self.price_book = price_book
        self.quiet = quiet


def list_examples() -> list[dict[str, str]]:
    examples = []
    for path in sorted(EXAMPLES_DIR.glob("*.json")):
        project = json.loads(path.read_text(encoding="utf-8")).get("project", path.stem)
        examples.append({"name": path.stem, "project": project})
    return examples


def load_example(name: str) -> dict[str, Any] | None:
    if not EXAMPLE_NAME.match(name):
        return None
    path = EXAMPLES_DIR / f"{name}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def static_file(url_path: str) -> Path | None:
    """Map a URL path to a file inside the web folder, refusing anything outside it."""
    relative = "index.html" if url_path in ("", "/") else url_path.lstrip("/")
    root = WEB_DIR.resolve()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


class EstimatorHandler(BaseHTTPRequestHandler):
    server: EstimatorServer
    server_version = "RemodelEstimator/0.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        if not self.server.quiet:
            super().log_message(format, *args)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: Any) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), CONTENT_TYPES[".json"])

    def _error(self, status: int, message: str, problems: list[str] | None = None) -> None:
        self._json(status, {"error": message, "problems": problems or []})

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/health":
            self._json(200, {"status": "ok", "price_book": self.server.price_book.header()})
        elif path == "/api/catalog":
            self._json(200, self.server.catalog.to_public())
        elif path == "/api/examples":
            self._json(200, {"examples": list_examples()})
        elif path.startswith("/api/examples/"):
            example = load_example(path.rsplit("/", 1)[1])
            if example is None:
                self._error(404, "no such example")
            else:
                self._json(200, example)
        elif path.startswith("/api/"):
            self._error(404, "no such endpoint")
        else:
            found = static_file(path)
            if found is None:
                self._error(404, "not found")
            else:
                self._send(200, found.read_bytes(), CONTENT_TYPES.get(found.suffix, "application/octet-stream"))

    do_HEAD = do_GET  # noqa: N815

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/api/estimate":
            self._error(404, "no such endpoint")
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._error(411, "Content-Length is required")
            return
        if length < 0 or length > MAX_BODY_BYTES:
            self._error(413, "request body is too large")
            return
        try:
            scope_data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._error(400, "request body is not valid JSON")
            return
        try:
            result = estimate(scope_data, self.server.catalog, self.server.price_book)
        except ScopeError as exc:
            self._error(400, "the scope has problems", exc.problems)
        except PriceBookError as exc:
            self._error(500, str(exc))
        else:
            self._json(200, result)


def make_server(host: str = "127.0.0.1", port: int = 8000, catalog: Catalog | None = None,
                price_book: PriceBook | None = None, quiet: bool = False) -> EstimatorServer:
    return EstimatorServer((host, port), catalog or load_catalog(), price_book or load_price_book(), quiet)

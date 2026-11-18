"""Local one-click search bridge for tools/sketch_pad.html (debug tool, not a product).

Serves the sketch pad and answers ``POST /search`` with the frozen production
search (``search_by_contour`` defaults: fourier_align / top_k=10 / shortlist=200 /
smoothing=light / allow_mirror=False) against the real index. Extraction refusals
are returned as refusals (HTTP 400 with the reason), never silently closed.

Loopback only, two fixed routes, 1 MiB body cap — no file serving beyond the pad
itself, no path parameters, nothing writable.

Usage:
    python scripts/serve_sketch_search.py [--index data/index] [--port 8765]
Then open http://127.0.0.1:8765/ in a browser, draw one closed stroke and press
"直接检索".
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from earthglyph.retrieval.search import (  # noqa: E402
    IndexHandle,
    load_index_handle,
    search_by_contour,
)

PAD_PATH = REPO_ROOT / "tools" / "sketch_pad.html"
MAX_BODY_BYTES = 1024 * 1024
DEFAULT_PORT = 8765
LOOPBACK = "127.0.0.1"


def run_search(payload: Any, index: IndexHandle) -> dict[str, Any]:
    """One search with production defaults; raises ValueError on refusal."""
    if not isinstance(payload, dict) or "points" not in payload:
        raise ValueError("expected a sketch export object with a 'points' key")
    result = search_by_contour(
        payload["points"],
        index,
        closed=payload.get("closed"),
        unit=payload.get("unit", "canvas"),
    )
    return {"ok": True, **result.to_dict(with_contours=True)}


def build_handler(index: IndexHandle) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: int, data: dict[str, Any]) -> None:
            self._send(status, json.dumps(data).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self) -> None:
            if self.path in ("/", "/sketch_pad.html"):
                self._send(200, PAD_PATH.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send_json(404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:
            if self.path != "/search":
                self._send_json(404, {"ok": False, "error": "not found"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                self._send_json(413, {"ok": False, "error": "body too large (max 1 MiB)"})
                return
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                self._send_json(400, {"ok": False, "error": f"invalid JSON: {error}"})
                return
            try:
                data = run_search(payload, index)
            except ValueError as error:
                self._send_json(400, {"ok": False, "error": str(error)})
                return
            self._send_json(200, data)

        def log_message(self, format: str, *args: Any) -> None:
            sys.stderr.write("[sketch-search] " + (format % args) + "\n")

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", default=str(REPO_ROOT / "data" / "index"))
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    index = load_index_handle(args.index)
    server = ThreadingHTTPServer((LOOPBACK, args.port), build_handler(index))
    print(
        f"serving {index.n_candidates} real candidates from {args.index}\n"
        f"open http://{LOOPBACK}:{args.port}/  (Ctrl+C to stop)",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
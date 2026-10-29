#!/usr/bin/env python3
"""Local dashboard. Reads BigQuery and GA4 when .env is set; otherwise previews locally."""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from interaction_insights.config import load_settings
from interaction_insights.service import DashboardService

HTML_PATH = Path(__file__).resolve().parent / "interaction_insights" / "dashboard.html"


class Handler(BaseHTTPRequestHandler):
    server: "AppServer"

    def log_message(self, fmt: str, *args) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/":
            body = HTML_PATH.read_bytes()
            self._send(200, body, "text/html; charset=utf-8")
            return
        if path == "/api/snapshot":
            payload = json.dumps(self.server.app.snapshot()).encode()
            self._send(200, payload, "application/json")
            return
        if path == "/api/health":
            self._send(200, b'{"ok":true}', "application/json")
            return
        self._send(404, b'{"error":"not found"}', "application/json")

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path != "/api/control":
            self._send(404, b'{"error":"not found"}', "application/json")
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            action = json.loads(raw.decode() or "{}").get("action", "")
            self.server.app.handle(str(action))
        except ValueError as exc:
            self._send(400, json.dumps({"error": str(exc)}).encode(), "application/json")
            return
        except Exception as exc:
            message = f"{exc.__class__.__name__}: {exc}"
            self._send(500, json.dumps({"error": message}).encode(), "application/json")
            return
        self._send(200, b'{"ok":true}', "application/json")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


class AppServer(ThreadingHTTPServer):
    def __init__(self, address, app: DashboardService) -> None:
        super().__init__(address, Handler)
        self.app = app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the Interaction Insights dashboard.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--speed", type=float, default=80, help="Local preview rows per second.")
    parser.add_argument("--max-rows", type=int, default=1000, help="Local preview row cap.")
    parser.add_argument("--source", choices=("auto", "cloud", "local"), default="auto")
    parser.add_argument("--data-dir", default="", help="Folder of events-*.parquet shards.")
    parser.add_argument("--report", default="", help="Path to data/report.json from scripts/build_report.py.")
    parser.add_argument("--no-loop", action="store_true")
    args = parser.parse_args(argv)
    settings = load_settings()
    app = DashboardService(
        settings,
        source=args.source,
        speed=args.speed,
        max_rows=args.max_rows,
        loop=not args.no_loop,
        data_dir=Path(args.data_dir) if args.data_dir else None,
        report_path=Path(args.report) if args.report else None,
    )
    app.start()
    server = AppServer((args.host, args.port), app)
    print(f"Interaction Insights at http://127.0.0.1:{args.port}/", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Stdlib HTTP server for PA Lottery Scratch Odds.

Serves the single-page UI, the cached data.json as a small JSON API, and
locally-cached ticket images. No framework, no pip installs.
"""
import json
import mimetypes
import os
import subprocess
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data.json"
IMAGES_DIR = ROOT / "images"
INDEX_FILE = ROOT / "index.html"
SCRAPE_SCRIPT = ROOT / "scrape.py"
LOCK_FILE = ROOT / "scrape.lock"

PORT = int(os.environ.get("PORT", "8789"))
BIND = os.environ.get("BIND", "0.0.0.0")


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # process exists, just owned by someone else


def scrape_running():
    if not LOCK_FILE.exists():
        return False
    try:
        pid = int(LOCK_FILE.read_text().strip())
    except (ValueError, OSError):
        return False
    return pid_alive(pid)


class Handler(BaseHTTPRequestHandler):
    server_version = "PALotteryScratchOdds/1.0"

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}")

    def _send(self, status, body, content_type, extra_headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]

        if path in ("/", "/index.html"):
            if not INDEX_FILE.exists():
                self._send(HTTPStatus.NOT_FOUND, b"index.html missing", "text/plain")
                return
            self._send(HTTPStatus.OK, INDEX_FILE.read_bytes(), "text/html; charset=utf-8")
            return

        if path == "/api/games.json":
            if DATA_FILE.exists():
                body = DATA_FILE.read_bytes()
            else:
                body = json.dumps({
                    "scraped_at": None,
                    "prizes_remaining_as_of": None,
                    "game_count": 0,
                    "games": [],
                }).encode()
            self._send(
                HTTPStatus.OK, body, "application/json",
                {"Cache-Control": "no-store"},
            )
            return

        if path == "/api/refresh-status":
            body = json.dumps({"running": scrape_running()}).encode()
            self._send(HTTPStatus.OK, body, "application/json", {"Cache-Control": "no-store"})
            return

        if path == "/health":
            games = 0
            scraped_at = None
            if DATA_FILE.exists():
                try:
                    d = json.loads(DATA_FILE.read_text())
                    games = d.get("game_count", 0)
                    scraped_at = d.get("scraped_at")
                except (json.JSONDecodeError, OSError):
                    pass
            body = json.dumps({"ok": True, "games": games, "scraped_at": scraped_at}).encode()
            self._send(HTTPStatus.OK, body, "application/json")
            return

        if path.startswith("/images/"):
            rel = path[len("/images/"):]
            # path-traversal guard: resolve and confirm containment
            candidate = (IMAGES_DIR / rel).resolve()
            try:
                candidate.relative_to(IMAGES_DIR.resolve())
            except ValueError:
                self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
                return
            if not candidate.is_file():
                self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
                return
            ctype = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
            self._send(
                HTTPStatus.OK, candidate.read_bytes(), ctype,
                {"Cache-Control": "public, max-age=604800"},
            )
            return

        self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

    def do_POST(self):
        path = self.path.split("?", 1)[0]

        if path == "/api/refresh":
            if scrape_running():
                body = json.dumps({"status": "already_running"}).encode()
                self._send(HTTPStatus.CONFLICT, body, "application/json")
                return
            subprocess.Popen([sys.executable, str(SCRAPE_SCRIPT)], cwd=str(ROOT))
            body = json.dumps({"status": "started"}).encode()
            self._send(HTTPStatus.ACCEPTED, body, "application/json")
            return

        self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")


def main():
    ThreadingHTTPServer.allow_reuse_address = True
    with ThreadingHTTPServer((BIND, PORT), Handler) as httpd:
        print(f"PA Lottery Scratch Odds serving on http://{BIND}:{PORT}/")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()

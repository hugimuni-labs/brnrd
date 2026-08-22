"""Small authenticated seam between signal-cli-rest-api and brnrd.dev."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import urllib.error
import urllib.request
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BRNRD_WEBHOOK_URL = os.environ["BRNRD_WEBHOOK_URL"]
WEBHOOK_SECRET = os.environ["BRNRD_SIGNAL_WEBHOOK_SECRET"]
API_TOKEN = os.environ["BRNRD_SIGNAL_API_TOKEN"]
SIGNAL_API_URL = os.environ.get("SIGNAL_API_URL", "http://signal-api:8080")
SPOOL_DIR = Path(os.environ.get("SPOOL_DIR", "/data/inbox"))


def _post(url: str, body: bytes, headers: dict[str, str]) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=35) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class Handler(BaseHTTPRequestHandler):
    server_version = "brnrd-signal-bridge/1"

    def _body(self) -> bytes:
        try:
            length = int(self.headers.get("content-length", "0"))
        except ValueError:
            length = 0
        return self.rfile.read(min(length, 1_000_000))

    def _answer(self, status: int, body: bytes = b"") -> None:
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self.path == "/health":
            self._answer(200, b'{"ok":true}')
        else:
            self._answer(404)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        body = self._body()
        if self.path == "/receive":
            SPOOL_DIR.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256(body).hexdigest()
            pending = SPOOL_DIR / f"{time.time_ns()}-{digest}.json"
            temporary = pending.with_suffix(".tmp")
            temporary.write_bytes(body)
            temporary.replace(pending)
            self._answer(202, b'{"queued":true}')
            return
        if self.path == "/v2/send" or self.path.startswith("/v1/register/"):
            supplied = self.headers.get("authorization", "")
            if not hmac.compare_digest(supplied, f"Bearer {API_TOKEN}"):
                self._answer(403, b'{"detail":"bad token"}')
                return
            status, response = _post(
                f"{SIGNAL_API_URL}{self.path}",
                body,
                {"content-type": "application/json"},
            )
            self._answer(status, response)
            return
        self._answer(404)

    def log_message(self, fmt: str, *args: object) -> None:
        # Never include headers or bodies: both can carry credentials or mail.
        print(f"[signal-bridge] {self.address_string()} {fmt % args}", flush=True)


def _drain_spool() -> None:
    SPOOL_DIR.mkdir(parents=True, exist_ok=True)
    while True:
        pending = next(iter(sorted(SPOOL_DIR.glob("*.json"))), None)
        if pending is None:
            time.sleep(1)
            continue
        body = pending.read_bytes()
        signature = "sha256=" + hmac.new(
            WEBHOOK_SECRET.encode(), body, hashlib.sha256
        ).hexdigest()
        try:
            status, _ = _post(
                BRNRD_WEBHOOK_URL,
                body,
                {
                    "content-type": "application/json",
                    "x-brnrd-signal-signature": signature,
                },
            )
        except OSError as exc:
            print(f"[signal-bridge] delivery failed: {exc}", flush=True)
            time.sleep(5)
            continue
        if 200 <= status < 300:
            pending.unlink()
        else:
            print(f"[signal-bridge] delivery returned {status}", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    threading.Thread(target=_drain_spool, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8090), Handler).serve_forever()

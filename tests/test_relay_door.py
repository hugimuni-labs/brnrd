"""The relay's door: one tap on brnrd.dev's shared bot pairs a person with this loom.

The shared bot binds a chat only to a code the server minted, so the loom's
own pairing code rides on that code (`POST /v1/daemons/door`) and reaches the
daemon as the chat's first message once the link is used.
"""

from __future__ import annotations

import http.server
import json
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient  # noqa: E402

from brnrd import create_app  # noqa: E402
from brnrd.config import Settings, _derived_telegram_usernames  # noqa: E402
from _helpers import brnrd_repo_and_daemon  # noqa: E402

_SECRET = "webhook-secret"


@pytest.fixture()
def env(monkeypatch):
    _derived_telegram_usernames.clear()
    sends: list[dict] = []
    monkeypatch.setattr(
        "brnrd.platforms.telegram.send_message",
        lambda token, chat_id, text, **_: sends.append({"chat_id": chat_id, "text": text}),
    )
    app = create_app(Settings(
        database_url="sqlite:///:memory:", telegram_bot_token="bot:TOKEN",
        telegram_webhook_secret=_SECRET, telegram_bot_username="brnrd_bot",
        inbox_long_poll_max_s=0.2, inbox_poll_interval_s=0.02,
    ))
    yield TestClient(app, base_url="https://testserver"), sends
    _derived_telegram_usernames.clear()


def test_a_door_link_binds_the_chat_and_hands_the_daemon_the_loom_code(env):
    client, sends = env
    _account, daemon, _repo = brnrd_repo_and_daemon(client)
    r = client.post("/v1/daemons/door", json={"code": "0a1b2c3d"}, headers=daemon)
    assert r.status_code == 200, r.text
    door = r.json()
    assert door["name"] == "@brnrd_bot"
    server_code = door["link"].removeprefix("https://t.me/brnrd_bot?start=")
    assert server_code and server_code != "0a1b2c3d"

    # Telegram turns the link into "/start <code>" once Start is pressed.
    update = {"message": {
        "message_id": 7, "date": int(datetime.now(timezone.utc).timestamp()),
        "chat": {"id": 777}, "from": {"id": 42, "first_name": "Ada", "username": "ada_l"},
        "text": f"/start {server_code}",
    }}
    r = client.post("/v1/webhooks/telegram", json=update,
                    headers={"X-Telegram-Bot-Api-Secret-Token": _SECRET})
    assert r.status_code == 200, r.text
    # The loom answers ("Paired ✓"); the bot's own pairing text would be a second voice.
    assert sends == []
    events = client.get("/v1/daemons/inbox", params={"since": 0, "wait": 0}, headers=daemon).json()["events"]
    assert [(e["body"], str(e["reply_to"]["chat_id"])) for e in events] == [("0a1b2c3d", "777")]


def test_door_refuses_an_account_token_and_a_malformed_code(env):
    client, _ = env
    account, daemon, _repo = brnrd_repo_and_daemon(client)
    assert client.post("/v1/daemons/door", json={"code": "0a1b2c3d"}, headers=account).status_code == 403
    assert client.post("/v1/daemons/door", json={"code": "not hex!"}, headers=daemon).status_code == 422


def test_relay_door_prints_the_link_the_server_mints(tmp_path):
    from brr.gates import cloud
    from brr.loom.runtime import connector, setup_channel
    from brr.loom.runtime.config import seed_config, set_value
    from brr.loom.runtime.home import Home

    seen = []

    class Server(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            size = int(self.headers.get("Content-Length") or 0)
            seen.append((self.path, json.loads(self.rfile.read(size)), self.headers.get("Authorization")))
            raw = json.dumps({"name": "@brnrd_bot", "link": "https://t.me/brnrd_bot?start=TG1"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Server)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    seed_config(tmp_path)
    cloud._save_state_to_dir(tmp_path / "loom", dict(brnrd_url=f"http://127.0.0.1:{server.server_port}",
                                                    token="bd_tok", since=0))
    set_value(tmp_path, "relay_state", str(tmp_path / "loom"))
    folder = Path(setup_channel.__file__).parent / "connectors/relay"
    try:
        door = setup_channel._door(folder, connector.environment(Home(tmp_path), "cloud"), "0a1b2c3d")
    finally:
        server.shutdown()
    assert door == {"name": "@brnrd_bot", "link": "https://t.me/brnrd_bot?start=TG1"}
    assert seen == [("/v1/daemons/door", {"code": "0a1b2c3d"}, "Bearer bd_tok")]

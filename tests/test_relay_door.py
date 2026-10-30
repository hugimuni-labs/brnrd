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
from _helpers import approve_pair, brnrd_repo_and_daemon, repo_telegram_pair  # noqa: E402

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
    r = client.post("/v1/daemons/door", json={"code": "K7Q-M9X"}, headers=daemon)
    assert r.status_code == 200, r.text
    door = r.json()
    assert door["name"] == "@brnrd_bot"
    server_code = door["link"].removeprefix("https://t.me/brnrd_bot?start=")
    assert server_code and server_code != "K7Q-M9X"

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
    assert [(e["body"], str(e["reply_to"]["chat_id"])) for e in events] == [("K7Q-M9X", "777")]


def test_door_refuses_an_account_token_and_a_malformed_code(env):
    client, _ = env
    account, daemon, _repo = brnrd_repo_and_daemon(client)
    assert client.post("/v1/daemons/door", json={"code": "0a1b2c3d"}, headers=account).status_code == 403
    for bad in ("not hex!", "k7q-m9x", "K7Q-M9O"):
        assert client.post("/v1/daemons/door", json={"code": bad}, headers=daemon).status_code == 422
    # A loom older than the typed code still pairs.
    assert client.post("/v1/daemons/door", json={"code": "0a1b2c3d"}, headers=daemon).status_code == 200


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
    # Typed into the shared bot, only the server's code pairs; no QR on this path.
    assert door == {"name": "@brnrd_bot", "link": "https://t.me/brnrd_bot?start=TG1", "code": "TG1", "qr": False}
    assert seen == [("/v1/daemons/door", {"code": "0a1b2c3d"}, "Bearer bd_tok")]


def _start(client, chat_id, text, *, user_id=42, message_id=7):
    update = {"message": {
        "message_id": message_id, "date": int(datetime.now(timezone.utc).timestamp()),
        "chat": {"id": chat_id}, "from": {"id": user_id, "first_name": "Ada", "username": "ada_l"},
        "text": text,
    }}
    r = client.post("/v1/webhooks/telegram", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": _SECRET})
    assert r.status_code == 200, r.text


def test_a_codeless_door_binds_the_chat_to_the_account_and_the_loom_reads_it_back(env):
    client, _ = env
    _account, daemon, _repo = brnrd_repo_and_daemon(client)
    assert client.get("/v1/daemons/chats", headers=daemon).json() == {"chats": []}
    door = client.post("/v1/daemons/door", json={}, headers=daemon).json()
    _start(client, 778, "/start " + door["link"].removeprefix("https://t.me/brnrd_bot?start="))

    chats = client.get("/v1/daemons/chats", headers=daemon).json()["chats"]
    assert [(c["platform"], c["chat_id"], c["principal_id"], c["principal_display"]) for c in chats] == [
        ("telegram", "778", "42", "@ada_l")]
    # No loom code rode on it: nothing for the daemon to pair on.
    assert client.get("/v1/daemons/inbox", params={"since": 0, "wait": 0}, headers=daemon).json()["events"] == []

    assert client.delete(f"/v1/daemons/chats/{chats[0]['id']}", headers=daemon).status_code == 200
    assert client.get("/v1/daemons/chats", headers=daemon).json() == {"chats": []}
    assert client.delete(f"/v1/daemons/chats/{chats[0]['id']}", headers=daemon).status_code == 404


def test_another_accounts_daemon_neither_sees_nor_unbinds_the_chat(env):
    from _helpers import brnrd_account_headers

    client, _ = env
    _account, daemon, _repo = brnrd_repo_and_daemon(client)
    door = client.post("/v1/daemons/door", json={}, headers=daemon).json()
    _start(client, 779, "/start " + door["link"].removeprefix("https://t.me/brnrd_bot?start="))
    chat = client.get("/v1/daemons/chats", headers=daemon).json()["chats"][0]

    other = brnrd_account_headers(client.app, github_id="999", login="mallory", email="m@x.com")
    repo = client.post("/v1/accounts/repos", json={"repo_full_name": "mallory/x"}, headers=other).json()
    pair = client.post("/v1/accounts/pair").json()
    approve_pair(client, pair["pair_code"], {"repo_id": repo["repo_id"], "approve_secret": pair["approve_secret"]}, headers=other)
    token = client.get(f"/v1/accounts/pair/{pair['pair_code']}", params={"poll_secret": pair["poll_secret"]}).json()["daemon_token"]
    theirs = {"Authorization": f"Bearer {token}"}

    assert client.get("/v1/daemons/chats", headers=theirs).json() == {"chats": []}
    assert client.delete(f"/v1/daemons/chats/{chat['id']}", headers=theirs).status_code == 404
    assert len(client.get("/v1/daemons/chats", headers=daemon).json()["chats"]) == 1


def test_a_chat_reaches_the_setup_loom_over_an_old_daemons_pinned_busier_repo(env):
    """`brnrd setup` registers its loom under the hostname, so only the loom
    snapshot says it is one. A chat the old daemon pinned, on the repo with the
    newest event, still lands in the resident's repo. (Every daemon on the
    account still reads it: the inbox is account-wide, and keeping an old
    daemon from answering is a consumer rule, not routing.)"""
    from brnrd import inbox as inbox_service

    client, _ = env
    account, loom, loom_repo = brnrd_repo_and_daemon(client)
    assert client.post("/v1/daemons/register", json={"daemon_name": "yellowcat"}, headers=loom).status_code == 200
    assert client.put("/v1/daemons/loom", json={"v": 1}, headers=loom).status_code in (200, 202, 409, 451)

    old = client.post("/v1/accounts/repos", json={"repo_full_name": "Gurio/old"}, headers=account).json()["repo_id"]
    code = repo_telegram_pair(client, old, headers=account).json()["pair_code"]
    _start(client, 780, f"/start {code}")
    with client.app.state.SessionLocal() as db:
        inbox_service.enqueue(db, repo_id=old, body="the old daemon's last work", source="test")

    _start(client, 780, "hello", message_id=8)

    events = client.get("/v1/daemons/inbox", params={"since": 0, "wait": 0}, headers=loom).json()["events"]
    assert [(e["body"], e["repo_label"]) for e in events if e["body"] == "hello"] == [("hello", "Gurio/brr")]


def test_a_codeless_door_still_carries_a_server_code_and_a_bare_start_binds_nothing(env):
    client, _ = env
    _account, daemon, _repo = brnrd_repo_and_daemon(client)
    door = client.post("/v1/daemons/door", json={}, headers=daemon).json()
    server_code = door["link"].removeprefix("https://t.me/brnrd_bot?start=")
    assert server_code and server_code != door["link"]

    _start(client, 781, "/start")
    _start(client, 782, "/start NOT-A-CODE", message_id=8)

    assert client.get("/v1/daemons/chats", headers=daemon).json() == {"chats": []}

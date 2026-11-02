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
    assert client.get("/v1/daemons/chats", headers=daemon).json() == {
        "chats": [], "login": "octocat", "links": {"telegram": "https://t.me/brnrd_bot"}}
    door = client.post("/v1/daemons/door", json={}, headers=daemon).json()
    _start(client, 778, "/start " + door["link"].removeprefix("https://t.me/brnrd_bot?start="))

    chats = client.get("/v1/daemons/chats", headers=daemon).json()["chats"]
    assert [(c["platform"], c["chat_id"], c["principal_id"], c["principal_display"]) for c in chats] == [
        ("telegram", "778", "42", "@ada_l")]
    # No loom code rode on it: nothing for the daemon to pair on.
    assert client.get("/v1/daemons/inbox", params={"since": 0, "wait": 0}, headers=daemon).json()["events"] == []

    assert client.delete(f"/v1/daemons/chats/{chats[0]['id']}", headers=daemon).status_code == 200
    assert client.get("/v1/daemons/chats", headers=daemon).json()["chats"] == []
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

    # Their web chat is their own login: the approver's, never the other account's.
    assert client.get("/v1/daemons/chats", headers=theirs).json()["login"] == "mallory"
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

    assert client.get("/v1/daemons/chats", headers=daemon).json()["chats"] == []


def _stub_brnrd(tmp_path, chats_after_door, login=None):
    """A brnrd.dev that lists ``chats_after_door`` once a codeless door was minted (none before, unless it is pre-bound)."""
    from brr.gates import cloud
    from brr.loom.runtime.config import seed_config, set_value

    seen = []

    class Server(http.server.BaseHTTPRequestHandler):
        def _reply(self, body):
            raw = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            seen.append(("GET", self.path, None))
            doored = any(path == "/v1/daemons/door" for _, path, _ in seen)
            self._reply({"chats": chats_after_door if doored or chats_after_door is PRE else [],
                         "links": {"telegram": "https://t.me/brnrd_bot", "whatsapp": "https://wa.me/15550001"},
                         **({"login": login} if login else {})})

        def do_POST(self):
            size = int(self.headers.get("Content-Length") or 0)
            seen.append(("POST", self.path, json.loads(self.rfile.read(size))))
            self._reply({"name": "@brnrd_bot", "link": "https://t.me/brnrd_bot?start=PK-AB12",
                         "whatsapp": "https://wa.me/15550001?text=PK-AB12"})

        def log_message(self, *_):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Server)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    seed_config(tmp_path)
    cloud._save_state_to_dir(tmp_path / "loom", dict(brnrd_url=f"http://127.0.0.1:{server.server_port}",
                                                    token="bd_tok", since=0))
    set_value(tmp_path, "relay_state", str(tmp_path / "loom"))
    return server, seen


_ADA = {"id": "cr_1", "platform": "telegram", "chat_id": "778", "topic_id": None, "principal_id": "42",
        "principal_display": "@ada_l", "chat_title": None, "paired_at": "2026-10-30T12:00:00Z"}
PRE = [_ADA]


def test_setup_uses_a_chat_already_bound_to_the_account_without_a_code(tmp_path):
    """Arseni 9924: a chat bound once per account; setup only connects the computer."""
    from brr.loom.runtime import connector, setup_channel
    from brr.loom.runtime.config import load_config
    from brr.loom.runtime.home import Home

    server, seen = _stub_brnrd(tmp_path, PRE)
    said = []
    try:
        key = setup_channel.pair(Home(tmp_path), "relay", "cloud", "ada", timeout=5, out=said.append, qr=False)
    finally:
        server.shutdown()
    assert key == "cloud:telegram:42"
    # Nothing minted, nothing to type: the binding is the server's.
    assert [(m, p) for m, p, _ in seen] == [("GET", "/v1/daemons/chats")]
    assert "cloud=relay" in load_config(tmp_path).connectors
    # The letter `in` writes from that chat comes from the person.
    assert connector._sender(Home(tmp_path), "cloud", "telegram:42") == "person:ada"
    assert any("Telegram chat is bound" in line and "@ada_l" in line for line in said)


def test_setup_with_no_bound_chat_shows_the_account_door_and_reads_the_binding_back(tmp_path, monkeypatch):
    from brr.loom.runtime import connector, setup_channel
    from brr.loom.runtime.home import Home

    monkeypatch.setattr(setup_channel, "BOUND_POLL_S", 0.01)
    server, seen = _stub_brnrd(tmp_path, [dict(_ADA, platform="whatsapp", chat_id="4915112345678",
                                               principal_id="+4915112345678", principal_display=None)])
    said = []
    try:
        key = setup_channel.pair(Home(tmp_path), "relay", "cloud", "ada", timeout=5, out=said.append, qr=False)
    finally:
        server.shutdown()
    # The door carries no loom code: the chat binds to the account, not to this computer.
    assert ("POST", "/v1/daemons/door", {}) in seen
    assert "https://t.me/brnrd_bot?start=PK-AB12" in said[0] and "PK-AB12" in said[0]
    # Fresh-user run (9967): the missing-chat message names WhatsApp as well as Telegram.
    assert "WhatsApp: open https://wa.me/15550001?text=PK-AB12" in said[0]
    # WhatsApp's sender is its chat, as `in` writes it.
    assert key == "cloud:whatsapp:4915112345678"
    assert connector._sender(Home(tmp_path), "cloud", "whatsapp:4915112345678") == "person:ada"


def test_setup_gives_up_when_no_chat_is_bound_in_time(tmp_path, monkeypatch):
    from brr.loom.runtime import setup_channel
    from brr.loom.runtime.config import load_config
    from brr.loom.runtime.home import Home

    monkeypatch.setattr(setup_channel, "BOUND_POLL_S", 0.01)
    server, _seen = _stub_brnrd(tmp_path, [])
    try:
        with pytest.raises(TimeoutError):
            setup_channel.pair(Home(tmp_path), "relay", "cloud", "ada", timeout=0.2, out=lambda _: None, qr=False)
    finally:
        server.shutdown()
    assert not load_config(tmp_path).connectors


def test_setup_enrols_the_approvers_web_chat_and_another_accounts_stays_a_stranger(tmp_path):
    """cloud-enrol: the account that approved this computer writes its web
    chat as ``loom:<login>``; setup saves it beside the phone chat, and a
    letter from any other login is a stranger the loom never answers."""
    from brr.loom.runtime import connector, setup_channel
    from brr.loom.runtime.home import Home, atomic_write

    server, seen = _stub_brnrd(tmp_path, PRE, login="Gurio")
    said = []
    try:
        key = setup_channel.pair(Home(tmp_path), "relay", "cloud", "ada", timeout=5, out=said.append, qr=False)
    finally:
        server.shutdown()
    # The phone chat stays first: it is the one the board writes to.
    assert key == "cloud:telegram:42"
    assert [(m, p) for m, p, _ in seen] == [("GET", "/v1/daemons/chats")]
    home = Home(tmp_path)
    atomic_write(tmp_path / "self/people/mallory/channels.toml", 'channels = ["cloud:telegram:7"]\n')
    assert connector._sender(home, "cloud", "loom:Gurio") == "person:ada"
    assert connector.person_dm(home, "channel:cloud/loom:Gurio") == "person:ada"
    assert connector._sender(home, "cloud", "loom:mallory") == "stranger:cloud:loom:mallory"
    assert connector.person_dm(home, "channel:cloud/loom:mallory") is None
    assert any("brnrd.dev chat (Gurio)" in line for line in said)


def test_a_web_chat_alone_still_offers_the_phone_door(tmp_path, monkeypatch):
    """The web chat is saved, but it is not a phone chat: with only it, the door is still shown."""
    from brr.loom.runtime import connector, setup_channel
    from brr.loom.runtime.home import Home

    monkeypatch.setattr(setup_channel, "BOUND_POLL_S", 0.01)
    server, seen = _stub_brnrd(tmp_path, [_ADA], login="Gurio")
    try:
        key = setup_channel.pair(Home(tmp_path), "relay", "cloud", "ada", timeout=5, out=lambda _: None, qr=False)
    finally:
        server.shutdown()
    assert ("POST", "/v1/daemons/door", {}) in seen
    assert key == "cloud:telegram:42"
    assert connector._sender(Home(tmp_path), "cloud", "loom:Gurio") == "person:ada"


def test_a_codeless_door_offers_the_same_code_on_whatsapp_where_brnrd_has_a_number(env):
    """The code that binds on Telegram binds texted alone on WhatsApp too; the door says where."""
    from brnrd.messenger_doors import MessengerIdentities

    client, _ = env
    _account, daemon, _repo = brnrd_repo_and_daemon(client)
    assert client.post("/v1/daemons/door", json={}, headers=daemon).json()["whatsapp"] is None
    client.app.state.messenger_identities = MessengerIdentities(telegram_bot_username="brnrd_bot",
                                                                whatsapp_e164="15550001")
    try:
        door = client.post("/v1/daemons/door", json={}, headers=daemon).json()
        links = client.get("/v1/daemons/chats", headers=daemon).json()["links"]
    finally:
        del client.app.state.messenger_identities
    code = door["link"].removeprefix("https://t.me/brnrd_bot?start=")
    assert door["whatsapp"] == f"https://wa.me/15550001?text={code}"
    assert links == {"telegram": "https://t.me/brnrd_bot", "whatsapp": "https://wa.me/15550001"}


def test_setup_hands_back_a_link_to_every_chat_it_enabled(tmp_path):
    """Fresh-user run (9967): the 'Say hello' step links each enabled chat."""
    from brr.loom.runtime import setup_channel
    from brr.loom.runtime.home import Home

    server, _seen = _stub_brnrd(tmp_path, PRE, login="Gurio")
    links = []
    try:
        setup_channel.pair(Home(tmp_path), "relay", "cloud", "ada", timeout=5, out=lambda _: None, qr=False,
                           links=links)
    finally:
        server.shutdown()
    assert links == [("Telegram", "https://t.me/brnrd_bot"),
                     ("brnrd.dev", f"http://127.0.0.1:{server.server_port}")]


def test_setup_run_again_reads_each_door_and_offers_to_mend_the_dark_one(tmp_path, capsys):
    """Item 4: a chat unbound on brnrd.dev after setup is seen on the rerun, and binding again keeps one connector."""
    from brr.loom.runtime import setup_channel
    from brr.loom.runtime.config import load_config
    from brr.loom.runtime.home import Home

    server, _seen = _stub_brnrd(tmp_path, PRE)
    try:
        setup_channel.pair(Home(tmp_path), "relay", "cloud", "ada", timeout=5, out=lambda _: None, qr=False)
        assert setup_channel.door_states(Home(tmp_path)) == {"cloud": ("relay", True, "Telegram bound")}
    finally:
        server.shutdown()
    server, _seen = _stub_brnrd(tmp_path, [])
    try:
        assert setup_channel.door_states(Home(tmp_path)) == {"cloud": ("relay", False, "no chat bound any more")}
        assert setup_channel._status(Home(tmp_path)) == 0
        assert "talks over cloud, but no chat bound any more: brnrd setup channel" in capsys.readouterr().out
    finally:
        server.shutdown()
    # Bound again under the same name: still one connector.
    server, _seen = _stub_brnrd(tmp_path, PRE)
    try:
        assert setup_channel._channel_name(Home(tmp_path), "relay") == "cloud"
        setup_channel.pair(Home(tmp_path), "relay", "cloud", "ada", timeout=5, out=lambda _: None, qr=False)
    finally:
        server.shutdown()
    assert load_config(tmp_path).connectors.count("cloud=relay") == 1

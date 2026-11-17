"""The all-tier ceiling on the routes an install calls (``limits.Ceiling``).

Through the routes: sends and reports are separate lanes, a refusal is 429 with
``{reason, message}`` and ``Retry-After``, the window turning admits again, and
the person hears about it once per episode, not once per refusal. The incoming
path's limit reply is once per episode too. On the install: a 429 from the
relay is ``not-sent`` (never ``maybe-sent``), and ``cloud._request`` holds the
refused path until Retry-After instead of asking on every tick.
"""

from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient  # noqa: E402

from brnrd import create_app, inbox as inbox_service, limits  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from _helpers import brnrd_account_headers, brnrd_client, brnrd_repo_and_daemon, repo_telegram_pair  # noqa: E402


class Clock:
    def __init__(self, at: float = 1_000_040.0):  # 20 s into a minute window
        self.at = at

    def __call__(self) -> float:
        return self.at


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(limits, "CEILING", limits.Ceiling(clock=c))
    return c


@pytest.fixture
def told(monkeypatch):
    sent: list[tuple[str, str]] = []

    def fake_fresh(token, chat_id, text, *, topic_id=None, **_kw):
        sent.append((str(chat_id), text))
        return str(900 + len(sent))

    monkeypatch.setattr("brnrd.platforms.telegram.send_fresh_message", fake_fresh)
    return sent


def _app(**limits_kw):
    kw = dict(limit_abuse_sends_per_minute=2, limit_abuse_reports_per_minute=3, telegram_bot_token="bot:T")
    kw.update(limits_kw)
    client = brnrd_client(**kw)
    _account, daemon, repo_id = brnrd_repo_and_daemon(client)
    assert client.post("/v1/daemons/register", json={"daemon_name": "laptop"}, headers=daemon).status_code == 200
    return client, daemon, repo_id


def _chat(client, repo_id, chat_id="42"):
    with client.app.state.SessionLocal() as db:
        inbox_service.enqueue(db, repo_id=repo_id, body="hello", source="telegram",
                              reply_to={"platform": "telegram", "chat_id": chat_id, "message_id": 1, "user_id": 7})


def _pack(client, daemon):
    return client.post("/v1/daemons/pack", json={"pack": {"x": 1}}, headers=daemon)


def _report(client, daemon):
    return client.put("/v1/daemons/run-ledger", json={"rows": []}, headers=daemon)


def test_sends_refused_past_the_ceiling_with_reason_and_retry_after(clock, told):
    client, daemon, _repo = _app()
    assert _pack(client, daemon).status_code == 200
    assert _pack(client, daemon).status_code == 200
    refused = _pack(client, daemon)
    assert refused.status_code == 429
    assert refused.json()["detail"]["reason"] == "abuse_sends_rate"
    assert "60/min" not in refused.json()["detail"]["message"]  # the configured number, not the default
    assert "2/min" in refused.json()["detail"]["message"]
    assert refused.headers["Retry-After"] == "40"


def test_lanes_are_separate_and_the_window_turning_admits_again(clock, told):
    client, daemon, _repo = _app()
    for _ in range(3):
        assert _report(client, daemon).status_code == 200
    assert _report(client, daemon).status_code == 429
    assert _pack(client, daemon).status_code == 200  # a flooded report lane does not stop a send
    clock.at += 40
    assert _report(client, daemon).status_code == 200


def test_a_refused_call_is_not_counted(clock, told):
    client, daemon, _repo = _app()
    _pack(client, daemon), _pack(client, daemon)
    for _ in range(20):
        assert _pack(client, daemon).status_code == 429
    clock.at += 40
    assert _pack(client, daemon).status_code == 200
    assert _pack(client, daemon).status_code == 200


def test_the_ceiling_binds_every_route_it_names(clock, told):
    client, daemon, _repo = _app(limit_abuse_sends_per_minute=0, limit_abuse_reports_per_minute=0)
    sends = [("post", "/v1/daemons/responses"), ("post", "/v1/daemons/messages"),
             ("post", "/v1/daemons/card"), ("post", "/v1/daemons/pack")]
    reports = [("put", p) for p in ("activity", "surface", "quota", "news", "loom", "loom/parts", "runners",
                                    "live-runs", "pr-review-queue", "run-ledger")]
    reports += [("post", "/v1/daemons/loom/presence"), ("post", "/v1/daemons/loom/typing"),
                ("post", "/v1/daemons/runners/wake-request/claim")]
    for method, path in sends + [(m, p if p.startswith("/") else f"/v1/daemons/{p}") for m, p in reports]:
        got = getattr(client, method)(path, json={}, headers=daemon)
        assert got.status_code == 429, (path, got.status_code)
        assert got.json()["detail"]["reason"].startswith("abuse_"), path
    # The routes an install reads, and its door, are not behind it.
    assert client.get("/v1/daemons/whoami", headers=daemon).status_code == 200


def test_the_person_is_told_once_per_episode(clock, told):
    client, daemon, repo_id = _app()
    _chat(client, repo_id)
    for _ in range(2):
        assert _pack(client, daemon).status_code == 200
    for _ in range(5):
        assert _pack(client, daemon).status_code == 429
    clock.at += 40  # a new window, still inside the episode's quiet span
    for _ in range(2):
        _pack(client, daemon)
    assert _pack(client, daemon).status_code == 429
    assert len(told) == 1
    chat, text = told[0]
    assert chat == "42" and "sends" in text and "2/min" in text
    clock.at += 600  # ten quiet minutes end the episode
    for _ in range(3):
        _pack(client, daemon)
    assert len(told) == 2


def test_a_notice_that_fails_still_answers_429(clock, monkeypatch):
    def boom(*_a, **_kw):
        raise RuntimeError("telegram down")

    monkeypatch.setattr("brnrd.platforms.telegram.send_fresh_message", boom)
    client, daemon, repo_id = _app()
    _chat(client, repo_id)
    _pack(client, daemon), _pack(client, daemon)
    assert _pack(client, daemon).status_code == 429


def test_incoming_limit_reply_is_once_per_episode_not_per_message(clock, monkeypatch):
    replies: list[str] = []
    monkeypatch.setattr("brnrd.platforms.telegram.send_message",
                        lambda token, chat_id, text, **_kw: replies.append(text))
    secret = "tg-secret"
    client = TestClient(create_app(Settings(
        database_url="sqlite:///:memory:", telegram_bot_token="bot:TOKEN",
        telegram_webhook_secret=secret, limit_free_events_per_minute=1,
    )))
    headers = brnrd_account_headers(client.app, github_id="123", login="octocat", email="o@example.com")
    repo_id = client.post("/v1/accounts/repos", json={"repo_full_name": "Gurio/demo"}, headers=headers).json()["repo_id"]
    code = repo_telegram_pair(client, repo_id, headers=headers).json()["pair_code"]
    hdr = {"X-Telegram-Bot-Api-Secret-Token": secret}

    def msg(text, n):
        return {"update_id": n, "message": {"chat": {"id": 7}, "from": {"id": 42, "first_name": "Ada"},
                                           "message_id": n, "date": int(time.time()), "text": text}}

    client.post("/v1/webhooks/telegram", json=msg(f"/start {code}", 1), headers=hdr)
    client.post("/v1/webhooks/telegram", json=msg("first", 2), headers=hdr)
    before = len(replies)
    for n in range(3, 9):
        assert client.post("/v1/webhooks/telegram", json=msg(f"flood {n}", n), headers=hdr).json()["ok"]
    limit_replies = [t for t in replies[before:] if "Free-tier burst limit" in t]
    assert len(limit_replies) == 1


def test_a_shape_refusal_is_still_answered_every_time(clock, monkeypatch):
    replies: list[str] = []
    monkeypatch.setattr("brnrd.platforms.telegram.send_message",
                        lambda token, chat_id, text, **_kw: replies.append(text))
    secret = "tg-secret"
    client = TestClient(create_app(Settings(
        database_url="sqlite:///:memory:", telegram_bot_token="bot:TOKEN",
        telegram_webhook_secret=secret, limit_max_event_body_bytes=10,
    )))
    headers = brnrd_account_headers(client.app, github_id="123", login="octocat", email="o@example.com")
    repo_id = client.post("/v1/accounts/repos", json={"repo_full_name": "Gurio/demo"}, headers=headers).json()["repo_id"]
    code = repo_telegram_pair(client, repo_id, headers=headers).json()["pair_code"]
    hdr = {"X-Telegram-Bot-Api-Secret-Token": secret}

    def msg(text, n):
        return {"update_id": n, "message": {"chat": {"id": 7}, "from": {"id": 42, "first_name": "Ada"},
                                           "message_id": n, "date": int(time.time()), "text": text}}

    client.post("/v1/webhooks/telegram", json=msg(f"/start {code}", 1), headers=hdr)
    before = len(replies)
    for n in range(2, 5):
        client.post("/v1/webhooks/telegram", json=msg("a message far too long", n), headers=hdr)
    assert len([t for t in replies[before:] if "per-event limit" in t]) == 3


# --- the install side, through its callers ------------------------------------

import connector_support as relay  # noqa: E402
from brr.daemon2.leases import LocalLeaseAuthority  # noqa: E402
from brr.gates import cloud  # noqa: E402
from brr.loom.runtime import speak as speak_mod  # noqa: E402
from brr.loom.runtime.config import loom_clock  # noqa: E402
from brr.loom.runtime.home import Home  # noqa: E402


class Wire:
    """``cloud._SESSION`` over the app in process, counting what reaches the server."""

    def __init__(self, client: TestClient):
        self.client, self.calls = client, []

    def request(self, method, url, *, json=None, params=None, headers=None, timeout=None):
        path = url.removeprefix("http://testserver")
        self.calls.append((method, path))
        return self.client.request(method, path, json=json, params=params, headers=headers)


class ServerRelay(relay.RelayClient):
    """The relay's real client; only its cloud state comes from the test."""

    def __init__(self, token: str):
        self.token = token

    def _state(self):
        return {"brnrd_url": "http://testserver", "token": self.token}


@pytest.fixture
def wired(clock, told, monkeypatch):
    client, daemon, repo_id = _app(limit_abuse_sends_per_minute=1, limit_abuse_reports_per_minute=1)
    _chat(client, repo_id)
    wire = Wire(client)
    monkeypatch.setattr(cloud, "_SESSION", wire)
    monkeypatch.setattr(cloud, "_HELD_UNTIL", {})
    return client, daemon, wire


def test_a_refused_reply_is_not_sent_and_its_thread_is_told(wired, tmp_path, monkeypatch):
    _client, daemon, wire = wired
    home = Home(tmp_path / "home")
    home.root.mkdir(parents=True)
    person = home.root / "self" / "people" / "ada"
    person.mkdir(parents=True)
    (person / "channels.toml").write_text('channels = ["relay:telegram:42"]\n')
    relay.enable(home)
    relay.install_out(monkeypatch, home, ServerRelay(daemon["Authorization"].split(" ", 1)[1]))
    lease = LocalLeaseAuthority(home.root / "ledger" / "leases", clock=loom_clock).acquire(
        "router", home.install_id(), 600)

    first = speak_mod.speak(home, lease, "s-a/one", "first", channel="channel:relay/telegram:42")
    assert first[0] == "sent"
    status, fact, unsure = speak_mod.speak(home, lease, "s-a/two", "second", channel="channel:relay/telegram:42")
    # Refused before anything left: not sent, never "maybe", and the reason names the ceiling.
    assert status == "not-sent" and not unsure
    assert "429" in fact.data["reason"] and "abuse_sends_rate" in fact.data["reason"]
    # One call per letter: the refusal is not retried.
    assert wire.calls.count(("POST", "/v1/daemons/messages")) == 2


def test_cloud_request_holds_a_refused_path_until_retry_after(wired, clock, monkeypatch):
    client, daemon, wire = wired
    token = daemon["Authorization"].split(" ", 1)[1]
    put = lambda: cloud._request("http://testserver", "PUT", "/v1/daemons/run-ledger", token=token, json={"rows": []})  # noqa: E731
    put()
    with pytest.raises(RuntimeError) as refused:
        put()
    assert refused.value.status_code == 429 and refused.value.retry_after == 40
    for _ in range(5):  # a publish lane's ticks inside the window ask nothing of the server
        with pytest.raises(RuntimeError) as held:
            put()
        assert held.value.status_code == 429
    assert wire.calls.count(("PUT", "/v1/daemons/run-ledger")) == 2
    # Another path is not held by this one.
    cloud._request("http://testserver", "POST", "/v1/daemons/pack", token=token, json={"pack": {}})
    real = time.monotonic
    monkeypatch.setattr(cloud.time, "monotonic", lambda: real() + 41)
    clock.at += 41
    put()
    assert wire.calls.count(("PUT", "/v1/daemons/run-ledger")) == 3

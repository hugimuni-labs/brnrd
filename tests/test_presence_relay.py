"""Tests for presence, the relay half (design-the-continuous-seat.md §Presence):
the four chat commands, typing-out dispatch on `composing`, and WhatsApp
read-status recording."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from brnrd import create_app  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.models import ChannelRoute, Event  # noqa: E402
from brnrd.routers import daemons as daemons_router  # noqa: E402
from _helpers import PUBLISH_EVERYTHING, brnrd_account_headers  # noqa: E402

_TG_SECRET = "webhook-secret"
_TG_HDR = {"X-Telegram-Bot-Api-Secret-Token": _TG_SECRET}
_WA_SECRET = "wa-app-secret"


def _make_client(monkeypatch, **overrides):
    tg_sends: list[dict] = []
    wa_sends: list[dict] = []

    def fake_tg_send(token, chat_id, text, *, topic_id=None, reply_to_message_id=None, timeout=30.0):
        tg_sends.append({"chat_id": chat_id, "text": text})

    def fake_wa_send(access_token, phone_number_id, to, text, **kw):
        wa_sends.append({"to": to, "text": text})
        return "wamid.fake"

    monkeypatch.setattr("brnrd.platforms.telegram.send_message", fake_tg_send)
    monkeypatch.setattr("brnrd.platforms.whatsapp.send_message", fake_wa_send)
    settings = Settings(
        database_url="sqlite:///:memory:",
        telegram_bot_token="bot:TOKEN",
        telegram_webhook_secret=_TG_SECRET,
        whatsapp_app_secret=_WA_SECRET,
        whatsapp_access_token="wa-token",
        whatsapp_phone_number_id="123456",
        inbox_long_poll_max_s=0.2,
        inbox_poll_interval_s=0.02,
        **overrides,
    )
    app = create_app(settings)
    return app, TestClient(app), tg_sends, wa_sends


@pytest.fixture()
def env(monkeypatch):
    return _make_client(monkeypatch)


def _account(client):
    return brnrd_account_headers(client.app, github_id="900", login="pika", email="p@b.com")


def _repo(client, headers, name="demo"):
    return client.post(
        "/v1/accounts/repos",
        json={"repo_full_name": f"Gurio/{name}", "publish_layers": PUBLISH_EVERYTHING},
        headers=headers,
    ).json()["repo_id"]


def _tg_pair_code(client, headers, repo_id):
    return client.post(
        "/v1/accounts/pair/telegram", json={"repo_id": repo_id}, headers=headers,
    ).json()["pair_code"]


def _tg_message(chat_id, text, *, message_id=1, user_id=42, username="ada_l"):
    return {
        "update_id": message_id,
        "message": {
            "chat": {"id": chat_id},
            "from": {"id": user_id, "first_name": "Ada", "username": username},
            "message_id": message_id,
            "date": int(time.time()),
            "text": text,
        },
    }


def _pair_telegram(client, headers, repo_id, chat_id=555):
    code = _tg_pair_code(client, headers, repo_id)
    client.post("/v1/webhooks/telegram", json=_tg_message(chat_id, f"/start {code}"), headers=_TG_HDR)


# The presence *commands* (/afk /hush /urgent-only /back) were removed
# 2026-09-09 at his steer (evt-…-dfxc, -g6hj): tempo is words to the
# resident, never parsed by the relay. What stays: typing-out, read status.


def test_whatsapp_read_status_records_read_at(env, monkeypatch):
    app, client, _, _ = env
    acc = _account(client)
    rid = _repo(client, acc)
    code = client.post(
        "/v1/accounts/pair/telegram", json={"repo_id": rid}, headers=acc,
    ).json()["pair_code"]
    monkeypatch.setattr(
        "brnrd.routers.webhooks._hub_signature_ok", lambda secret, body, sig: True,
    )
    pair_body = {"entry": [{"changes": [{"value": {
        "contacts": [{"profile": {"name": "Bo"}}],
        "messages": [{"from": "15559998888", "id": "wamid.p1", "type": "text",
                      "timestamp": str(int(time.time())), "text": {"body": code}}],
    }}]}]}
    client.post("/v1/webhooks/whatsapp", json=pair_body, headers={"X-Hub-Signature-256": "sha256=x"})

    status_body = {"entry": [{"changes": [{"value": {"statuses": [
        {"id": "wamid.OUT1", "status": "read", "timestamp": str(int(time.time())),
         "recipient_id": "15559998888"},
    ]}}]}]}
    r = client.post("/v1/webhooks/whatsapp", json=status_body, headers={"X-Hub-Signature-256": "sha256=x"})
    assert r.status_code == 200

    with app.state.SessionLocal() as db:
        route = db.execute(
            select(ChannelRoute).where(ChannelRoute.platform == "whatsapp", ChannelRoute.channel_id == "15559998888")
        ).scalar_one()
        assert route.last_read_message_id == "wamid.OUT1"
        assert route.last_read_at is not None


# ── typing-out dispatch ─────────────────────────────────────────────


def _daemon_headers(client, acc, repo_id):
    pair = client.post("/v1/accounts/pair").json()
    client.post(
        f"/v1/accounts/pair/{pair['pair_code']}/approve",
        json={"repo_id": repo_id, "approve_secret": pair["approve_secret"]},
        headers=acc,
    )
    token = client.get(
        f"/v1/accounts/pair/{pair['pair_code']}", params={"poll_secret": pair["poll_secret"]},
    ).json()["daemon_token"]
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/v1/daemons/register", json={"daemon_name": "laptop"}, headers=headers)
    return headers


def test_composing_row_sends_one_chat_action_per_window(env, monkeypatch):
    app, client, _, _ = env
    acc = _account(client)
    rid = _repo(client, acc)
    headers = _daemon_headers(client, acc, rid)

    daemons_router._last_typing_sent.clear()
    calls: list[tuple] = []
    monkeypatch.setattr(
        "brnrd.platforms.telegram.send_chat_action",
        lambda token, chat_id, topic_id=None: calls.append((chat_id, topic_id)),
    )
    row = {
        "id": "run-a",
        "repo_label": "Gurio/demo",
        "stream": "cloud:telegram:555:",
        "composing": True,
    }
    r1 = client.put("/v1/daemons/live-runs", json={"runs": [row]}, headers=headers)
    assert r1.status_code == 200
    r2 = client.put("/v1/daemons/live-runs", json={"runs": [row]}, headers=headers)
    assert r2.status_code == 200

    assert calls == [("555", None)]


def test_composing_false_sends_nothing(env, monkeypatch):
    app, client, _, _ = env
    acc = _account(client)
    rid = _repo(client, acc)
    headers = _daemon_headers(client, acc, rid)
    daemons_router._last_typing_sent.clear()
    calls: list[tuple] = []
    monkeypatch.setattr(
        "brnrd.platforms.telegram.send_chat_action",
        lambda token, chat_id, topic_id=None: calls.append((chat_id, topic_id)),
    )
    row = {"id": "run-a", "repo_label": "Gurio/demo", "stream": "cloud:telegram:555:", "composing": False}
    client.put("/v1/daemons/live-runs", json={"runs": [row]}, headers=headers)
    assert calls == []


def test_cloud_chat_target_parses_and_rejects():
    assert daemons_router._cloud_chat_target("cloud:telegram:555:") == ("telegram", "555", None)
    assert daemons_router._cloud_chat_target("cloud:telegram:555:9") == ("telegram", "555", 9)
    assert daemons_router._cloud_chat_target("telegram:555:") is None
    assert daemons_router._cloud_chat_target("spawn:default") is None
    assert daemons_router._cloud_chat_target("") is None

"""Tests for free-tier headroom limits + abuse ceilings (``limits.py``).

Covers the #501 repo-cap half and the decision-ledger contract
(2026-07-21): free-tier reject, supporter pass (entitlement derived from
subscription state through the real Stripe webhook path), abuse ceilings
binding every tier, unreadable-billing fallback (headroom open, abuse
closed), burst throttle, and the Telegram polite drop.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from brnrd import create_app  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.models import Account, Event  # noqa: E402
from _helpers import brnrd_account_headers  # noqa: E402

WEBHOOK_SECRET = "whsec_test"


def _client(**overrides) -> TestClient:
    settings = Settings(
        database_url="sqlite:///:memory:",
        stripe_webhook_secret=WEBHOOK_SECRET,
        stripe_price_supporter_monthly="price_sup_m",
        **overrides,
    )
    return TestClient(create_app(settings))


def _account(client: TestClient, github_id: str = "123", login: str = "octocat"):
    return brnrd_account_headers(client.app, github_id=github_id, login=login, email=f"{login}@example.com")


def _account_id(client: TestClient) -> str:
    with client.app.state.SessionLocal() as db:
        return db.execute(select(Account)).scalars().first().id


def _subscribe(client: TestClient, account_id: str) -> None:
    """Flip the account to supporter through the verified webhook path —
    entitlement must derive from subscription state, never a set flag."""
    event = {
        "id": f"evt_sub_{account_id}",
        "type": "customer.subscription.created",
        "data": {
            "object": {
                "id": f"sub_{account_id}",
                "status": "active",
                "customer": f"cus_{account_id}",
                "cancel_at_period_end": False,
                "current_period_end": 2000000000,
                "metadata": {"brnrd_account_id": account_id},
                "items": {"data": [{"price": {"id": "price_sup_m", "recurring": {"interval": "month"}}}]},
            }
        },
    }
    body = json.dumps(event).encode()
    ts = str(int(time.time()))
    mac = hmac.new(WEBHOOK_SECRET.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    out = client.post(
        "/v1/webhooks/stripe",
        content=body,
        headers={"Stripe-Signature": f"t={ts},v1={mac}", "Content-Type": "application/json"},
    )
    assert out.status_code == 200, out.text


def _connect(client: TestClient, headers: dict, name: str):
    return client.post(
        "/v1/accounts/repos", json={"repo_full_name": f"Gurio/{name}"}, headers=headers
    )


def _enqueue(client: TestClient, headers: dict, repo_id: str, body: str = "hi", attachments=None):
    payload = {"repo_id": repo_id, "body": body}
    if attachments is not None:
        payload["attachments"] = attachments
    return client.post("/v1/_dev/enqueue", json=payload, headers=headers)


def _detail(response) -> dict:
    detail = response.json()["detail"]
    assert isinstance(detail, dict), detail
    return detail


# --- repo cap (#501) ---------------------------------------------------------


def test_free_repo_cap_rejects_with_reason_and_upgrade_path():
    client = _client(limit_free_repos=2)
    headers = _account(client)
    assert _connect(client, headers, "one").status_code == 201
    assert _connect(client, headers, "two").status_code == 201

    denied = _connect(client, headers, "three")
    assert denied.status_code == 403
    detail = _detail(denied)
    assert detail["reason"] == "free_repo_limit"
    assert "supporter" in detail["message"]

    # Reconnecting an existing repo stays idempotent and uncapped.
    assert _connect(client, headers, "one").status_code == 201


def test_supporter_lifts_repo_cap_but_abuse_ceiling_holds():
    client = _client(limit_free_repos=1, limit_abuse_repos=3)
    headers = _account(client)
    _subscribe(client, _account_id(client))

    for name in ("one", "two", "three"):
        assert _connect(client, headers, name).status_code == 201
    denied = _connect(client, headers, "four")
    assert denied.status_code == 403
    assert _detail(denied)["reason"] == "abuse_repo_ceiling"


# --- event throttling --------------------------------------------------------


def test_free_burst_throttle_rejects_third_event():
    client = _client(limit_free_events_per_minute=2)
    headers = _account(client)
    repo_id = _connect(client, headers, "demo").json()["repo_id"]

    assert _enqueue(client, headers, repo_id).status_code == 201
    assert _enqueue(client, headers, repo_id).status_code == 201
    denied = _enqueue(client, headers, repo_id)
    assert denied.status_code == 429
    detail = _detail(denied)
    assert detail["reason"] == "free_event_burst"
    assert "supporter" in detail["message"]


def test_free_daily_ceiling():
    client = _client(limit_free_events_per_minute=100, limit_free_events_per_day=3)
    headers = _account(client)
    repo_id = _connect(client, headers, "demo").json()["repo_id"]

    for _ in range(3):
        assert _enqueue(client, headers, repo_id).status_code == 201
    denied = _enqueue(client, headers, repo_id)
    assert denied.status_code == 429
    assert _detail(denied)["reason"] == "free_daily_events"


def test_supporter_passes_free_limits_but_abuse_rate_binds():
    client = _client(limit_free_events_per_minute=1, limit_abuse_events_per_minute=3)
    headers = _account(client)
    repo_id = _connect(client, headers, "demo").json()["repo_id"]
    _subscribe(client, _account_id(client))

    # Sails past the free burst limit...
    for _ in range(3):
        assert _enqueue(client, headers, repo_id).status_code == 201
    # ...but the abuse ceiling is protection for every tier.
    denied = _enqueue(client, headers, repo_id)
    assert denied.status_code == 429
    assert _detail(denied)["reason"] == "abuse_event_rate"


def test_unreadable_billing_fails_open_for_headroom_closed_for_abuse(monkeypatch):
    def boom(db, account):
        raise RuntimeError("billing store unreachable")

    monkeypatch.setattr("brnrd.billing.entitlements", boom)

    client = _client(
        limit_free_events_per_minute=1,
        limit_abuse_events_per_minute=3,
        limit_free_repos=1,
        limit_abuse_repos=2,
    )
    headers = _account(client)
    repo_id = _connect(client, headers, "demo").json()["repo_id"]

    # Headroom fails open: the free burst limit (1/min) must not bind.
    for _ in range(3):
        assert _enqueue(client, headers, repo_id).status_code == 201
    # Abuse ceilings fail closed: they never depended on billing state.
    denied = _enqueue(client, headers, repo_id)
    assert denied.status_code == 429
    assert _detail(denied)["reason"] == "abuse_event_rate"

    # Same posture on the repo cap: free cap (1) open, abuse cap (2) closed.
    assert _connect(client, headers, "two").status_code == 201
    denied = _connect(client, headers, "three")
    assert denied.status_code == 403
    assert _detail(denied)["reason"] == "abuse_repo_ceiling"


# --- payload abuse caps ------------------------------------------------------


def test_event_body_size_cap():
    client = _client(limit_max_event_body_bytes=100)
    headers = _account(client)
    repo_id = _connect(client, headers, "demo").json()["repo_id"]
    denied = _enqueue(client, headers, repo_id, body="x" * 200)
    assert denied.status_code == 413
    assert _detail(denied)["reason"] == "event_body_too_large"


def test_event_attachment_count_cap():
    client = _client(limit_max_event_attachments=2)
    headers = _account(client)
    repo_id = _connect(client, headers, "demo").json()["repo_id"]
    attachments = [{"file_id": f"f{i}", "filename": f"{i}.png", "kind": "photo"} for i in range(3)]
    denied = _enqueue(client, headers, repo_id, attachments=attachments)
    assert denied.status_code == 413
    assert _detail(denied)["reason"] == "too_many_attachments"


# --- telegram polite drop ----------------------------------------------------


def test_telegram_over_limit_gets_one_line_reply_not_silence(monkeypatch):
    sends: list[str] = []

    def fake_send(token, chat_id, text, *, topic_id=None, reply_to_message_id=None, timeout=30.0):
        sends.append(text)

    monkeypatch.setattr("brnrd.platforms.telegram.send_message", fake_send)
    secret = "tg-secret"
    settings = Settings(
        database_url="sqlite:///:memory:",
        telegram_bot_token="bot:TOKEN",
        telegram_webhook_secret=secret,
        limit_free_events_per_minute=1,
    )
    client = TestClient(create_app(settings))
    headers = _account(client)
    repo_id = _connect(client, headers, "demo").json()["repo_id"]
    code = client.post(
        "/v1/accounts/pair/telegram", json={"repo_id": repo_id}, headers=headers
    ).json()["pair_code"]

    def _msg(text, message_id):
        return {
            "update_id": message_id,
            "message": {
                "chat": {"id": 7},
                "from": {"id": 42, "first_name": "Ada"},
                "message_id": message_id,
                "date": int(time.time()),
                "text": text,
            },
        }

    hdr = {"X-Telegram-Bot-Api-Secret-Token": secret}
    assert client.post("/v1/webhooks/telegram", json=_msg(f"/start {code}", 1), headers=hdr).json()["ok"]
    assert client.post("/v1/webhooks/telegram", json=_msg("first task", 2), headers=hdr).json()["ok"]
    assert client.post("/v1/webhooks/telegram", json=_msg("second task", 3), headers=hdr).json()["ok"]

    # Exactly one event enqueued; the second message earned a reply naming
    # the limit — a polite drop, never a silent one.
    with client.app.state.SessionLocal() as db:
        assert len(list(db.execute(select(Event)).scalars())) == 1
    assert any("Free-tier burst limit" in text for text in sends)

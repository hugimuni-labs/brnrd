"""Hosted Signal bridge: signed ingress, pairing, enqueue, reply."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient
from sqlalchemy import select

from _helpers import brnrd_account_headers
from brnrd import create_app, inbox as inbox_service
from brnrd.config import Settings
from brnrd.models import ChannelRoute, Event
from brnrd.platforms import signal

SECRET = "signal-hook-secret"


def _message(sender: str, text: str, stamp: int | None = None) -> dict:
    stamp = stamp or int(time.time() * 1000)
    return {
        "jsonrpc": "2.0",
        "method": "receive",
        "params": {
            "account": "+33999999999",
            "envelope": {
                "sourceNumber": sender,
                "sourceName": "Ada",
                "timestamp": stamp,
                "dataMessage": {"timestamp": stamp, "message": text},
            },
        },
    }


@pytest.fixture()
def env(monkeypatch):
    sends: list[dict] = []

    def fake_send(api_url, token, number, to, text, *, timeout=30.0):
        sends.append({"api_url": api_url, "token": token, "number": number, "to": to, "text": text})
        return "receipt-1"

    monkeypatch.setattr(signal, "send_message", fake_send)
    settings = Settings(
        database_url="sqlite:///:memory:",
        signal_api_url="https://signal-bridge.example",
        signal_api_token="bridge-token",
        signal_number="+33999999999",
        signal_webhook_secret=SECRET,
        inbox_long_poll_max_s=0.2,
        inbox_poll_interval_s=0.02,
    )
    app = create_app(settings)
    return app, TestClient(app), sends


def _post(client: TestClient, payload: dict, *, secret: str = SECRET):
    raw = json.dumps(payload, separators=(",", ":")).encode()
    signature = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(
        "/v1/webhooks/signal",
        content=raw,
        headers={"content-type": "application/json", "x-brnrd-signal-signature": signature},
    )


def _account(client: TestClient):
    return brnrd_account_headers(client.app, github_id="123", login="octocat", email="a@b.com")


def _repo(client: TestClient, headers):
    return client.post(
        "/v1/accounts/repos",
        json={"repo_full_name": "Gurio/demo"},
        headers=headers,
    ).json()["repo_id"]


def _pair_code(client: TestClient, headers, repo_id: str) -> str:
    return client.post(
        "/v1/accounts/pair/telegram", json={"repo_id": repo_id}, headers=headers
    ).json()["pair_code"]


def test_parser_reads_upstream_json_rpc_envelope():
    parsed = signal.parse_update(_message("+15551234567", "hello", 1_700_000_000_000))
    assert parsed is not None
    assert parsed.chat_id == "+15551234567"
    assert parsed.text == "hello"
    assert parsed.message_id == "1700000000000"


def test_webhook_rejects_bad_signature(env):
    _, client, _ = env
    assert _post(client, _message("+15551234567", "hello"), secret="wrong").status_code == 403


def test_pair_then_enqueue_round_trip(env):
    app, client, sends = env
    account = _account(client)
    repo_id = _repo(client, account)
    code = _pair_code(client, account, repo_id)

    assert _post(client, _message("+15551234567", code)).status_code == 200
    with app.state.SessionLocal() as db:
        route = db.execute(select(ChannelRoute)).scalar_one()
        assert route.platform == "signal"
        assert route.channel_id == "+15551234567"
        assert route.repo_id == repo_id
    assert sends and "now reaches your resident" in sends[-1]["text"]

    sends.clear()
    assert _post(client, _message("+15551234567", "do the thing")).status_code == 200
    with app.state.SessionLocal() as db:
        event = db.execute(select(Event).where(Event.source == "signal")).scalar_one()
        assert event.body == "do the thing"
        assert inbox_service.reply_to_of(event)["platform"] == "signal"


def test_forwarder_sends_response_through_bridge(env):
    app, client, sends = env
    account = _account(client)
    repo_id = _repo(client, account)
    code = _pair_code(client, account, repo_id)
    _post(client, _message("+15551234567", code))
    sends.clear()
    _post(client, _message("+15551234567", "answer me"))
    with app.state.SessionLocal() as db:
        event = db.execute(select(Event).where(Event.source == "signal")).scalar_one()
        item = inbox_service.ForwardItem(
            event_id=event.event_id,
            reply_to=inbox_service.reply_to_of(event),
            body="the answer",
            status="done",
        )
    inbox_service.make_default_forwarder(app.state.settings)(item)
    assert sends == [{
        "api_url": "https://signal-bridge.example",
        "token": "bridge-token",
        "number": "+33999999999",
        "to": "+15551234567",
        "text": "the answer",
    }]

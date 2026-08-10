"""Tests for the WhatsApp Cloud API webhook ingress + response forwarding.

Mirrors ``test_brnrd_telegram.py``'s shape where the platform shape actually
matches (secret verification, enqueue-with-reply_to, response forwarding);
diverges where WhatsApp itself diverges (bare-code pairing instead of
``/start``, no group-chat authz gate, the 24h-window failure surface) — see
``routers/webhooks.py``'s WhatsApp section docstring for why.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from brnrd import create_app, ids, inbox as inbox_service  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.models import ChannelRoute, Event, Repo, TgPairCode  # noqa: E402
from brnrd.platforms import whatsapp as wa  # noqa: E402
from _helpers import brnrd_account_headers  # noqa: E402

_APP_SECRET = "whatsapp-app-secret"
_VERIFY_TOKEN = "verify-me"


@pytest.fixture()
def env(monkeypatch):
    sends: list[dict] = []

    def fake_send(token, phone_number_id, to, text, *, api_base_url=None,
                  api_version=None, reply_to_message_id=None, timeout=30.0):
        sends.append(
            {
                "token": token,
                "phone_number_id": phone_number_id,
                "to": to,
                "text": text,
                "reply_to_message_id": reply_to_message_id,
            }
        )

    monkeypatch.setattr("brnrd.platforms.whatsapp.send_message", fake_send)
    settings = Settings(
        database_url="sqlite:///:memory:",
        whatsapp_access_token="wa-token",
        whatsapp_phone_number_id="phone-1",
        whatsapp_verify_token=_VERIFY_TOKEN,
        whatsapp_app_secret=_APP_SECRET,
        inbox_long_poll_max_s=0.2,
        inbox_poll_interval_s=0.02,
    )
    app = create_app(settings)
    return app, TestClient(app), sends


def _account(client):
    return brnrd_account_headers(
        client.app, github_id="123", login="octocat", email="a@b.com"
    )


def _repo(client, headers, name="demo"):
    return client.post(
        "/v1/accounts/repos",
        json={"repo_full_name": f"Gurio/{name}"},
        headers=headers,
    ).json()["repo_id"]


def _pair_code(client, headers, repo_id):
    """Mint a pair code through the existing (telegram-flavored, but
    platform-agnostic) endpoint — ``TgPairCode`` carries no platform column,
    so the same code a WhatsApp user texts in works here too (see
    routers/webhooks.py's WhatsApp section docstring)."""
    return client.post(
        "/v1/accounts/pair/telegram", json={"repo_id": repo_id}, headers=headers
    ).json()["pair_code"]


def _daemon_headers(client, acc, repo_id):
    pair = client.post("/v1/accounts/pair").json()
    client.post(
        f"/v1/accounts/pair/{pair['pair_code']}/approve",
        json={"repo_id": repo_id},
        headers=acc,
    )
    token = client.get(
        f"/v1/accounts/pair/{pair['pair_code']}",
        params={"poll_secret": pair["poll_secret"]},
    ).json()["daemon_token"]
    return {"Authorization": f"Bearer {token}"}


def _sig(body: bytes, secret: str = _APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _post(client, payload: dict, *, secret: str = _APP_SECRET):
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return client.post(
        "/v1/webhooks/whatsapp",
        content=raw,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": _sig(raw, secret)},
    )


def _message(wa_id: str, text: str, *, message_id="wamid.1", ts=None, name="Ada"):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "1", "phone_number_id": "phone-1"},
                            "contacts": [{"profile": {"name": name}, "wa_id": wa_id}],
                            "messages": [
                                {
                                    "from": wa_id,
                                    "id": message_id,
                                    "timestamp": str(ts if ts is not None else int(time.time())),
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def _status_payload(wa_id: str):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "1", "phone_number_id": "phone-1"},
                            "statuses": [
                                {"id": "wamid.1", "status": "delivered", "recipient_id": wa_id}
                            ],
                        },
                    }
                ],
            }
        ],
    }


# ── signature + subscription verification ─────────────────────────────


def test_webhook_rejects_bad_signature(env):
    _, client, _ = env
    raw = json.dumps(_message("15551234567", "hi")).encode("utf-8")
    r = client.post(
        "/v1/webhooks/whatsapp",
        content=raw,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=nope"},
    )
    assert r.status_code == 403


def test_webhook_audit_names_rejection_without_sender_or_body(env, caplog):
    _, client, _ = env
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    sender = "15551234567"
    body = "private pairing attempt"
    raw = json.dumps(_message(sender, body)).encode("utf-8")
    r = client.post(
        "/v1/webhooks/whatsapp",
        content=raw,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=nope"},
    )
    assert r.status_code == 403
    audit = "\n".join(record.getMessage() for record in caplog.records)
    assert "stage=received" in audit
    assert "stage=rejected reason=bad_signature" in audit
    assert sender not in audit
    assert body not in audit
    assert len(set(re.findall(r"trace=([0-9a-f]{8})", audit))) == 1


def test_webhook_rejects_missing_signature(env):
    _, client, _ = env
    r = client.post("/v1/webhooks/whatsapp", json=_message("15551234567", "hi"))
    assert r.status_code == 403


def test_hub_challenge_echoed_on_valid_verify_token(env):
    _, client, _ = env
    r = client.get(
        "/v1/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": _VERIFY_TOKEN, "hub.challenge": "12345"},
    )
    assert r.status_code == 200
    assert r.text == "12345"


def test_hub_challenge_rejected_on_wrong_verify_token(env):
    _, client, _ = env
    r = client.get(
        "/v1/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "12345"},
    )
    assert r.status_code == 403


def test_hub_challenge_rejected_without_subscribe_mode(env):
    _, client, _ = env
    r = client.get(
        "/v1/webhooks/whatsapp",
        params={"hub.mode": "unsubscribe", "hub.verify_token": _VERIFY_TOKEN, "hub.challenge": "12345"},
    )
    assert r.status_code == 403


# ── pairing (bare code, no /start convention) ─────────────────────────


def test_pair_code_binds_chat_and_confirms(env):
    app, client, sends = env
    acc = _account(client)
    rid = _repo(client, acc, name="myrepo")
    code = _pair_code(client, acc, rid)

    r = _post(client, _message("15551234567", code))
    assert r.status_code == 200

    with app.state.SessionLocal() as db:
        route = db.execute(
            select(ChannelRoute).where(
                ChannelRoute.platform == "whatsapp", ChannelRoute.channel_id == "15551234567"
            )
        ).scalar_one()
        assert route.repo_id == rid
    assert len(sends) == 1
    assert sends[0]["to"] == "15551234567"
    assert "myrepo" in sends[0]["text"]


def test_legacy_tg_prefixed_code_still_pairs(env):
    """#1237 migration window: `ids.tg_pair_code` no longer mints the `TG-`
    shape (moved to `PK-`), but a code minted before the flip must still
    pair until it naturally expires — `_WA_PAIR_CODE_RE` accepts both.
    Inserted directly since the mint itself can't produce this shape
    anymore."""
    app, client, sends = env
    acc = _account(client)
    rid = _repo(client, acc, name="myrepo")
    with app.state.SessionLocal() as db:
        repo = db.get(Repo, rid)
        code = "TG-LEGA"
        db.add(
            TgPairCode(
                id=ids.tg_pair_code_id(),
                code=code,
                account_id=repo.account_id,
                repo_id=rid,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=600),
            )
        )
        db.commit()

    r = _post(client, _message("15551234567", code))
    assert r.status_code == 200

    with app.state.SessionLocal() as db:
        route = db.execute(
            select(ChannelRoute).where(
                ChannelRoute.platform == "whatsapp", ChannelRoute.channel_id == "15551234567"
            )
        ).scalar_one()
        assert route.repo_id == rid
    assert len(sends) == 1
    assert "myrepo" in sends[0]["text"]


def test_pairing_audit_joins_decisions_without_sender_or_code(env, caplog):
    _, client, _ = env
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    acc = _account(client)
    rid = _repo(client, acc)
    code = _pair_code(client, acc, rid)
    sender = "15551234567"

    r = _post(client, _message(sender, code))

    assert r.status_code == 200
    audit = "\n".join(record.getMessage() for record in caplog.records)
    for stage in ("received", "message_parsed", "pair_attempt", "paired"):
        assert f"stage={stage}" in audit
    assert sender not in audit
    assert code not in audit
    assert len(set(re.findall(r"trace=([0-9a-f]{8})", audit))) == 1


def test_pair_code_is_case_insensitive_and_whitespace_tolerant(env):
    app, client, sends = env
    acc = _account(client)
    rid = _repo(client, acc)
    code = _pair_code(client, acc, rid)

    r = _post(client, _message("15551234567", f"  {code.lower()}  "))
    assert r.status_code == 200
    with app.state.SessionLocal() as db:
        assert db.execute(select(ChannelRoute)).scalars().all()[0].repo_id == rid


def test_invalid_pair_code_shaped_text_is_not_treated_as_pairing(env):
    """Only the exact PK-XXXX (or legacy TG-XXXX, #1237 migration window)
    shape is a pairing attempt — an unpaired chat's ordinary message gets
    the unpaired-setup reply, not "invalid pair code" (which would leak
    that the format is being probed)."""
    app, client, sends = env
    r = _post(client, _message("15551234567", "hello there"))
    assert r.status_code == 200
    with app.state.SessionLocal() as db:
        assert db.execute(select(Event)).scalars().all() == []
    assert len(sends) == 1
    assert "not paired" in sends[0]["text"]


def test_pair_code_from_wrong_platform_family_is_not_consumed(env):
    """A BR- device-pair code (unrelated flow) never matches the WhatsApp
    pairing shape, so texting one behaves like any other unpaired message."""
    app, client, sends = env
    r = _post(client, _message("15551234567", "BR-AAAA"))
    assert r.status_code == 200
    with app.state.SessionLocal() as db:
        assert db.execute(select(ChannelRoute)).scalars().all() == []
    assert "not paired" in sends[0]["text"]


def test_unknown_shaped_code_reports_invalid(env):
    _, client, sends = env
    r = _post(client, _message("15551234567", "TG-ZZZZ"))
    assert r.status_code == 200
    assert sends and "Invalid or expired" in sends[0]["text"]


def test_expired_pair_code_names_the_retry_command(env):
    """#1282 — same fix as the Telegram `/start` path: a code that genuinely
    existed and expired (600s TTL) gets a specific, actionable message
    instead of the generic "Invalid or expired pair code." text."""
    app, client, sends = env
    acc = _account(client)
    rid = _repo(client, acc)
    code = _pair_code(client, acc, rid)
    with app.state.SessionLocal() as db:
        pc = db.execute(select(TgPairCode).where(TgPairCode.code == code)).scalar_one()
        pc.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()

    r = _post(client, _message("15551234567", code))
    assert r.status_code == 200
    assert sends
    assert "expired" in sends[0]["text"]
    assert "account connect" in sends[0]["text"]
    assert sends[0]["text"] != "Invalid or expired pair code."


# ── enqueue + response forwarding ──────────────────────────────────────


def _bound_wa_event(app, client, sends, *, message_id="wamid.42", text="do the thing"):
    acc = _account(client)
    rid = _repo(client, acc)
    code = _pair_code(client, acc, rid)
    _post(client, _message("15551234567", code))
    sends.clear()
    r = _post(client, _message("15551234567", text, message_id=message_id))
    assert r.status_code == 200
    return acc, rid


def test_bound_chat_message_enqueues_with_reply_to(env):
    app, client, sends = env
    acc, rid = _bound_wa_event(app, client, sends, message_id="wamid.42")

    with app.state.SessionLocal() as db:
        event = db.execute(select(Event).where(Event.source == "whatsapp")).scalar_one()
        assert event.repo_id == rid
        assert event.body == "do the thing"
        assert inbox_service.reply_to_of(event) == {
            "platform": "whatsapp",
            "chat_id": "15551234567",
            "message_id": "wamid.42",
        }


def test_unbound_chat_gets_setup_error(env):
    _, client, sends = env
    r = _post(client, _message("15559998888", "help me out"))
    assert r.status_code == 200
    assert len(sends) == 1
    assert sends[0]["to"] == "15559998888"
    assert "not paired" in sends[0]["text"]


def test_status_receipt_is_never_a_trigger(env):
    """A delivery/read status update for our own outbound send carries no
    ``messages`` array — never enqueued, never replied to."""
    app, client, sends = env
    r = _post(client, _status_payload("15551234567"))
    assert r.status_code == 200
    with app.state.SessionLocal() as db:
        assert db.execute(select(Event)).scalars().all() == []
    assert sends == []


def test_non_text_media_message_gets_honest_reply_and_annotated_body_if_paired(env):
    """v1 doesn't ingest WhatsApp media at all (no attachment pointers,
    unlike Telegram's image path): an unpaired-adjacent media message with
    no text gets the "can't see attached media" reply."""
    app, client, sends = env
    acc = _account(client)
    rid = _repo(client, acc)
    code = _pair_code(client, acc, rid)
    _post(client, _message("15551234567", code))
    sends.clear()

    payload = _message("15551234567", "", message_id="wamid.9")
    msg = payload["entry"][0]["changes"][0]["value"]["messages"][0]
    del msg["text"]
    msg["type"] = "image"
    msg["image"] = {"id": "media-1", "mime_type": "image/jpeg"}
    r = _post(client, payload)
    assert r.status_code == 200
    assert sends and "can't see attached media" in sends[0]["text"]
    with app.state.SessionLocal() as db:
        assert db.execute(select(Event)).scalars().all() == []


def test_response_is_forwarded_back_to_whatsapp(env):
    app, client, sends = env
    acc, rid = _bound_wa_event(app, client, sends, message_id="wamid.77")
    with app.state.SessionLocal() as db:
        event_id = db.execute(
            select(Event).where(Event.source == "whatsapp")
        ).scalar_one().event_id

    dmn = _daemon_headers(client, acc, rid)
    resp = client.post(
        "/v1/daemons/responses",
        json={"event_id": event_id, "body_markdown": "here is your answer", "status": "done"},
        headers=dmn,
    )
    assert resp.status_code == 200

    assert len(sends) == 1
    assert sends[0]["to"] == "15551234567"
    assert sends[0]["text"] == "here is your answer"
    assert sends[0]["reply_to_message_id"] == "wamid.77"


# ── the 24h customer-service window ────────────────────────────────────


def test_send_message_raises_window_closed_on_error_code_131047(monkeypatch):
    class _Resp:
        status_code = 470

        def __init__(self):
            self.content = b"1"

        def json(self):
            return {"error": {"code": 131047, "message": "Re-engagement message"}}

    monkeypatch.setattr(wa.httpx, "post", lambda *a, **k: _Resp())
    with pytest.raises(wa.WindowClosed, match="outside 24h window"):
        wa.send_message("tok", "phone-1", "1555", "hi")


def test_send_message_raises_plain_runtime_error_for_other_failures(monkeypatch):
    class _Resp:
        status_code = 400

        def __init__(self):
            self.content = b"1"

        def json(self):
            return {"error": {"code": 100, "message": "Invalid parameter"}}

    monkeypatch.setattr(wa.httpx, "post", lambda *a, **k: _Resp())
    with pytest.raises(RuntimeError) as exc_info:
        wa.send_message("tok", "phone-1", "1555", "hi")
    assert not isinstance(exc_info.value, wa.WindowClosed)


def test_window_closed_delivery_failure_surfaces_a_clear_message_and_keeps_event_queued(env, monkeypatch):
    """The 24h-window failure rides the same delivery-health channel every
    forwarder failure does (``DeliveryError`` -> 502): the daemon sees an
    honest, distinct message instead of a generic transport error, and the
    event stays queued/retriable exactly like any other forward failure
    (see test_brnrd_inbox.test_delivery_failure_keeps_event_queued_then_recovers)."""
    app, client, sends = env
    acc, rid = _bound_wa_event(app, client, sends, message_id="wamid.5")
    with app.state.SessionLocal() as db:
        event_id = db.execute(
            select(Event).where(Event.source == "whatsapp")
        ).scalar_one().event_id

    def blow_up(*a, **k):
        raise wa.WindowClosed("outside 24h window — template messages not yet supported")

    monkeypatch.setattr("brnrd.platforms.whatsapp.send_message", blow_up)
    dmn = _daemon_headers(client, acc, rid)
    resp = client.post(
        "/v1/daemons/responses",
        json={"event_id": event_id, "body_markdown": "answer", "status": "done"},
        headers=dmn,
    )
    assert resp.status_code == 502
    assert "outside 24h window" in resp.json()["detail"]

    with app.state.SessionLocal() as db:
        row = db.execute(select(Event).where(Event.event_id == event_id)).scalar_one()
        assert row.status == Event.STATUS_QUEUED
        assert row.body == "do the thing"  # the original task body, untouched


# ── parse_update shape ────────────────────────────────────────────────


def test_parse_update_ignores_status_only_payload():
    assert wa.parse_update(_status_payload("155")) is None


def test_parse_update_normalizes_text_message():
    parsed = wa.parse_update(_message("15551234567", "hello", message_id="wamid.9", name="Bob"))
    assert parsed is not None
    assert parsed.chat_id == "15551234567"
    assert parsed.user_id == "15551234567"
    assert parsed.text == "hello"
    assert parsed.message_id == "wamid.9"
    assert parsed.user == "Bob"
    assert parsed.has_media is False


def test_parse_update_flags_media_types_without_text():
    payload = _message("15551234567", "", message_id="wamid.9")
    msg = payload["entry"][0]["changes"][0]["value"]["messages"][0]
    del msg["text"]
    msg["type"] = "image"
    msg["image"] = {"id": "media-1"}
    parsed = wa.parse_update(payload)
    assert parsed is not None
    assert parsed.has_media is True
    assert parsed.text == ""

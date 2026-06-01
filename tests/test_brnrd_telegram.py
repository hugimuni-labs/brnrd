"""Tests for the Telegram webhook ingress + response forwarding."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from brnrd import create_app  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.models import ChatBinding, Event  # noqa: E402

_SECRET = "webhook-secret"
_HDR = {"X-Telegram-Bot-Api-Secret-Token": _SECRET}


@pytest.fixture()
def env(monkeypatch):
    sends: list[dict] = []

    def fake_send(token, chat_id, text, *, topic_id=None, reply_to_message_id=None,
                  timeout=30.0):
        sends.append(
            {
                "chat_id": chat_id,
                "text": text,
                "topic_id": topic_id,
                "reply_to_message_id": reply_to_message_id,
            }
        )

    monkeypatch.setattr("brnrd.platforms.telegram.send_message", fake_send)
    settings = Settings(
        database_url="sqlite:///:memory:",
        telegram_bot_token="bot:TOKEN",
        telegram_webhook_secret=_SECRET,
        inbox_long_poll_max_s=0.2,
        inbox_poll_interval_s=0.02,
    )
    app = create_app(settings)
    return app, TestClient(app), sends


def _account(client):
    key = client.post(
        "/v1/accounts", json={"email": "a@b.com", "password": "supersecret"}
    ).json()["api_key"]
    return {"Authorization": f"Bearer {key}"}


def _project(client, headers, name="demo"):
    return client.post(
        "/v1/accounts/projects", json={"name": name}, headers=headers
    ).json()["project_id"]


def _tg_pair_code(client, headers, project_id):
    return client.post(
        "/v1/accounts/pair/telegram", json={"project_id": project_id}, headers=headers
    ).json()["pair_code"]


def _message(chat_id, text, *, message_id=1, thread_id=None, name="Ada"):
    msg = {"chat": {"id": chat_id}, "from": {"first_name": name},
           "message_id": message_id, "text": text}
    if thread_id is not None:
        msg["message_thread_id"] = thread_id
    return {"update_id": message_id, "message": msg}


def _daemon_headers(client, acc, pid):
    pair = client.post("/v1/accounts/pair").json()
    client.post(
        f"/v1/accounts/pair/{pair['pair_code']}/approve",
        json={"project_id": pid},
        headers=acc,
    )
    token = client.get(
        f"/v1/accounts/pair/{pair['pair_code']}",
        params={"poll_secret": pair["poll_secret"]},
    ).json()["daemon_token"]
    return {"Authorization": f"Bearer {token}"}


def test_webhook_rejects_bad_secret(env):
    _, client, _ = env
    # No secret header.
    assert client.post("/v1/webhooks/telegram", json=_message(1, "hi")).status_code == 403
    # Wrong secret.
    assert client.post(
        "/v1/webhooks/telegram",
        json=_message(1, "hi"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "nope"},
    ).status_code == 403


def test_start_binds_chat_and_confirms(env):
    app, client, sends = env
    acc = _account(client)
    pid = _project(client, acc, name="myproj")
    code = _tg_pair_code(client, acc, pid)

    r = client.post(
        "/v1/webhooks/telegram", json=_message(555, f"/start {code}"), headers=_HDR
    )
    assert r.status_code == 200

    with app.state.SessionLocal() as db:
        binding = db.execute(
            select(ChatBinding).where(ChatBinding.chat_id == "555")
        ).scalar_one()
        assert binding.project_id == pid
    # The bot confirmed the pairing back to the chat.
    assert len(sends) == 1
    assert sends[0]["chat_id"] == "555"
    assert "myproj" in sends[0]["text"]


def test_invalid_start_code_is_reported(env):
    _, client, sends = env
    r = client.post(
        "/v1/webhooks/telegram", json=_message(7, "/start TG-NOPE"), headers=_HDR
    )
    assert r.status_code == 200
    assert sends and "Invalid" in sends[0]["text"]


def test_bound_chat_message_enqueues_with_reply_to(env):
    app, client, _ = env
    acc = _account(client)
    pid = _project(client, acc)
    code = _tg_pair_code(client, acc, pid)
    client.post("/v1/webhooks/telegram", json=_message(555, f"/start {code}"), headers=_HDR)

    r = client.post(
        "/v1/webhooks/telegram",
        json=_message(555, "do the thing", message_id=42, thread_id=9),
        headers=_HDR,
    )
    assert r.status_code == 200

    with app.state.SessionLocal() as db:
        event = db.execute(
            select(Event).where(Event.source == "telegram")
        ).scalar_one()
        assert event.project_id == pid
        assert event.body == "do the thing"

    # Drain it through the daemon to confirm the reply_to routes home.
    dmn = _daemon_headers(client, acc, pid)
    drained = client.get(
        "/v1/daemons/inbox", params={"since": 0, "wait": 0}, headers=dmn
    ).json()
    assert drained["events"][0]["reply_to"] == {
        "platform": "telegram",
        "chat_id": "555",
        "topic_id": 9,
        "message_id": 42,
    }


def test_unbound_chat_is_ignored(env):
    app, client, _ = env
    r = client.post(
        "/v1/webhooks/telegram", json=_message(404, "stranger danger"), headers=_HDR
    )
    assert r.status_code == 200
    with app.state.SessionLocal() as db:
        assert db.execute(select(Event)).scalars().all() == []


def test_split_message_prefers_newlines_and_loses_nothing():
    from brnrd.platforms import telegram as tg

    text = "\n".join("x" * 30 for _ in range(5))
    parts = tg.split_message(text, limit=35)
    assert all(len(p) <= 35 for p in parts)
    assert not any(p.startswith("\n") for p in parts)
    assert "".join(parts) == text.replace("\n", "")  # boundaries fall on newlines


def test_send_message_chunks_long_body(monkeypatch):
    from brnrd.platforms import telegram as tg

    posts: list[dict] = []

    class _Resp:
        def raise_for_status(self):
            return None

    monkeypatch.setattr(tg.httpx, "post", lambda url, json=None, timeout=None: (
        posts.append(json) or _Resp()
    ))

    body = "\n".join(f"line {i} " + "x" * 300 for i in range(80))  # well past 4096
    tg.send_message("bot:T", 555, body, reply_to_message_id=42)

    assert len(posts) >= 2                       # fanned out across messages
    assert all(len(p["text"]) <= 4096 for p in posts)
    assert posts[0]["reply_to_message_id"] == 42  # threading only on the first
    assert "reply_to_message_id" not in posts[1]


def test_response_is_forwarded_back_to_telegram(env):
    app, client, sends = env
    acc = _account(client)
    pid = _project(client, acc)
    code = _tg_pair_code(client, acc, pid)
    client.post("/v1/webhooks/telegram", json=_message(555, f"/start {code}"), headers=_HDR)
    sends.clear()  # drop the pairing confirmation

    client.post(
        "/v1/webhooks/telegram",
        json=_message(555, "task", message_id=77),
        headers=_HDR,
    )
    with app.state.SessionLocal() as db:
        event_id = db.execute(
            select(Event).where(Event.source == "telegram")
        ).scalar_one().event_id

    dmn = _daemon_headers(client, acc, pid)
    resp = client.post(
        "/v1/daemons/responses",
        json={"event_id": event_id, "body_markdown": "here is your answer",
              "status": "done"},
        headers=dmn,
    )
    assert resp.status_code == 200

    # The real forwarder posted the answer back to the originating chat,
    # threaded under the source message.
    assert len(sends) == 1
    assert sends[0]["chat_id"] == "555"
    assert sends[0]["text"] == "here is your answer"
    assert sends[0]["reply_to_message_id"] == 77


def test_telegram_pair_returns_deep_link_when_username_set():
    settings = Settings(
        database_url="sqlite:///:memory:",
        telegram_bot_token="bot:TOKEN",
        telegram_webhook_secret=_SECRET,
        telegram_bot_username="@brnrd_bot",  # leading @ tolerated
    )
    client = TestClient(create_app(settings))
    acc = _account(client)
    pid = _project(client, acc)
    body = client.post(
        "/v1/accounts/pair/telegram", json={"project_id": pid}, headers=acc
    ).json()
    code = body["pair_code"]
    assert code.startswith("TG-")
    # @-prefix stripped; the pair code rides as the tap-to-open start= param.
    assert body["deep_link"] == f"https://t.me/brnrd_bot?start={code}"
    assert body["deep_link"] in body["instructions"]


def test_telegram_pair_omits_deep_link_without_username(env):
    _, client, _ = env  # fixture Settings sets no telegram_bot_username
    acc = _account(client)
    pid = _project(client, acc)
    body = client.post(
        "/v1/accounts/pair/telegram", json={"project_id": pid}, headers=acc
    ).json()
    assert body["deep_link"] is None
    assert f"/start {body['pair_code']}" in body["instructions"]

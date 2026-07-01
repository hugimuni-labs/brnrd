"""Tests for the Telegram webhook ingress + response forwarding."""

from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from brnrd import create_app  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.models import ChannelRoute, Event  # noqa: E402
from _helpers import brnrd_account_headers  # noqa: E402

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
    return brnrd_account_headers(
        client.app, github_id="123", login="octocat", email="a@b.com"
    )


def _repo(client, headers, name="demo"):
    return client.post(
        "/v1/accounts/repos",
        json={"repo_full_name": f"Gurio/{name}"},
        headers=headers,
    ).json()["repo_id"]


def _tg_pair_code(client, headers, repo_id):
    return client.post(
        "/v1/accounts/pair/telegram", json={"repo_id": repo_id}, headers=headers
    ).json()["pair_code"]


def _message(
    chat_id,
    text,
    *,
    message_id=1,
    thread_id=None,
    date=None,
    name="Ada",
    user_id=42,
    username="ada_l",
):
    msg = {
        "chat": {"id": chat_id},
        "from": {"id": user_id, "first_name": name, "username": username},
        "message_id": message_id,
        "date": int(time.time()) if date is None else date,
        "text": text,
    }
    if thread_id is not None:
        msg["message_thread_id"] = thread_id
    return {"update_id": message_id, "message": msg}


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
    rid = _repo(client, acc, name="myrepo")
    code = _tg_pair_code(client, acc, rid)

    r = client.post(
        "/v1/webhooks/telegram", json=_message(555, f"/start {code}"), headers=_HDR
    )
    assert r.status_code == 200

    with app.state.SessionLocal() as db:
        binding = db.execute(
            select(ChannelRoute).where(ChannelRoute.channel_id == "555")
        ).scalar_one()
        assert binding.repo_id == rid
    # The bot confirmed the pairing back to the chat.
    assert len(sends) == 1
    assert sends[0]["chat_id"] == "555"
    assert "myrepo" in sends[0]["text"]


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
    rid = _repo(client, acc)
    code = _tg_pair_code(client, acc, rid)
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
        assert event.repo_id == rid
        assert event.body == "do the thing"

    # Drain it through the daemon to confirm the reply_to routes home.
    dmn = _daemon_headers(client, acc, rid)
    drained = client.get(
        "/v1/daemons/inbox", params={"since": 0, "wait": 0}, headers=dmn
    ).json()
    assert drained["events"][0]["reply_to"] == {
        "platform": "telegram",
        "chat_id": "555",
        "topic_id": 9,
        "message_id": 42,
        "user": "Ada",
        "user_id": 42,
        "username": "ada_l",
    }


def test_unbound_chat_gets_setup_error(env):
    app, client, sends = env
    r = client.post(
        "/v1/webhooks/telegram", json=_message(404, "stranger danger"), headers=_HDR
    )
    assert r.status_code == 200
    with app.state.SessionLocal() as db:
        assert db.execute(select(Event)).scalars().all() == []
    assert len(sends) == 1
    assert sends[0]["chat_id"] == "404"
    assert "not paired" in sends[0]["text"]


def test_pre_pair_backlog_is_ignored_after_chat_binds(env):
    app, client, sends = env
    acc = _account(client)
    rid = _repo(client, acc, name="alpha")
    code = _tg_pair_code(client, acc, rid)
    stale_date = int(time.time()) - 120
    client.post(
        "/v1/webhooks/telegram",
        json=_message(555, f"/start {code}", message_id=10),
        headers=_HDR,
    )
    sends.clear()

    r = client.post(
        "/v1/webhooks/telegram",
        json=_message(555, "this was sent before pairing", message_id=9, date=stale_date),
        headers=_HDR,
    )
    assert r.status_code == 200

    with app.state.SessionLocal() as db:
        assert db.execute(select(Event)).scalars().all() == []
    assert sends == []


def test_repo_command_switches_bound_chat(env):
    app, client, sends = env
    acc = _account(client)
    rid_a = _repo(client, acc, name="alpha")
    rid_b = _repo(client, acc, name="beta")
    code = _tg_pair_code(client, acc, rid_a)
    client.post(
        "/v1/webhooks/telegram",
        json=_message(555, f"/start {code}"),
        headers=_HDR,
    )
    sends.clear()

    r = client.post(
        "/v1/webhooks/telegram", json=_message(555, "/repo beta"), headers=_HDR
    )
    assert r.status_code == 200
    with app.state.SessionLocal() as db:
        binding = db.execute(
            select(ChannelRoute).where(ChannelRoute.channel_id == "555")
        ).scalar_one()
        assert binding.repo_id == rid_b
        assert db.execute(select(Event)).scalars().all() == []
    assert len(sends) == 1
    assert "Active repo set to 'Gurio/beta'" in sends[0]["text"]

    client.post(
        "/v1/webhooks/telegram", json=_message(555, "ship it"), headers=_HDR
    )
    with app.state.SessionLocal() as db:
        event = db.execute(select(Event).where(Event.source == "telegram")).scalar_one()
        assert event.repo_id == rid_b
        assert event.body == "ship it"


def test_status_command_reports_active_repo(env):
    app, client, sends = env
    acc = _account(client)
    rid = _repo(client, acc, name="alpha")
    code = _tg_pair_code(client, acc, rid)
    client.post(
        "/v1/webhooks/telegram",
        json=_message(555, f"/start {code}"),
        headers=_HDR,
    )
    sends.clear()

    r = client.post(
        "/v1/webhooks/telegram",
        json=_message(555, "/status"),
        headers=_HDR,
    )
    assert r.status_code == 200

    with app.state.SessionLocal() as db:
        binding = db.execute(
            select(ChannelRoute).where(ChannelRoute.channel_id == "555")
        ).scalar_one()
        assert binding.repo_id == rid
    assert len(sends) == 1
    assert "Active repo: Gurio/alpha" in sends[0]["text"]


def test_repo_command_unknown_repo_replies_without_enqueue(env):
    app, client, sends = env
    acc = _account(client)
    rid = _repo(client, acc, name="alpha")
    code = _tg_pair_code(client, acc, rid)
    client.post(
        "/v1/webhooks/telegram",
        json=_message(555, f"/start {code}"),
        headers=_HDR,
    )
    sends.clear()

    r = client.post(
        "/v1/webhooks/telegram",
        json=_message(555, "/repo missing"),
        headers=_HDR,
    )
    assert r.status_code == 200

    with app.state.SessionLocal() as db:
        assert db.execute(select(Event)).scalars().all() == []
    assert len(sends) == 1
    assert "was not found" in sends[0]["text"]


def test_repos_command_lists_current_repo(env):
    _, client, sends = env
    acc = _account(client)
    rid = _repo(client, acc, name="alpha")
    _repo(client, acc, name="beta")
    code = _tg_pair_code(client, acc, rid)
    client.post(
        "/v1/webhooks/telegram",
        json=_message(555, f"/start {code}"),
        headers=_HDR,
    )
    sends.clear()

    r = client.post(
        "/v1/webhooks/telegram", json=_message(555, "/repos"), headers=_HDR
    )
    assert r.status_code == 200

    assert len(sends) == 1
    assert "- Gurio/alpha (active)" in sends[0]["text"]
    assert "- Gurio/beta" in sends[0]["text"]


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
    rid = _repo(client, acc)
    code = _tg_pair_code(client, acc, rid)
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

    dmn = _daemon_headers(client, acc, rid)
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
    rid = _repo(client, acc)
    body = client.post(
        "/v1/accounts/pair/telegram", json={"repo_id": rid}, headers=acc
    ).json()
    code = body["pair_code"]
    assert code.startswith("TG-")
    # @-prefix stripped; the pair code rides as the tap-to-open start= param.
    assert body["deep_link"] == f"https://t.me/brnrd_bot?start={code}"
    assert body["deep_link"] in body["instructions"]


def test_telegram_pair_omits_deep_link_without_username(env):
    _, client, _ = env  # fixture Settings sets no telegram_bot_username
    acc = _account(client)
    rid = _repo(client, acc)
    body = client.post(
        "/v1/accounts/pair/telegram", json={"repo_id": rid}, headers=acc
    ).json()
    assert body["deep_link"] is None
    assert f"/start {body['pair_code']}" in body["instructions"]


def test_startup_registers_hosted_telegram_webhook(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(
        "brnrd.platforms.telegram.set_webhook",
        lambda token, url, *, secret_token, timeout=30.0: calls.append(
            {
                "token": token,
                "url": url,
                "secret_token": secret_token,
                "timeout": timeout,
            }
        ),
    )
    settings = Settings(
        database_url="sqlite:///:memory:",
        public_base_url="https://brnrd.dev/",
        telegram_bot_token="bot:TOKEN",
        telegram_webhook_secret=_SECRET,
    )

    with TestClient(create_app(settings)):
        pass

    assert calls == [
        {
            "token": "bot:TOKEN",
            "url": "https://brnrd.dev/v1/webhooks/telegram",
            "secret_token": _SECRET,
            "timeout": 10.0,
        }
    ]


def test_startup_skips_telegram_webhook_for_local_http(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(
        "brnrd.platforms.telegram.set_webhook",
        lambda *a, **k: calls.append({"args": a, "kwargs": k}),
    )
    settings = Settings(
        database_url="sqlite:///:memory:",
        public_base_url="http://localhost:8000",
        telegram_bot_token="bot:TOKEN",
        telegram_webhook_secret=_SECRET,
    )

    with TestClient(create_app(settings)):
        pass

    assert calls == []


def _bound_telegram_event(app, client, *, message_id=77):
    """Bind chat 555 and enqueue one task message; return its event_id."""
    acc = _account(client)
    rid = _repo(client, acc)
    code = _tg_pair_code(client, acc, rid)
    client.post("/v1/webhooks/telegram", json=_message(555, f"/start {code}"), headers=_HDR)
    client.post(
        "/v1/webhooks/telegram",
        json=_message(555, "task", message_id=message_id),
        headers=_HDR,
    )
    with app.state.SessionLocal() as db:
        event_id = db.execute(
            select(Event).where(Event.source == "telegram")
        ).scalar_one().event_id
    return acc, rid, event_id


def test_card_relay_sends_then_edits(env, monkeypatch):
    app, client, _ = env
    cards: list[dict] = []

    def fake_send_card(token, chat_id, text, *, topic_id=None,
                       reply_to_message_id=None, timeout=30.0):
        cards.append({"op": "send", "chat_id": chat_id, "text": text,
                      "reply_to": reply_to_message_id})
        return 4321

    def fake_edit_card(token, chat_id, message_id, text, *, timeout=30.0):
        cards.append({"op": "edit", "chat_id": chat_id,
                      "message_id": message_id, "text": text})

    monkeypatch.setattr("brnrd.platforms.telegram.send_card", fake_send_card)
    monkeypatch.setattr("brnrd.platforms.telegram.edit_card", fake_edit_card)

    acc, rid, event_id = _bound_telegram_event(app, client)
    dmn = _daemon_headers(client, acc, rid)

    # First card: no message_id → send, brnrd returns the platform id.
    r1 = client.post(
        "/v1/daemons/card",
        json={"event_id": event_id, "text": "<b>preparing</b>"},
        headers=dmn,
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["message_id"] == 4321

    # Replaying that id → edit in place, not a second send.
    r2 = client.post(
        "/v1/daemons/card",
        json={"event_id": event_id, "text": "<b>running</b>", "message_id": 4321},
        headers=dmn,
    )
    assert r2.status_code == 200
    assert r2.json()["message_id"] == 4321

    assert [c["op"] for c in cards] == ["send", "edit"]
    # Routed to the event's own bound chat + threaded under the source msg.
    assert cards[0]["chat_id"] == "555"
    assert cards[0]["reply_to"] == 77
    assert cards[1]["message_id"] == 4321
    assert cards[1]["text"] == "<b>running</b>"


def test_card_relay_is_noop_after_responded(env, monkeypatch):
    app, client, _ = env
    cards: list = []
    monkeypatch.setattr(
        "brnrd.platforms.telegram.send_card",
        lambda *a, **k: cards.append(a) or 1,
    )
    monkeypatch.setattr(
        "brnrd.platforms.telegram.edit_card", lambda *a, **k: cards.append(a)
    )

    acc, rid, event_id = _bound_telegram_event(app, client)
    dmn = _daemon_headers(client, acc, rid)

    # Deliver the final answer first; the card lifecycle is then over.
    client.post(
        "/v1/daemons/responses",
        json={"event_id": event_id, "body_markdown": "done", "status": "done"},
        headers=dmn,
    )
    r = client.post(
        "/v1/daemons/card", json={"event_id": event_id, "text": "late"}, headers=dmn
    )
    assert r.status_code == 200
    assert cards == []  # no platform call after the answer went out


def test_card_relay_unknown_event_is_404(env, monkeypatch):
    app, client, _ = env
    monkeypatch.setattr(
        "brnrd.platforms.telegram.send_card", lambda *a, **k: 1
    )
    acc = _account(client)
    rid = _repo(client, acc)
    dmn = _daemon_headers(client, acc, rid)
    r = client.post(
        "/v1/daemons/card", json={"event_id": "evt-nope", "text": "x"}, headers=dmn
    )
    assert r.status_code == 404

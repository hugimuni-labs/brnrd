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


def test_photo_caption_enqueues_with_attachment_pointer(env):
    """#525 — a captioned photo enqueues with an attachment *pointer*
    (largest PhotoSize; no bytes server-side) and no not-ingested note.
    Previously (#553) it enqueued annotated-only."""
    app, client, _ = env
    acc = _account(client)
    rid = _repo(client, acc)
    code = _tg_pair_code(client, acc, rid)
    client.post("/v1/webhooks/telegram", json=_message(555, f"/start {code}"), headers=_HDR)

    update = _message(555, "why are the rows grouped like this?", message_id=43)
    msg = update["message"]
    del msg["text"]
    msg["caption"] = "why are the rows grouped like this?"
    msg["photo"] = [
        {"file_id": "f1-small", "width": 90, "height": 60, "file_size": 1000},
        {"file_id": "f1-big", "width": 900, "height": 600, "file_size": 90000},
    ]
    r = client.post("/v1/webhooks/telegram", json=update, headers=_HDR)
    assert r.status_code == 200

    with app.state.SessionLocal() as db:
        event = db.execute(select(Event).where(Event.source == "telegram")).scalar_one()
        assert event.body == "why are the rows grouped like this?"
        from brnrd import inbox as inbox_service
        assert inbox_service.attachments_of(event) == [
            {"file_id": "f1-big", "filename": "photo.jpg", "kind": "photo", "file_size": 90000}
        ]


def test_captionless_photo_enqueues_pointer_with_empty_body(env, monkeypatch):
    """#525 — a captionless *image* is a valid message now (the image carries
    the content, matching the local gate); no more "can't see media" reply."""
    app, client, _ = env
    sent: list[str] = []
    monkeypatch.setattr(
        "brnrd.platforms.telegram.send_message",
        lambda token, chat_id, text, **kw: sent.append(text) or 1,
    )
    acc = _account(client)
    rid = _repo(client, acc)
    code = _tg_pair_code(client, acc, rid)
    client.post("/v1/webhooks/telegram", json=_message(555, f"/start {code}"), headers=_HDR)

    update = _message(555, "", message_id=44)
    msg = update["message"]
    del msg["text"]
    msg["photo"] = [{"file_id": "f2", "width": 90, "height": 60}]
    r = client.post("/v1/webhooks/telegram", json=update, headers=_HDR)
    assert r.status_code == 200

    with app.state.SessionLocal() as db:
        from brnrd import inbox as inbox_service
        event = db.execute(select(Event).where(Event.source == "telegram")).scalar_one()
        assert event.body == ""
        assert inbox_service.attachments_of(event) == [
            {"file_id": "f2", "filename": "photo.jpg", "kind": "photo"}
        ]
    assert not any("can't see attached media" in t for t in sent)


def test_image_document_pointer_keeps_filename(env):
    """#525 — a drag-and-drop image document keeps its own filename; the
    filename is sanitized to a bare basename."""
    app, client, _ = env
    acc = _account(client)
    rid = _repo(client, acc)
    code = _tg_pair_code(client, acc, rid)
    client.post("/v1/webhooks/telegram", json=_message(555, f"/start {code}"), headers=_HDR)

    update = _message(555, "see attached", message_id=45)
    update["message"]["document"] = {
        "file_id": "d1", "file_name": "../evil/shot.png", "mime_type": "image/png",
    }
    client.post("/v1/webhooks/telegram", json=update, headers=_HDR)

    with app.state.SessionLocal() as db:
        from brnrd import inbox as inbox_service
        event = db.execute(select(Event).where(Event.source == "telegram")).scalar_one()
        assert inbox_service.attachments_of(event) == [
            {"file_id": "d1", "filename": "shot.png", "kind": "document"}
        ]


def test_non_image_media_keeps_annotation_and_captionless_still_replies(env, monkeypatch):
    """Non-image media stays annotated-not-fetched (#553 behavior)."""
    app, client, _ = env
    sent: list[str] = []
    monkeypatch.setattr(
        "brnrd.platforms.telegram.send_message",
        lambda token, chat_id, text, **kw: sent.append(text) or 1,
    )
    acc = _account(client)
    rid = _repo(client, acc)
    code = _tg_pair_code(client, acc, rid)
    client.post("/v1/webhooks/telegram", json=_message(555, f"/start {code}"), headers=_HDR)

    # Voice note with a caption → enqueue annotated, no pointer.
    update = _message(555, "listen to this", message_id=46)
    msg = update["message"]
    del msg["text"]
    msg["caption"] = "listen to this"
    msg["voice"] = {"file_id": "v1", "duration": 3}
    client.post("/v1/webhooks/telegram", json=update, headers=_HDR)
    with app.state.SessionLocal() as db:
        from brnrd import inbox as inbox_service
        event = db.execute(select(Event).where(Event.source == "telegram")).scalar_one()
        assert "[attached media not ingested" in (event.body or "")
        assert inbox_service.attachments_of(event) == []

    # Captionless video → still the honest "can't see media" reply.
    update2 = _message(555, "", message_id=47)
    msg2 = update2["message"]
    del msg2["text"]
    msg2["video"] = {"file_id": "vid1"}
    client.post("/v1/webhooks/telegram", json=update2, headers=_HDR)
    assert any("can't see attached media" in t for t in sent)


# ── #409 default-closed authorization gate ───────────────────────────


def test_non_principal_group_member_is_not_enqueued(env):
    # Ada (user_id=42, the default sender) pairs the chat by running
    # /start; a different member of the same group chat is neither the
    # paired principal nor allowlisted, so their message must not enqueue.
    app, client, sends = env
    acc = _account(client)
    rid = _repo(client, acc)
    code = _tg_pair_code(client, acc, rid)
    client.post("/v1/webhooks/telegram", json=_message(555, f"/start {code}"), headers=_HDR)
    sends.clear()

    r = client.post(
        "/v1/webhooks/telegram",
        json=_message(555, "do the thing", user_id=999, username="mallory", name="Mallory"),
        headers=_HDR,
    )
    assert r.status_code == 200

    with app.state.SessionLocal() as db:
        assert db.execute(select(Event)).scalars().all() == []
    # No reply either — the audit trail for a denied sender is
    # server-side only, so an unauthorized prober learns nothing.
    assert sends == []


def test_paired_principal_is_enqueued(env):
    # The sender who consumed the pair code is the route's principal —
    # their own later messages must still enqueue (test_bound_chat_message
    # _enqueues_with_reply_to already covers this end-to-end; this pins
    # the authorization predicate itself in isolation).
    app, client, _ = env
    acc = _account(client)
    rid = _repo(client, acc)
    code = _tg_pair_code(client, acc, rid)
    client.post("/v1/webhooks/telegram", json=_message(555, f"/start {code}"), headers=_HDR)

    r = client.post(
        "/v1/webhooks/telegram",
        json=_message(555, "do the thing", message_id=88),
        headers=_HDR,
    )
    assert r.status_code == 200

    with app.state.SessionLocal() as db:
        event = db.execute(select(Event).where(Event.source == "telegram")).scalar_one()
        assert event.repo_id == rid
        assert event.body == "do the thing"


def test_allowlisted_sender_is_enqueued(monkeypatch):
    monkeypatch.setattr("brnrd.platforms.telegram.send_message", lambda *a, **k: None)
    settings = Settings(
        database_url="sqlite:///:memory:",
        telegram_bot_token="bot:TOKEN",
        telegram_webhook_secret=_SECRET,
        telegram_authz_allowlist=(777,),
    )
    app = create_app(settings)
    client = TestClient(app)
    acc = _account(client)
    rid = _repo(client, acc)
    code = _tg_pair_code(client, acc, rid)
    client.post("/v1/webhooks/telegram", json=_message(555, f"/start {code}"), headers=_HDR)

    r = client.post(
        "/v1/webhooks/telegram",
        json=_message(555, "do the thing", user_id=777, username="carol", name="Carol"),
        headers=_HDR,
    )
    assert r.status_code == 200

    with app.state.SessionLocal() as db:
        event = db.execute(select(Event).where(Event.source == "telegram")).scalar_one()
        assert event.repo_id == rid
        assert event.body == "do the thing"


def test_edited_message_does_not_enqueue(env):
    app, client, sends = env
    acc = _account(client)
    rid = _repo(client, acc)
    code = _tg_pair_code(client, acc, rid)
    client.post("/v1/webhooks/telegram", json=_message(555, f"/start {code}"), headers=_HDR)
    sends.clear()

    original = _message(555, "do the thing", message_id=88)
    edited_payload = {
        "update_id": original["update_id"],
        "edited_message": original["message"],
    }
    r = client.post("/v1/webhooks/telegram", json=edited_payload, headers=_HDR)
    assert r.status_code == 200

    with app.state.SessionLocal() as db:
        assert db.execute(select(Event)).scalars().all() == []
    assert sends == []


def test_migration_updates_route_chat_id_without_enqueue(env):
    app, client, sends = env
    acc = _account(client)
    rid = _repo(client, acc)
    code = _tg_pair_code(client, acc, rid)
    client.post("/v1/webhooks/telegram", json=_message(555, f"/start {code}"), headers=_HDR)
    sends.clear()

    migrate_payload = {
        "update_id": 909,
        "message": {
            "message_id": 900,
            "chat": {"id": 555},
            "date": int(time.time()),
            "migrate_to_chat_id": -100555,
        },
    }
    r = client.post("/v1/webhooks/telegram", json=migrate_payload, headers=_HDR)
    assert r.status_code == 200

    with app.state.SessionLocal() as db:
        route = db.execute(
            select(ChannelRoute).where(ChannelRoute.channel_id == "-100555")
        ).scalar_one()
        assert route.repo_id == rid
        assert db.execute(select(Event)).scalars().all() == []
    assert sends == []  # never a trigger, no reply either


def test_forwarded_message_keys_on_forwarder_not_origin(env):
    # A forward carries the forwarder's own `from.id` (42, the paired
    # principal) plus `forward_origin` describing who originally sent it
    # (999, a stranger). Authorization must key on the forwarder.
    app, client, _ = env
    acc = _account(client)
    rid = _repo(client, acc)
    code = _tg_pair_code(client, acc, rid)
    client.post("/v1/webhooks/telegram", json=_message(555, f"/start {code}"), headers=_HDR)

    payload = _message(555, "forwarded task", message_id=88)
    payload["message"]["forward_origin"] = {
        "type": "user",
        "sender_user": {"id": 999, "first_name": "Not Ada"},
    }
    r = client.post("/v1/webhooks/telegram", json=payload, headers=_HDR)
    assert r.status_code == 200

    with app.state.SessionLocal() as db:
        event = db.execute(select(Event).where(Event.source == "telegram")).scalar_one()
        assert event.repo_id == rid
        assert event.body == "forwarded task"


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


def test_card_relay_continues_after_responded(env, monkeypatch):
    """A responded event still relays cards: a respawn continuation run
    rides its parent's event, and the parent's terminal close must not
    mute the child's live card (2026-07-21 — the mega run whose status
    card never appeared)."""
    app, client, _ = env
    cards: list = []
    monkeypatch.setattr(
        "brnrd.platforms.telegram.send_card",
        lambda *a, **k: cards.append(("send", a)) or 1,
    )
    monkeypatch.setattr(
        "brnrd.platforms.telegram.edit_card",
        lambda *a, **k: cards.append(("edit", a)),
    )

    acc, rid, event_id = _bound_telegram_event(app, client)
    dmn = _daemon_headers(client, acc, rid)

    # Parent run delivers its final answer; the event closes.
    client.post(
        "/v1/daemons/responses",
        json={"event_id": event_id, "body_markdown": "done", "status": "done"},
        headers=dmn,
    )
    # A continuation run's card still reaches the platform.
    r = client.post(
        "/v1/daemons/card", json={"event_id": event_id, "text": "child card"}, headers=dmn
    )
    assert r.status_code == 200
    assert r.json()["message_id"] == 1
    assert [op for op, _ in cards] == ["send"]


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


# ── #525 attachment read-through proxy ───────────────────────────────


def _bound_photo_event(app, client, *, message_id=88, caption="see this"):
    """Pair a chat, deliver a photo webhook, return (acc, rid, event dict
    as the daemon inbox pull sees it)."""
    acc = _account(client)
    rid = _repo(client, acc)
    code = _tg_pair_code(client, acc, rid)
    client.post("/v1/webhooks/telegram", json=_message(555, f"/start {code}"), headers=_HDR)
    update = _message(555, "", message_id=message_id)
    msg = update["message"]
    del msg["text"]
    if caption:
        msg["caption"] = caption
    msg["photo"] = [{"file_id": "photo-big", "width": 900, "height": 600}]
    client.post("/v1/webhooks/telegram", json=update, headers=_HDR)
    dmn = _daemon_headers(client, acc, rid)
    drained = client.get(
        "/v1/daemons/inbox", params={"since": 0, "wait": 0}, headers=dmn
    ).json()
    return acc, rid, dmn, drained["events"][0]


def test_inbox_pull_carries_attachment_pointers(env):
    app, client, _ = env
    _, _, _, event = _bound_photo_event(app, client)
    assert event["attachments"] == [
        {"file_id": "photo-big", "filename": "photo.jpg", "kind": "photo"}
    ]


def test_attachment_proxy_streams_bytes_fresh_per_request(env, monkeypatch):
    app, client, _ = env
    resolved: list[str] = []

    def fake_resolve(token, file_id, **kw):
        resolved.append(file_id)
        return {"file_path": "photos/x.jpg", "file_size": 5}

    monkeypatch.setattr("brnrd.platforms.telegram.resolve_file", fake_resolve)
    monkeypatch.setattr(
        "brnrd.platforms.telegram.fetch_file_bytes",
        lambda token, file_path, *, max_bytes, timeout=60.0: b"JPEG!",
    )
    _, _, dmn, event = _bound_photo_event(app, client)
    r = client.get(f"/v1/daemons/events/{event['event_id']}/attachments/0", headers=dmn)
    assert r.status_code == 200
    assert r.content == b"JPEG!"
    assert r.headers["content-type"].startswith("image/jpeg")
    # getFile resolved fresh on each request — never cached server-side.
    client.get(f"/v1/daemons/events/{event['event_id']}/attachments/0", headers=dmn)
    assert resolved == ["photo-big", "photo-big"]


def test_attachment_proxy_requires_daemon_credential(env, monkeypatch):
    app, client, _ = env
    monkeypatch.setattr(
        "brnrd.platforms.telegram.resolve_file",
        lambda *a, **k: {"file_path": "p", "file_size": 1},
    )
    monkeypatch.setattr(
        "brnrd.platforms.telegram.fetch_file_bytes", lambda *a, **k: b"x"
    )
    acc, rid, dmn, event = _bound_photo_event(app, client)
    url = f"/v1/daemons/events/{event['event_id']}/attachments/0"
    assert client.get(url).status_code == 401
    assert client.get(url, headers=acc).status_code == 403
    # Daemon credentials are account-scoped: a token paired through another
    # repo in the same account can fetch the account event.
    other_rid = _repo(client, acc, name="other")
    other_dmn = _daemon_headers(client, acc, other_rid)
    assert client.get(url, headers=other_dmn).status_code == 200


def test_attachment_proxy_expired_file_is_502(env, monkeypatch):
    app, client, _ = env

    def gone(token, file_id, **kw):
        raise RuntimeError("telegram getFile failed: file is too old")

    monkeypatch.setattr("brnrd.platforms.telegram.resolve_file", gone)
    _, _, dmn, event = _bound_photo_event(app, client)
    r = client.get(f"/v1/daemons/events/{event['event_id']}/attachments/0", headers=dmn)
    assert r.status_code == 502
    assert "telegram file unavailable" in r.json()["detail"]


def test_attachment_proxy_over_cap_is_413(env, monkeypatch):
    app, client, _ = env
    fetched: list[str] = []
    monkeypatch.setattr(
        "brnrd.platforms.telegram.resolve_file",
        lambda token, file_id, **kw: {"file_path": "p", "file_size": 11 * 1024 * 1024},
    )
    monkeypatch.setattr(
        "brnrd.platforms.telegram.fetch_file_bytes",
        lambda *a, **k: fetched.append("x") or b"x",
    )
    _, _, dmn, event = _bound_photo_event(app, client)
    r = client.get(f"/v1/daemons/events/{event['event_id']}/attachments/0", headers=dmn)
    assert r.status_code == 413
    assert fetched == []  # declared size rejected before any bytes moved


def test_attachment_proxy_unknown_index_is_404(env):
    app, client, _ = env
    _, _, dmn, event = _bound_photo_event(app, client)
    r = client.get(f"/v1/daemons/events/{event['event_id']}/attachments/5", headers=dmn)
    assert r.status_code == 404


def test_responded_event_clears_pointers_and_proxy_404s(env):
    app, client, _ = env
    _, _, dmn, event = _bound_photo_event(app, client)
    client.post(
        "/v1/daemons/responses",
        json={"event_id": event["event_id"], "body_markdown": "done", "status": "done"},
        headers=dmn,
    )
    with app.state.SessionLocal() as db:
        from brnrd import inbox as inbox_service
        row = db.execute(
            select(Event).where(Event.event_id == event["event_id"])
        ).scalar_one()
        assert inbox_service.attachments_of(row) == []
    r = client.get(f"/v1/daemons/events/{event['event_id']}/attachments/0", headers=dmn)
    assert r.status_code == 404

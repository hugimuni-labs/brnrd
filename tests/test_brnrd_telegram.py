"""Tests for the Telegram webhook ingress + response forwarding."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from brnrd import create_app, ids  # noqa: E402
from brnrd.app import _maybe_derive_telegram_bot_username  # noqa: E402
from brnrd.config import Settings, _derived_telegram_usernames  # noqa: E402
from brnrd.models import ChannelRoute, Event, Repo, TgPairCode  # noqa: E402
from _helpers import brnrd_account_headers  # noqa: E402

_SECRET = "webhook-secret"
_HDR = {"X-Telegram-Bot-Api-Secret-Token": _SECRET}


@pytest.fixture(autouse=True)
def _isolate_telegram_derived_username():
    """Clear config.py's getMe-derived-username cache around each test.

    It's a module-level dict keyed by token (#1463) so it can outlive one
    request — exactly the state that must not leak between tests that
    happen to share a literal token string (most of this file uses
    ``"bot:TOKEN"``).
    """
    _derived_telegram_usernames.clear()
    yield
    _derived_telegram_usernames.clear()


def _make_client(monkeypatch, **overrides):
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
        **overrides,
    )
    app = create_app(settings)
    return app, TestClient(app), sends


@pytest.fixture()
def env(monkeypatch):
    return _make_client(monkeypatch)


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
    chat_type=None,
):
    chat = {"id": chat_id}
    if chat_type is not None:
        chat["type"] = chat_type
    msg = {
        "chat": chat,
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
        json={"repo_id": repo_id, "approve_secret": pair["approve_secret"]},
        headers=acc,
    )
    token = client.get(
        f"/v1/accounts/pair/{pair['pair_code']}",
        params={"poll_secret": pair["poll_secret"]},
    ).json()["daemon_token"]
    return {"Authorization": f"Bearer {token}"}


def _register_daemon(client, dmn_headers, name="local"):
    r = client.post("/v1/daemons/register", json={"daemon_name": name}, headers=dmn_headers)
    assert r.status_code == 200, r.text


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


def test_webhook_fails_closed_on_a_non_ascii_secret_header(env):
    """H-4: `hmac.compare_digest` raises `TypeError` (rather than returning
    `False`) when a `str` argument carries non-ASCII code points, and this
    header is attacker-controlled — before the fix this turned a should-be
    403 into an unhandled 500. Raw (bytes, bytes) headers because httpx's
    str header path is ASCII-only; the wire is not."""
    import json as _json

    _, client, _ = env
    body = _json.dumps(_message(1, "hi")).encode("utf-8")
    r = client.post(
        "/v1/webhooks/telegram",
        content=body,
        headers=[
            (b"Content-Type", b"application/json"),
            (b"X-Telegram-Bot-Api-Secret-Token", b"\xff\xfe not-the-secret"),
        ],
    )
    assert r.status_code == 403, r.text


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


def test_legacy_tg_prefixed_code_still_pairs_via_start(env):
    """#1237 migration window: `ids.tg_pair_code` no longer mints the `TG-`
    shape (moved to `PK-`), but a code minted before the flip must still
    pair via `/start <code>` until it naturally expires. `/start` never
    consults `_WA_PAIR_CODE_RE` — this is real coverage of the `/start`
    invariant, not a guard on the regex itself (see the bare-code sibling
    test below for that)."""
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

    r = client.post(
        "/v1/webhooks/telegram", json=_message(555, f"/start {code}"), headers=_HDR
    )
    assert r.status_code == 200

    with app.state.SessionLocal() as db:
        binding = db.execute(
            select(ChannelRoute).where(ChannelRoute.channel_id == "555")
        ).scalar_one()
        assert binding.repo_id == rid
    assert len(sends) == 1
    assert "myrepo" in sends[0]["text"]


def test_invalid_start_code_is_reported(env):
    _, client, sends = env
    r = client.post(
        "/v1/webhooks/telegram", json=_message(7, "/start TG-NOPE"), headers=_HDR
    )
    assert r.status_code == 200
    assert sends and "Invalid" in sends[0]["text"]


def test_expired_start_code_names_the_retry_command(env):
    """#1282 — a code that genuinely existed and expired (600s TTL) gets a
    specific, actionable message instead of folding into the generic
    "Invalid or expired pair code." text an unknown/consumed code gets."""
    app, client, sends = env
    acc = _account(client)
    rid = _repo(client, acc, name="myrepo")
    code = _tg_pair_code(client, acc, rid)
    with app.state.SessionLocal() as db:
        pc = db.execute(select(TgPairCode).where(TgPairCode.code == code)).scalar_one()
        pc.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()

    r = client.post(
        "/v1/webhooks/telegram", json=_message(7, f"/start {code}"), headers=_HDR
    )
    assert r.status_code == 200
    assert sends
    assert "expired" in sends[0]["text"]
    assert "account connect" in sends[0]["text"]
    assert sends[0]["text"] != "Invalid or expired pair code."


# ── #1242: the rescue loop, bare-code parity, username shape ──────────


def test_unpaired_repos_reply_names_the_rescue_command(env):
    """The old text pointed an unpaired chat at /repos, whose own unpaired
    reply is this same message — a closed loop. The reply must instead name
    /start, the command that actually works."""
    _, client, sends = env
    r = client.post(
        "/v1/webhooks/telegram", json=_message(7, "/repos"), headers=_HDR
    )
    assert r.status_code == 200
    assert sends and "/start" in sends[0]["text"]


def test_unpaired_plain_message_reply_names_the_rescue_command(env):
    _, client, sends = env
    r = client.post(
        "/v1/webhooks/telegram", json=_message(7, "hello"), headers=_HDR
    )
    assert r.status_code == 200
    assert sends and "/start" in sends[0]["text"]


def test_bare_pair_code_binds_chat_like_start(env):
    """Telegram accepts a bare PK-XXXX exactly like WhatsApp does — a user
    who pastes just the code, no /start prefix, still pairs."""
    app, client, sends = env
    acc = _account(client)
    rid = _repo(client, acc, name="myrepo")
    code = _tg_pair_code(client, acc, rid)

    r = client.post("/v1/webhooks/telegram", json=_message(555, code), headers=_HDR)
    assert r.status_code == 200

    with app.state.SessionLocal() as db:
        binding = db.execute(
            select(ChannelRoute).where(ChannelRoute.channel_id == "555")
        ).scalar_one()
        assert binding.repo_id == rid
    assert len(sends) == 1
    assert "myrepo" in sends[0]["text"]


def test_legacy_tg_prefixed_code_still_pairs_bare(env):
    """#1237 migration window, bare-code lane: unlike `/start`, the bare
    lane consults `_WA_PAIR_CODE_RE` directly, so this is real coverage of
    the regex still accepting the legacy `TG-` shape."""
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

    r = client.post("/v1/webhooks/telegram", json=_message(555, code), headers=_HDR)
    assert r.status_code == 200

    with app.state.SessionLocal() as db:
        binding = db.execute(
            select(ChannelRoute).where(ChannelRoute.channel_id == "555")
        ).scalar_one()
        assert binding.repo_id == rid
    assert len(sends) == 1
    assert "myrepo" in sends[0]["text"]


def test_bare_pair_code_is_case_insensitive_and_whitespace_tolerant(env):
    app, client, sends = env
    acc = _account(client)
    rid = _repo(client, acc)
    code = _tg_pair_code(client, acc, rid)

    r = client.post(
        "/v1/webhooks/telegram", json=_message(555, f"  {code.lower()}  "), headers=_HDR
    )
    assert r.status_code == 200
    with app.state.SessionLocal() as db:
        assert db.execute(select(ChannelRoute)).scalars().all()[0].repo_id == rid


def test_bare_shaped_but_unknown_code_reports_invalid(env):
    _, client, sends = env
    r = client.post(
        "/v1/webhooks/telegram", json=_message(7, "TG-ZZZZ"), headers=_HDR
    )
    assert r.status_code == 200
    assert sends and "Invalid" in sends[0]["text"]


def test_invalid_bot_username_mints_no_deep_link(monkeypatch):
    """config.py:78 / routers/pairing.py's `_telegram_pair_response`: an
    invalid-shape username (the hyphenated GitHub-login spelling, e.g.)
    must never reach a deep link — no entity resolves at t.me/<name> with a
    hyphen in it, so a link built on it is a link to nowhere."""
    app, client, _ = _make_client(monkeypatch, telegram_bot_username="brnrd-bot")
    acc = _account(client)
    rid = _repo(client, acc, name="myrepo")

    r = client.post(
        "/v1/accounts/pair/telegram", json={"repo_id": rid}, headers=acc
    )
    assert r.status_code == 200
    body = r.json()
    assert body["deep_link"] is None
    # The manual path leads the instructions instead of a dead t.me link.
    assert body["instructions"].startswith("Send `/start")
    assert "t.me" not in body["instructions"]


def test_valid_bot_username_still_mints_deep_link(monkeypatch):
    app, client, _ = _make_client(monkeypatch, telegram_bot_username="brnrd_bot")
    acc = _account(client)
    rid = _repo(client, acc, name="myrepo")

    r = client.post(
        "/v1/accounts/pair/telegram", json={"repo_id": rid}, headers=acc
    )
    assert r.status_code == 200
    body = r.json()
    assert body["deep_link"] == f"https://t.me/brnrd_bot?start={body['pair_code']}"


def test_invalid_bot_username_warns_loudly_at_settings_construction(caplog):
    caplog.set_level(logging.WARNING, logger="brnrd.config")
    Settings(database_url="sqlite:///:memory:", telegram_bot_username="brnrd-bot")
    assert any(
        "not a valid Telegram username" in r.getMessage() for r in caplog.records
    )


def test_valid_bot_username_construction_is_quiet(caplog):
    caplog.set_level(logging.WARNING, logger="brnrd.config")
    Settings(database_url="sqlite:///:memory:", telegram_bot_username="brnrd_bot")
    assert caplog.records == []


# ── #1463: getMe-derived username, through the callers ───────────────


def test_derived_username_wins_over_invalid_env_and_mints_deep_link(monkeypatch):
    """The prod-shaped case (#1242): the env var carries the hyphenated
    GitHub-login spelling, which never resolves to a Telegram entity — but
    the token's own `getMe` does, so the derived value must be what
    actually reaches the deep link, not the env fallback's ``""``."""
    monkeypatch.setattr(
        "brnrd.platforms.telegram.get_me",
        lambda token, *, timeout=10.0: {"id": 1, "username": "brnrd_bot", "is_bot": True},
    )
    app, client, _ = _make_client(monkeypatch, telegram_bot_username="brnrd-bot")
    _maybe_derive_telegram_bot_username(app.state.settings)

    acc = _account(client)
    rid = _repo(client, acc, name="myrepo")
    r = client.post("/v1/accounts/pair/telegram", json={"repo_id": rid}, headers=acc)
    assert r.status_code == 200
    body = r.json()
    assert body["deep_link"] == f"https://t.me/brnrd_bot?start={body['pair_code']}"


def test_getme_unreachable_falls_back_to_valid_env(monkeypatch):
    def _boom(token, *, timeout=10.0):
        raise TimeoutError("getMe timed out")

    monkeypatch.setattr("brnrd.platforms.telegram.get_me", _boom)
    app, client, _ = _make_client(monkeypatch, telegram_bot_username="brnrd_bot")
    _maybe_derive_telegram_bot_username(app.state.settings)  # never blocks, never raises

    acc = _account(client)
    rid = _repo(client, acc, name="myrepo")
    r = client.post("/v1/accounts/pair/telegram", json={"repo_id": rid}, headers=acc)
    assert r.status_code == 200
    body = r.json()
    assert body["deep_link"] == f"https://t.me/brnrd_bot?start={body['pair_code']}"


def test_getme_and_env_both_invalid_yields_empty(monkeypatch):
    monkeypatch.setattr(
        "brnrd.platforms.telegram.get_me",
        lambda token, *, timeout=10.0: {"id": 1, "username": "no", "is_bot": True},  # too short
    )
    app, client, _ = _make_client(monkeypatch, telegram_bot_username="brnrd-bot")
    _maybe_derive_telegram_bot_username(app.state.settings)

    acc = _account(client)
    rid = _repo(client, acc, name="myrepo")
    r = client.post("/v1/accounts/pair/telegram", json={"repo_id": rid}, headers=acc)
    assert r.status_code == 200
    body = r.json()
    assert body["deep_link"] is None
    assert "t.me" not in body["instructions"]


def test_getme_disagreement_with_env_warns_once(monkeypatch, capsys):
    monkeypatch.setattr(
        "brnrd.platforms.telegram.get_me",
        lambda token, *, timeout=10.0: {"id": 1, "username": "real_bot", "is_bot": True},
    )
    app, client, _ = _make_client(monkeypatch, telegram_bot_username="stale_bot")
    _maybe_derive_telegram_bot_username(app.state.settings)
    out = capsys.readouterr().out
    assert "disagrees" in out
    assert "real_bot" in out


def test_getme_never_blocks_startup_on_missing_token(monkeypatch):
    """No token ⇒ nothing to derive from; the call is a same-turn no-op,
    never a network attempt."""
    called = []
    monkeypatch.setattr(
        "brnrd.platforms.telegram.get_me",
        lambda token, *, timeout=10.0: called.append(token) or {"username": "x"},
    )
    settings = Settings(database_url="sqlite:///:memory:", telegram_bot_token="")
    _maybe_derive_telegram_bot_username(settings)
    assert called == []


def test_unset_bot_username_construction_is_quiet(caplog):
    caplog.set_level(logging.WARNING, logger="brnrd.config")
    Settings(database_url="sqlite:///:memory:")
    assert caplog.records == []


def test_no_daemon_online_gets_a_nudge_and_still_enqueues(env):
    """#1282 — a bound chat whose account has never had a daemon check in
    must not go silent: it still gets a reply, and the message still
    enqueues (a daemon that shows up later drains it normally)."""
    app, client, sends = env
    acc = _account(client)
    rid = _repo(client, acc)
    code = _tg_pair_code(client, acc, rid)
    client.post("/v1/webhooks/telegram", json=_message(555, f"/start {code}"), headers=_HDR)
    sends.clear()  # drop the pairing confirmation

    r = client.post(
        "/v1/webhooks/telegram",
        json=_message(555, "do the thing", message_id=43),
        headers=_HDR,
    )
    assert r.status_code == 200
    assert sends
    assert "no daemon" in sends[0]["text"].lower()
    assert "account connect" in sends[0]["text"]

    with app.state.SessionLocal() as db:
        event = db.execute(select(Event).where(Event.source == "telegram")).scalar_one()
        assert event.body == "do the thing"


def test_online_daemon_suppresses_the_no_daemon_nudge(env):
    """#1282 — once a daemon has registered and is heartbeat-fresh, the
    nudge stops firing (the account has somewhere for the message to go)."""
    app, client, sends = env
    acc = _account(client)
    rid = _repo(client, acc)
    code = _tg_pair_code(client, acc, rid)
    client.post("/v1/webhooks/telegram", json=_message(555, f"/start {code}"), headers=_HDR)
    sends.clear()  # drop the pairing confirmation

    dmn = _daemon_headers(client, acc, rid)
    _register_daemon(client, dmn)

    r = client.post(
        "/v1/webhooks/telegram",
        json=_message(555, "do the thing", message_id=44),
        headers=_HDR,
    )
    assert r.status_code == 200
    assert sends == []


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


def test_media_group_album_coalesces_into_one_event(env):
    """#1389 — a Telegram album (one text/caption plus N photos) arrives as
    N separate webhook calls sharing ``media_group_id``; previously each
    became its own event — a five-photo send was five events, five future
    runs. They now fold into one event carrying every attachment pointer,
    with whichever item carried the caption supplying the body."""
    app, client, _ = env
    acc = _account(client)
    rid = _repo(client, acc)
    code = _tg_pair_code(client, acc, rid)
    client.post("/v1/webhooks/telegram", json=_message(555, f"/start {code}"), headers=_HDR)

    def _photo_update(message_id, file_id, *, caption=None):
        update = _message(555, "", message_id=message_id)
        msg = update["message"]
        del msg["text"]
        if caption is not None:
            msg["caption"] = caption
        msg["photo"] = [{"file_id": file_id, "width": 900, "height": 600, "file_size": 1000}]
        msg["media_group_id"] = "album-42"
        return update

    r1 = client.post(
        "/v1/webhooks/telegram", json=_photo_update(50, "f1"), headers=_HDR
    )
    r2 = client.post(
        "/v1/webhooks/telegram",
        json=_photo_update(51, "f2", caption="look at these"),
        headers=_HDR,
    )
    r3 = client.post(
        "/v1/webhooks/telegram", json=_photo_update(52, "f3"), headers=_HDR
    )
    assert r1.status_code == r2.status_code == r3.status_code == 200

    with app.state.SessionLocal() as db:
        events = list(
            db.execute(select(Event).where(Event.source == "telegram")).scalars()
        )
        assert len(events) == 1
        event = events[0]
        assert event.body == "look at these"
        from brnrd import inbox as inbox_service
        assert [p["file_id"] for p in inbox_service.attachments_of(event)] == [
            "f1", "f2", "f3",
        ]

    # Drained to the daemon exactly once — the merge happened before the
    # event ever reached the queue, not as a daemon-side dedup.
    dmn = _daemon_headers(client, acc, rid)
    drained = client.get(
        "/v1/daemons/inbox", params={"since": 0, "wait": 0}, headers=dmn
    ).json()
    telegram_events = [e for e in drained["events"] if e.get("body") == "look at these"]
    assert len(telegram_events) == 1
    assert len(telegram_events[0]["attachments"]) == 3


def test_merged_album_item_stays_deliverable_after_an_interleaved_drain(env):
    """#1396 finding 1 — a merge that mutates the row *in place* never bumps
    ``Event.seq``, and delivery is ``Event.seq > since``. A daemon that is
    already long-polling can drain item 1 (advancing its cursor past that
    row's seq) *before* item 2 merges into it — the album isn't fully
    arrived yet, so this is the common case, not an edge case. Interleaves a
    drain between the first and second item of
    ``test_media_group_album_coalesces_into_one_event``'s own album to prove
    the second photo is still reachable afterwards.

    Pre-fix (an in-place ``UPDATE`` that only ever touches ``body`` /
    ``attachments_json``), this went red exactly as the review predicted:
    the second drain came back with **zero** events, because the merged
    row's ``seq`` never advanced past the cursor the first drain had already
    consumed — the second photo was in the database and permanently
    unreachable over this API. Verified live on this branch's pre-fix tip
    (commit `9e833cda`, the last commit before the #1396 fix): swapping
    `_merge_into_open_media_group`'s delete+reinsert back for the original
    in-place `UPDATE` reproduces that exact failure —
    ``assert len(second_drain["events"]) == 1`` raised
    ``AssertionError: assert 0 == 1``.
    """
    app, client, _ = env
    acc = _account(client)
    rid = _repo(client, acc)
    code = _tg_pair_code(client, acc, rid)
    client.post("/v1/webhooks/telegram", json=_message(555, f"/start {code}"), headers=_HDR)
    dmn = _daemon_headers(client, acc, rid)

    def _photo_update(message_id, file_id, *, caption=None):
        update = _message(555, "", message_id=message_id)
        msg = update["message"]
        del msg["text"]
        if caption is not None:
            msg["caption"] = caption
        msg["photo"] = [{"file_id": file_id, "width": 900, "height": 600, "file_size": 1000}]
        msg["media_group_id"] = "album-interleaved"
        return update

    # Item 1 arrives and mints a fresh event.
    r1 = client.post(
        "/v1/webhooks/telegram", json=_photo_update(60, "f1"), headers=_HDR
    )
    assert r1.status_code == 200

    # A daemon already long-polling drains it *before* item 2 arrives — the
    # interleaving the review named. Its cursor now sits at item 1's seq.
    first_drain = client.get(
        "/v1/daemons/inbox", params={"since": 0, "wait": 0}, headers=dmn
    ).json()
    assert len(first_drain["events"]) == 1
    cursor = first_drain["cursor"]

    # Item 2 merges into the same (already-delivered) event.
    r2 = client.post(
        "/v1/webhooks/telegram",
        json=_photo_update(61, "f2", caption="look at these"),
        headers=_HDR,
    )
    assert r2.status_code == 200

    with app.state.SessionLocal() as db:
        events = list(
            db.execute(select(Event).where(Event.source == "telegram")).scalars()
        )
        assert len(events) == 1, "the merge must still land in the one event, not a second"
        from brnrd import inbox as inbox_service
        assert [p["file_id"] for p in inbox_service.attachments_of(events[0])] == ["f1", "f2"]

    # The daemon polls again from its already-advanced cursor. The merged
    # growth must still be reachable — this is the assertion that was red
    # pre-fix (see docstring).
    second_drain = client.get(
        "/v1/daemons/inbox", params={"since": cursor, "wait": 0}, headers=dmn
    ).json()
    assert len(second_drain["events"]) == 1, (
        "the merged album item never re-entered delivery — the second photo "
        "is in the database and permanently unreachable over this cursor"
    )
    assert len(second_drain["events"][0]["attachments"]) == 2


def test_concurrent_album_webhooks_do_not_lose_attachments(monkeypatch, tmp_path):
    """#1396 — the merge path this PR adds (`_find_open_media_group` +
    `inbox.enqueue`'s read-modify-write of ``attachments_json``) has no row
    lock. Telegram delivers each item of one album as its own webhook call;
    ``telegram_webhook`` is a sync ``def``, so Starlette runs concurrent
    calls in its threadpool for real, and the engine is built with
    ``check_same_thread: False`` (db.py). Two items landing in overlapping
    requests can both read the same ``attachments_json``, both append, and
    the later commit overwrites the earlier — one photo silently gone.

    Drives the *real* caller path: genuine OS threads calling
    ``client.post`` (not asyncio tasks), barrier-synced to force maximum
    overlap, against a **file-backed** sqlite db — db.py's own docstring on
    ``make_engine`` documents that ``:memory:`` is pinned to a ``StaticPool``
    sharing one connection across every session, which is not an honest
    model of concurrent callers (see also `tests/test_brnrd_inbox.py::env`).
    Repeated over many trials and every attachment must survive every trial.

    One item per album is a **video**, not a photo (#1396 finding 2): a
    video has ``has_media=True`` but ``extract_attachments`` returns
    nothing for it (`brr/channels/telegram.py` only converts photos and
    image documents to pointers), so it merges with ``attachments=None``
    and a non-empty *annotated* body (the
    ``"[attached media not ingested...]"`` suffix webhooks.py appends).
    That is the exact shape the CAS hole missed: a merge guarded only by
    ``attachments_json`` lets a racing photo item's stale-``body`` write
    silently wipe that annotation, because the photo item's own CAS
    ``WHERE`` still matches (it never touched attachments either). Every
    trial must therefore keep *both* — every photo file id, and the video's
    annotation text somewhere in the final merged event's body.
    """
    import json
    import threading

    monkeypatch.setattr(
        "brnrd.platforms.telegram.send_message",
        lambda token, chat_id, text, **kw: None,
    )
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'race.db'}",
        telegram_bot_token="bot:TOKEN",
        telegram_webhook_secret=_SECRET,
        inbox_long_poll_max_s=0.2,
        inbox_poll_interval_s=0.02,
        # 20 trials x 6 items/album would blow the default free-tier burst
        # ceiling (6/min) long before the race window is exercised at all —
        # this test is about the merge race, not the admission throttle, so
        # lift both bounds well past what it drives.
        limit_free_events_per_minute=10_000,
        limit_abuse_events_per_minute=10_000,
    )
    app = create_app(settings)
    client = TestClient(app)

    acc = _account(client)
    rid = _repo(client, acc)
    code = _tg_pair_code(client, acc, rid)
    client.post("/v1/webhooks/telegram", json=_message(555, f"/start {code}"), headers=_HDR)

    N = 5  # photo items per album
    _VIDEO_MARKER = "attached media not ingested"
    TRIALS = 20
    lost_trials = 0
    total_lost = 0
    lost_video_annotation_trials = 0

    for trial in range(TRIALS):
        group_id = f"race-{trial}"
        barrier = threading.Barrier(N + 1)
        errors: list[BaseException] = []

        def _send(i, group_id=group_id, trial=trial):
            try:
                barrier.wait(timeout=5)
                if i == N:
                    # The one video item — see docstring.
                    update = _message(
                        555, f"trial {trial} video", message_id=10_000 + trial * 100 + i,
                    )
                    msg = update["message"]
                    del msg["text"]
                    msg["caption"] = f"trial {trial} video"
                    msg["video"] = {"file_id": f"vid-{trial}", "duration": 3}
                else:
                    update = _message(555, "", message_id=10_000 + trial * 100 + i)
                    msg = update["message"]
                    del msg["text"]
                    msg["photo"] = [
                        {"file_id": f"f-{trial}-{i}", "width": 900, "height": 600, "file_size": 1000}
                    ]
                msg["media_group_id"] = group_id
                r = client.post("/v1/webhooks/telegram", json=update, headers=_HDR)
                r.raise_for_status()
            except BaseException as exc:  # noqa: BLE001 - surfaced below
                errors.append(exc)

        threads = [threading.Thread(target=_send, args=(i,)) for i in range(N + 1)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if errors:
            raise errors[0]

        with app.state.SessionLocal() as db:
            events = list(
                db.execute(select(Event).where(Event.source == "telegram")).scalars()
            )
        matching = [
            e for e in events
            if json.loads(e.reply_to or "{}").get("media_group_id") == group_id
        ]
        seen_ids = {
            p["file_id"]
            for e in matching
            for p in json.loads(e.attachments_json or "[]")
        }
        if len(seen_ids) != N:
            lost_trials += 1
            total_lost += N - len(seen_ids)
        if not any(_VIDEO_MARKER in (e.body or "") for e in matching):
            lost_video_annotation_trials += 1

    assert lost_trials == 0, (
        f"lost an attachment in {lost_trials}/{TRIALS} trials "
        f"({total_lost} attachments lost total, {N} items/album)"
    )
    assert lost_video_annotation_trials == 0, (
        f"a racing photo item wiped the video's annotated body in "
        f"{lost_video_annotation_trials}/{TRIALS} trials — the CAS hole #1396 "
        "finding 2 named"
    )


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


def test_send_fresh_message_returns_the_last_chunks_id(monkeypatch):
    """#1205's fresh-send primitive — no reply target, but a caller needs
    the platform id back (``POST /v1/daemons/messages``'s whole contract)."""
    from brnrd.platforms import telegram as tg

    posts: list[dict] = []

    class _Resp:
        def __init__(self, message_id):
            self._message_id = message_id

        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True, "result": {"message_id": self._message_id}}

    import itertools
    ids = itertools.count(101)

    def fake_post(url, json=None, timeout=None):
        posts.append(json)
        return _Resp(next(ids))

    monkeypatch.setattr(tg.httpx, "post", fake_post)

    body = "\n".join(f"line {i} " + "x" * 300 for i in range(80))  # well past 4096
    message_id = tg.send_fresh_message("bot:T", 555, body, topic_id=7)

    assert len(posts) >= 2  # fanned out across messages, same chunking as send_message
    assert all("reply_to_message_id" not in p for p in posts)  # nothing to thread onto
    assert all(p.get("message_thread_id") == 7 for p in posts)
    # the *last* chunk's id — what the correspondent sees last on the screen
    assert message_id == str(100 + len(posts))


def test_send_fresh_message_empty_result_returns_none(monkeypatch):
    from brnrd.platforms import telegram as tg

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True, "result": {}}

    monkeypatch.setattr(tg.httpx, "post", lambda url, json=None, timeout=None: _Resp())

    assert tg.send_fresh_message("bot:T", 555, "hi") is None


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
    # #1282 — no daemon is registered yet at this point in the test, so the
    # "task" message above also drew the no-runner nudge; this test is about
    # response forwarding, so drop it (covered separately).
    sends.clear()

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
    assert code.startswith("PK-")
    # @-prefix stripped; the pair code rides as the tap-to-open start= param.
    assert body["deep_link"] == f"https://t.me/brnrd_bot?start={code}"
    assert body["deep_link"] in body["instructions"]
    assert f"For WhatsApp, text `{code}` by itself" in body["instructions"]
    assert "no `/start`" in body["instructions"]


def test_telegram_pair_omits_deep_link_without_username(env):
    _, client, _ = env  # fixture Settings sets no telegram_bot_username
    acc = _account(client)
    rid = _repo(client, acc)
    body = client.post(
        "/v1/accounts/pair/telegram", json={"repo_id": rid}, headers=acc
    ).json()
    assert body["deep_link"] is None
    assert f"/start {body['pair_code']}" in body["instructions"]
    assert f"For WhatsApp, text `{body['pair_code']}` by itself" in body["instructions"]


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


# ── w-52 pre-alpha teams: the room-membership grant ──────────────────
# The room is the address (w-52): with telegram_open_rooms enabled, a
# paired group/supergroup authorizes any identifiable sender — the room's
# admins control membership, so the room is the grant. Default stays
# closed (#409); private chats never widen; anonymity never speaks.


def _room_env(monkeypatch, *, open_rooms):
    monkeypatch.setattr("brnrd.platforms.telegram.send_message", lambda *a, **k: None)
    settings = Settings(
        database_url="sqlite:///:memory:",
        telegram_bot_token="bot:TOKEN",
        telegram_webhook_secret=_SECRET,
        telegram_open_rooms=open_rooms,
    )
    app = create_app(settings)
    client = TestClient(app)
    acc = _account(client)
    rid = _repo(client, acc)
    code = _tg_pair_code(client, acc, rid)
    # The principal pairs the ROOM (a supergroup), exactly the POC shape.
    client.post(
        "/v1/webhooks/telegram",
        json=_message(-100555, f"/start {code}", chat_type="supergroup"),
        headers=_HDR,
    )
    return app, client, rid


def test_room_grant_off_refuses_a_group_stranger(monkeypatch):
    app, client, _rid = _room_env(monkeypatch, open_rooms=False)
    r = client.post(
        "/v1/webhooks/telegram",
        json=_message(-100555, "do the thing", chat_type="supergroup",
                      user_id=999, username="sasha", name="Sasha"),
        headers=_HDR,
    )
    assert r.status_code == 200
    with app.state.SessionLocal() as db:
        assert db.execute(select(Event)).scalars().all() == []


def test_room_grant_on_enqueues_a_group_member_with_attribution(monkeypatch):
    app, client, rid = _room_env(monkeypatch, open_rooms=True)
    r = client.post(
        "/v1/webhooks/telegram",
        json=_message(-100555, "ship it", chat_type="supergroup",
                      user_id=999, username="sasha", name="Sasha"),
        headers=_HDR,
    )
    assert r.status_code == 200
    with app.state.SessionLocal() as db:
        event = db.execute(select(Event).where(Event.source == "telegram")).scalar_one()
        assert event.repo_id == rid
        assert event.body == "ship it"
        # Attribution is part of the grant: the event names the member.
        import json as _json

        reply_to = _json.loads(event.reply_to)
        assert reply_to["user_id"] == 999
        assert reply_to["username"] == "sasha"


def test_room_grant_never_widens_a_private_chat(monkeypatch):
    monkeypatch.setattr("brnrd.platforms.telegram.send_message", lambda *a, **k: None)
    settings = Settings(
        database_url="sqlite:///:memory:",
        telegram_bot_token="bot:TOKEN",
        telegram_webhook_secret=_SECRET,
        telegram_open_rooms=True,
    )
    app = create_app(settings)
    client = TestClient(app)
    acc = _account(client)
    rid = _repo(client, acc)
    code = _tg_pair_code(client, acc, rid)
    client.post(
        "/v1/webhooks/telegram",
        json=_message(555, f"/start {code}", chat_type="private"),
        headers=_HDR,
    )
    r = client.post(
        "/v1/webhooks/telegram",
        json=_message(555, "do the thing", chat_type="private",
                      user_id=999, username="mallory", name="Mallory"),
        headers=_HDR,
    )
    assert r.status_code == 200
    with app.state.SessionLocal() as db:
        assert db.execute(select(Event)).scalars().all() == []


def test_room_grant_refuses_an_untyped_chat_even_when_open(monkeypatch):
    # chat.type absent ⇒ chat_type == "" ⇒ the group clause must not fire:
    # authorization widens only on Telegram's own verifiable classification.
    app, client, _rid = _room_env(monkeypatch, open_rooms=True)
    r = client.post(
        "/v1/webhooks/telegram",
        json=_message(-100555, "do the thing", user_id=999),
        headers=_HDR,
    )
    assert r.status_code == 200
    with app.state.SessionLocal() as db:
        assert db.execute(select(Event)).scalars().all() == []


def test_room_grant_refuses_anonymous_admins(monkeypatch):
    # sender_chat ⇒ user_id None (#409): a room grants members, never masks.
    app, client, _rid = _room_env(monkeypatch, open_rooms=True)
    payload = _message(-100555, "do the thing", chat_type="supergroup", user_id=77)
    payload["message"]["sender_chat"] = {"id": -100555}
    r = client.post("/v1/webhooks/telegram", json=payload, headers=_HDR)
    assert r.status_code == 200
    with app.state.SessionLocal() as db:
        assert db.execute(select(Event)).scalars().all() == []

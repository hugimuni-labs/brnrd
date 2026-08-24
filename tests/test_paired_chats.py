"""#1464 — the transparency + revocation floor for messenger pairing.

Three surfaces, each closing one direction the bind used to be silent
about:

- redemption stamps a display label (and, when the platform supplies one,
  a chat title) onto the `ChannelRoute` it creates/updates, and onto the
  `TgPairCode` it consumed;
- `GET /v1/dashboard/pair/{code}` lets the minting session read
  that back while its panel is still open;
- `GET /v1/dashboard/paired-chats` lists every route on the account, and
  `DELETE /v1/dashboard/paired-chats/{id}` revokes one — which must stop
  the route *authorizing*, not just remove a row.

Confirm-on-redeem is explicitly out of scope (the issue's own product
call) — nothing here tests for it.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from brnrd import create_app  # noqa: E402
from brnrd import inbox as inbox_service  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.models import ChannelRoute, Event, TgPairCode  # noqa: E402
from _helpers import brnrd_account_headers  # noqa: E402

_SECRET = "webhook-secret"
_HDR = {"X-Telegram-Bot-Api-Secret-Token": _SECRET}


def _make_client(monkeypatch, **overrides):
    sends: list[dict] = []

    def fake_send(token, chat_id, text, *, topic_id=None, reply_to_message_id=None, timeout=30.0):
        sends.append({"chat_id": chat_id, "text": text})

    monkeypatch.setattr("brnrd.platforms.telegram.send_message", fake_send)
    kwargs = dict(
        database_url="sqlite:///:memory:",
        telegram_bot_token="bot:TOKEN",
        telegram_webhook_secret=_SECRET,
        telegram_bot_username="brnrd_bot",
        inbox_long_poll_max_s=0.2,
        inbox_poll_interval_s=0.02,
    )
    kwargs.update(overrides)
    settings = Settings(**kwargs)
    app = create_app(settings)
    return app, TestClient(app), sends


@pytest.fixture()
def env(monkeypatch):
    return _make_client(monkeypatch)


def _account(client, *, login="octocat"):
    # No explicit `github_id`: let it derive from `login` so two different
    # logins land as two different accounts (a fixed id would collide them).
    return brnrd_account_headers(client.app, login=login, email=f"{login}@example.com")


def _login_session(client, headers):
    token = headers["Authorization"].removeprefix("Bearer ")
    client.cookies.set("brnrd_session", token)


def _account_code(client) -> str:
    r = client.post("/v1/dashboard/telegram-pair")
    assert r.status_code == 200, r.text
    return r.json()["pair_code"]


def _message(chat_id, text, *, message_id=1, user_id=42, chat_type="private", chat_title=None, username="ada_l", first_name="Ada"):
    chat: dict = {"id": chat_id, "type": chat_type}
    if chat_title is not None:
        chat["title"] = chat_title
    frm: dict = {"id": user_id, "first_name": first_name}
    if username is not None:
        frm["username"] = username
    return {
        "message": {
            "message_id": message_id,
            "date": int(datetime.now(timezone.utc).timestamp()),
            "chat": chat,
            "from": frm,
            "text": text,
        }
    }


def _post(client, payload):
    r = client.post("/v1/webhooks/telegram", json=payload, headers=_HDR)
    assert r.status_code == 200, r.text
    return r


def _route(app, chat_id):
    with app.state.SessionLocal() as db:
        return db.execute(select(ChannelRoute).where(ChannelRoute.channel_id == str(chat_id))).scalar_one_or_none()


# ── redemption stamps the display (and title, when known) ──────────────


def test_redeem_stamps_username_display_on_the_route(env):
    app, client, sends = env
    headers = _account(client)
    _login_session(client, headers)
    code = _account_code(client)

    _post(client, _message(2001, f"/start {code}", username="ada_l"))

    route = _route(app, 2001)
    assert route.paired_user_display == "@ada_l"
    assert route.chat_title is None  # private chat: no title concept


def test_redeem_falls_back_to_first_name_with_no_username(env):
    app, client, sends = env
    headers = _account(client)
    _login_session(client, headers)
    code = _account_code(client)

    _post(client, _message(2002, f"/start {code}", username=None, first_name="Ada"))

    route = _route(app, 2002)
    assert route.paired_user_display == "Ada"


def test_redeem_in_a_group_captures_the_chat_title(env):
    app, client, sends = env
    headers = _account(client)
    _login_session(client, headers)
    code = _account_code(client)

    _post(
        client,
        _message(2003, f"/start {code}", chat_type="group", chat_title="Ops Room"),
    )

    route = _route(app, 2003)
    assert route.chat_title == "Ops Room"


def test_repair_refreshes_the_display(env):
    """A chat re-paired under a new code (new code, same chat) gets the
    *current* display, never a stale one left over from the first pair —
    same "re-pair overwrites" rule `paired_user_id` already followed."""
    app, client, sends = env
    headers = _account(client)
    _login_session(client, headers)
    code1 = _account_code(client)
    _post(client, _message(2004, f"/start {code1}", username="ada_l"))

    code2 = _account_code(client)
    _post(client, _message(2004, f"/start {code2}", username="ada_two", message_id=2))

    route = _route(app, 2004)
    assert route.paired_user_display == "@ada_two"


# ── the minting session's outcome readback ──────────────────────────────


def test_pair_status_before_redeem_is_unconsumed(env):
    app, client, sends = env
    headers = _account(client)
    _login_session(client, headers)
    code = _account_code(client)

    r = client.get(f"/v1/dashboard/pair/{code}")
    assert r.status_code == 200, r.text
    assert r.json() == {"consumed": False, "display": None}


def test_pair_status_after_redeem_carries_the_display(env):
    app, client, sends = env
    headers = _account(client)
    _login_session(client, headers)
    code = _account_code(client)
    _post(client, _message(2005, f"/start {code}", username="ada_l"))

    r = client.get(f"/v1/dashboard/pair/{code}")
    assert r.status_code == 200, r.text
    assert r.json() == {"consumed": True, "display": "@ada_l"}


def test_pair_status_requires_a_session(env):
    app, client, sends = env
    code = _account_code_as(env)
    r = client.get(f"/v1/dashboard/pair/{code}")
    assert r.status_code == 401


def _account_code_as(env):
    app, client, sends = env
    headers = _account(client)
    _login_session(client, headers)
    code = _account_code(client)
    client.cookies.clear()
    return code


def test_pair_status_scoped_to_the_minting_account(env):
    """A session for a *different* account learns nothing about a code it
    didn't mint — not even that it exists."""
    app, client, sends = env
    headers = _account(client, login="octocat")
    _login_session(client, headers)
    code = _account_code(client)

    other_headers = _account(client, login="mallory")
    _login_session(client, other_headers)
    r = client.get(f"/v1/dashboard/pair/{code}")
    assert r.status_code == 404


def test_pair_status_unknown_code_is_404(env):
    app, client, sends = env
    headers = _account(client)
    _login_session(client, headers)
    r = client.get("/v1/dashboard/pair/PK-NOPE")
    assert r.status_code == 404


# ── the paired-chats list ────────────────────────────────────────────────


def test_paired_chats_list_carries_platform_title_display_and_paired_at(env):
    app, client, sends = env
    headers = _account(client)
    _login_session(client, headers)
    code = _account_code(client)
    _post(client, _message(2006, f"/start {code}", chat_type="group", chat_title="Ops Room", username="ada_l"))

    r = client.get("/v1/dashboard/paired-chats")
    assert r.status_code == 200, r.text
    rows = r.json()["paired_chats"]
    assert len(rows) == 1
    row = rows[0]
    assert row["platform"] == "telegram"
    assert row["paired"] is True
    assert row["chat_title"] == "Ops Room"
    assert row["principal_display"] == "@ada_l"
    assert row["paired_at"] is not None
    assert row["repo_full_name"] is None  # account-level: no pin


def test_paired_chats_list_requires_a_session(env):
    app, client, sends = env
    r = client.get("/v1/dashboard/paired-chats")
    assert r.status_code == 401


def test_paired_chats_list_is_scoped_to_the_account(env):
    app, client, sends = env
    headers = _account(client, login="octocat")
    _login_session(client, headers)
    code = _account_code(client)
    _post(client, _message(2007, f"/start {code}"))

    other_headers = _account(client, login="mallory")
    _login_session(client, other_headers)
    r = client.get("/v1/dashboard/paired-chats")
    assert r.json()["paired_chats"] == []


# ── revoke: kills the principal, not just the row ───────────────────────


def test_revoke_deletes_the_route(env):
    app, client, sends = env
    headers = _account(client)
    _login_session(client, headers)
    code = _account_code(client)
    _post(client, _message(2008, f"/start {code}"))
    route_id = _route(app, 2008).id

    r = client.delete(f"/v1/dashboard/paired-chats/{route_id}")
    assert r.status_code == 200, r.text
    assert _route(app, 2008) is None


def test_revoke_stops_the_chat_authorizing(env):
    """The authz consequence, not just the row deletion (#409's
    default-closed contract: nothing here may widen who is authorized, and
    a revoked route must narrow back to nobody)."""
    app, client, sends = env
    headers = _account(client)
    _login_session(client, headers)
    repo_id = client.post(
        "/v1/accounts/repos", json={"repo_full_name": "Gurio/demo"}, headers=headers
    ).json()["repo_id"]
    code = _account_code(client)
    _post(client, _message(2009, f"/start {code}"))
    route_id = _route(app, 2009).id

    # Before revoke: the paired principal's message enqueues normally.
    _post(client, _message(2009, "do the thing", message_id=2))
    with app.state.SessionLocal() as db:
        before = len(list(db.execute(select(Event)).scalars()))
    assert before == 1

    r = client.delete(f"/v1/dashboard/paired-chats/{route_id}")
    assert r.status_code == 200, r.text

    # After revoke: the exact same sender, same chat — refused as unpaired,
    # not silently authorized, and nothing new is enqueued.
    _post(client, _message(2009, "do it again", message_id=3))
    with app.state.SessionLocal() as db:
        after = list(db.execute(select(Event)).scalars())
    assert len(after) == before
    assert "not paired" in sends[-1]["text"].lower() or "pair" in sends[-1]["text"].lower()


def test_revoke_requires_a_session(env):
    app, client, sends = env
    headers = _account(client)
    _login_session(client, headers)
    code = _account_code(client)
    _post(client, _message(2010, f"/start {code}"))
    route_id = _route(app, 2010).id
    client.cookies.clear()

    r = client.delete(f"/v1/dashboard/paired-chats/{route_id}")
    assert r.status_code == 401
    assert _route(app, 2010) is not None


def test_revoke_scoped_to_the_owning_account(env):
    app, client, sends = env
    headers = _account(client, login="octocat")
    _login_session(client, headers)
    code = _account_code(client)
    _post(client, _message(2011, f"/start {code}"))
    route_id = _route(app, 2011).id

    other_headers = _account(client, login="mallory")
    _login_session(client, other_headers)
    r = client.delete(f"/v1/dashboard/paired-chats/{route_id}")
    assert r.status_code == 404
    assert _route(app, 2011) is not None

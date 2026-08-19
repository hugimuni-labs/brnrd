"""#1457 — account-level messenger pairing and message-time repo resolution.

The chat pairs to the *account*; the repo is routing, not identity:

- `POST /v1/dashboard/telegram-pair` mints a repo-less code from the
  browser session — the mobile cold start's constructible deep link.
- Consuming such a code binds a `ChannelRoute` with `repo_id NULL`.
- A message on an account-level route resolves its repo per message
  (`webhooks._route_target_repo`): pin → sole repo → recency → updated_at.
- `/repo owner/name` pins, `/repo auto` un-pins, `/status` says which.
- Disconnecting a repo un-pins routes instead of killing the pairing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from brnrd import create_app, ids  # noqa: E402
from brnrd import inbox as inbox_service  # noqa: E402
from brnrd.app import _maybe_derive_telegram_bot_username  # noqa: E402
from brnrd.config import Settings, _derived_telegram_usernames  # noqa: E402
from brnrd.models import ChannelRoute, Event, Repo, TgPairCode  # noqa: E402
from _helpers import brnrd_account_headers  # noqa: E402

_SECRET = "webhook-secret"
_HDR = {"X-Telegram-Bot-Api-Secret-Token": _SECRET}


@pytest.fixture(autouse=True)
def _isolate_telegram_derived_username():
    """See the twin fixture in test_brnrd_telegram.py — same cache, same
    cross-test leak risk from reusing the literal token ``"bot:TOKEN"``."""
    _derived_telegram_usernames.clear()
    yield
    _derived_telegram_usernames.clear()


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


def _account(client):
    return brnrd_account_headers(client.app, github_id="123", login="octocat", email="a@b.com")


def _login_session(client, headers):
    """The dashboard endpoints authenticate by cookie, not header — reuse
    the same session token the header carries."""
    token = headers["Authorization"].removeprefix("Bearer ")
    client.cookies.set("brnrd_session", token)


def _repo(client, headers, name="demo"):
    return client.post(
        "/v1/accounts/repos", json={"repo_full_name": f"Gurio/{name}"}, headers=headers
    ).json()["repo_id"]


def _account_code(client) -> str:
    r = client.post("/v1/dashboard/telegram-pair")
    assert r.status_code == 200, r.text
    return r.json()["pair_code"]


def _message(chat_id, text, *, message_id=1, user_id=42):
    return {
        "message": {
            "message_id": message_id,
            "date": int(datetime.now(timezone.utc).timestamp()),
            "chat": {"id": chat_id},
            "from": {"id": user_id, "first_name": "Ada", "username": "ada_l"},
            "text": text,
        }
    }


def _post(client, payload):
    r = client.post("/v1/webhooks/telegram", json=payload, headers=_HDR)
    assert r.status_code == 200, r.text
    return r


def _route(app, chat_id):
    with app.state.SessionLocal() as db:
        return db.execute(
            select(ChannelRoute).where(ChannelRoute.channel_id == str(chat_id))
        ).scalar_one_or_none()


def _events(app):
    with app.state.SessionLocal() as db:
        return list(db.execute(select(Event)).scalars())


# ── the mint ─────────────────────────────────────────────────────────


def test_account_mint_requires_a_session(env):
    app, client, sends = env
    r = client.post("/v1/dashboard/telegram-pair")
    assert r.status_code == 401


def test_account_mint_needs_no_repo_and_builds_the_deep_link(env):
    """The gap that made the mobile cold start unconstructible: a signed-in
    account with zero repos gets a working `t.me/<bot>?start=<code>`."""
    app, client, sends = env
    headers = _account(client)
    _login_session(client, headers)

    r = client.post("/v1/dashboard/telegram-pair")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deep_link"] == f"https://t.me/brnrd_bot?start={body['pair_code']}"
    assert "your account" in body["instructions"]
    with app.state.SessionLocal() as db:
        pc = db.execute(select(TgPairCode).where(TgPairCode.code == body["pair_code"])).scalar_one()
        assert pc.repo_id is None


# ── #1465: the generalized mint, `POST /v1/dashboard/pair` ────────────


def test_generalized_pair_requires_a_session(env):
    app, client, sends = env
    r = client.post("/v1/dashboard/pair", json={"platform": "telegram"})
    assert r.status_code == 401


def test_generalized_pair_defaults_to_telegram_with_no_body(env):
    app, client, sends = env
    headers = _account(client)
    _login_session(client, headers)
    r = client.post("/v1/dashboard/pair")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["platform"] == "telegram"
    assert body["deep_link"] == f"https://t.me/brnrd_bot?start={body['pair_code']}"


def test_generalized_pair_mints_a_whatsapp_deep_link_when_configured(monkeypatch):
    app, client, sends = _make_client(
        monkeypatch, whatsapp_access_token="tok", whatsapp_phone_number_id="123"
    )
    # No lifespan has run against this bare TestClient (see
    # `messenger_doors.py`'s module docstring), so the WhatsApp identity
    # rides the request's own cache slot directly — the same seam
    # `_messenger_identities` reads for a request that finds nothing
    # startup-derived.
    from brnrd.messenger_doors import MessengerIdentities

    app.state.messenger_identities = MessengerIdentities(
        telegram_bot_username="brnrd_bot", whatsapp_e164="15551234567"
    )
    headers = _account(client)
    _login_session(client, headers)
    r = client.post("/v1/dashboard/pair", json={"platform": "whatsapp"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["platform"] == "whatsapp"
    assert body["deep_link"] == f"https://wa.me/15551234567?text={body['pair_code']}"
    assert "your account" in body["instructions"]
    with app.state.SessionLocal() as db:
        pc = db.execute(select(TgPairCode).where(TgPairCode.code == body["pair_code"])).scalar_one()
        assert pc.repo_id is None


def test_generalized_pair_refuses_a_platform_with_no_deep_link_door(env):
    app, client, sends = env
    headers = _account(client)
    _login_session(client, headers)
    r = client.post("/v1/dashboard/pair", json={"platform": "slack"})
    assert r.status_code == 409


def test_generalized_pair_refuses_an_unknown_platform(env):
    app, client, sends = env
    headers = _account(client)
    _login_session(client, headers)
    r = client.post("/v1/dashboard/pair", json={"platform": "carrier-pigeon"})
    assert r.status_code == 404


def test_generalized_pair_refuses_whatsapp_when_unconfigured(env):
    """Telegram is configured in the `env` fixture, WhatsApp is not — the
    registry must say so per-platform, never a single account-wide switch."""
    app, client, sends = env
    headers = _account(client)
    _login_session(client, headers)
    r = client.post("/v1/dashboard/pair", json={"platform": "whatsapp"})
    assert r.status_code == 409


def test_dashboard_repos_carries_the_bot_username(env):
    app, client, sends = env
    headers = _account(client)
    _login_session(client, headers)
    body = client.get("/v1/dashboard/repos").json()
    assert body["telegram_bot_username"] == "brnrd_bot"


def test_dashboard_repos_carries_the_registry_derived_messenger_doors(env):
    """#1465 — the wire array every connector's `deep_link_available` flag
    rides; the registry test in `test_messenger_doors.py` covers the
    module itself, this covers it reaching the wire unmodified."""
    app, client, sends = env
    headers = _account(client)
    _login_session(client, headers)
    body = client.get("/v1/dashboard/repos").json()
    doors = {d["platform"]: d["deep_link_available"] for d in body["messenger_doors"]}
    assert doors["telegram"] is True  # `env` configures a valid bot username
    assert doors["whatsapp"] is False  # not configured in this fixture
    assert doors["slack"] is False
    assert doors["signal"] is False


def test_dashboard_repos_carries_the_off_door_reason(env):
    """brr/every-door-on-the-page — the frontend renders every door,
    including dark ones, with an honest reason: a connector nobody ever
    built (slack, signal) reads differently from one this deployment just
    hasn't configured (whatsapp here)."""
    app, client, sends = env
    headers = _account(client)
    _login_session(client, headers)
    body = client.get("/v1/dashboard/repos").json()
    reasons = {d["platform"]: d["reason"] for d in body["messenger_doors"]}
    assert reasons["telegram"] is None
    assert reasons["whatsapp"] == "not_configured"
    assert reasons["slack"] == "not_built"
    assert reasons["signal"] == "not_built"


def test_generalized_pair_mint_carries_a_visible_expiry(env):
    """brr/every-door-on-the-page — the mint response needs its own
    `expires_at` so the page can render a live countdown instead of a link
    that just goes dead with no explanation; the deployment's short TTL
    (`messenger_pair_ttl_s`, not `pair_ttl_s`'s device-flow number)."""
    app, client, sends = env
    headers = _account(client)
    _login_session(client, headers)
    before = datetime.now(timezone.utc)
    r = client.post("/v1/dashboard/pair", json={"platform": "telegram"})
    assert r.status_code == 200, r.text
    body = r.json()
    expires_at = datetime.fromisoformat(body["expires_at"])
    ttl = app.state.settings.messenger_pair_ttl_s
    assert ttl < app.state.settings.pair_ttl_s  # shorter than the device-connect flow's
    assert before + timedelta(seconds=ttl - 5) <= expires_at <= before + timedelta(seconds=ttl + 5)


def test_invalid_bot_username_rides_the_wire_as_empty(monkeypatch):
    # #1242's rule at the new seam: a bad handle must not let the frontend
    # construct a link that resolves to no Telegram entity.
    app, client, sends = _make_client(monkeypatch, telegram_bot_username="has-hyphens")
    headers = _account(client)
    _login_session(client, headers)
    body = client.get("/v1/dashboard/repos").json()
    assert body["telegram_bot_username"] == ""


def test_dashboard_repos_carries_the_derived_bot_username(monkeypatch):
    """#1463, through the dashboard caller: a getMe-derived username must
    reach `GET /v1/dashboard/repos`'s wire field even when the env var is
    the invalid, hyphenated GitHub-login spelling (#1242's prod shape)."""
    monkeypatch.setattr(
        "brnrd.platforms.telegram.get_me",
        lambda token, *, timeout=10.0: {"id": 1, "username": "real_bot", "is_bot": True},
    )
    app, client, sends = _make_client(monkeypatch, telegram_bot_username="stale-bot")
    _maybe_derive_telegram_bot_username(app.state.settings)
    headers = _account(client)
    _login_session(client, headers)
    body = client.get("/v1/dashboard/repos").json()
    assert body["telegram_bot_username"] == "real_bot"


# ── consuming an account-level code ──────────────────────────────────


def test_account_code_pairs_route_with_no_repo_pin(env):
    app, client, sends = env
    headers = _account(client)
    _login_session(client, headers)
    code = _account_code(client)

    _post(client, _message(1001, f"/start {code}"))

    route = _route(app, 1001)
    assert route is not None
    assert route.repo_id is None
    assert route.paired_user_id == 42
    # No repos yet: the confirmation names the one step that stands
    # between here and work, and is honest that nothing is queued.
    # #1464 — the reply also names the bound GitHub login, not just "your
    # account": a wrong-account bind is now visible in the message itself.
    assert "Paired with octocat's brnrd account" in sends[-1]["text"]
    assert "no project is connected" in sends[-1]["text"].lower()


def test_minting_page_observes_pairing_complete_through_status_route(env):
    """#1530 — the origin page's poll sees the same code turn consumed."""
    app, client, sends = env
    headers = _account(client)
    _login_session(client, headers)
    code = _account_code(client)

    pending = client.get(f"/v1/dashboard/pair/{code}")
    assert pending.status_code == 200
    assert pending.json() == {"consumed": False, "display": None}

    _post(client, _message(1001, f"/start {code}"))

    paired = client.get(f"/v1/dashboard/pair/{code}")
    assert paired.status_code == 200
    assert paired.json() == {"consumed": True, "display": "@ada_l"}


def test_account_code_with_repos_names_the_resolved_target(env):
    app, client, sends = env
    headers = _account(client)
    _login_session(client, headers)
    _repo(client, headers, "solo")
    code = _account_code(client)

    _post(client, _message(1002, f"/start {code}"))

    assert "Paired with octocat's brnrd account" in sends[-1]["text"]
    assert "Gurio/solo" in sends[-1]["text"]


def test_message_on_account_route_with_no_repos_is_refused_out_loud(env):
    """Not guessing and not saying are the same act: the message is not
    queued (no repo lane exists to drain it), so the reply must say so."""
    app, client, sends = env
    headers = _account(client)
    _login_session(client, headers)
    code = _account_code(client)
    _post(client, _message(1003, f"/start {code}"))

    _post(client, _message(1003, "do the thing", message_id=2))

    assert _events(app) == []
    assert "nowhere to run" in sends[-1]["text"]


# ── resolution: pin → sole → recency → updated_at ────────────────────


def test_sole_repo_resolves_without_a_pin(env):
    app, client, sends = env
    headers = _account(client)
    _login_session(client, headers)
    repo_id = _repo(client, headers, "solo")
    code = _account_code(client)
    _post(client, _message(1004, f"/start {code}"))

    _post(client, _message(1004, "ship it", message_id=2))

    events = _events(app)
    assert [e.repo_id for e in events] == [repo_id]


def test_recency_resolves_among_many(env):
    """With two repos and no pin, the message lands where the conversation
    already lives: the repo of the account's most recent event."""
    app, client, sends = env
    headers = _account(client)
    _login_session(client, headers)
    alpha = _repo(client, headers, "alpha")
    beta = _repo(client, headers, "beta")
    with app.state.SessionLocal() as db:
        inbox_service.enqueue(db, repo_id=beta, body="prior work", source="test")
        # Deliberately make the *fallback* rung (updated_at) point at alpha
        # while the event recency points at beta: a test where both rungs
        # agree proves the ordering the code incidentally has, not the rule.
        now = datetime.now(timezone.utc)
        db.get(Repo, alpha).updated_at = now + timedelta(minutes=5)
        db.get(Repo, beta).updated_at = now - timedelta(days=1)
        db.commit()
    code = _account_code(client)
    _post(client, _message(1005, f"/start {code}"))

    _post(client, _message(1005, "continue", message_id=2))

    newest = max(_events(app), key=lambda e: e.seq)
    assert newest.repo_id == beta


def test_no_events_resolves_to_most_recently_updated(env):
    app, client, sends = env
    headers = _account(client)
    _login_session(client, headers)
    alpha = _repo(client, headers, "alpha")
    beta = _repo(client, headers, "beta")
    with app.state.SessionLocal() as db:
        now = datetime.now(timezone.utc)
        db.get(Repo, alpha).updated_at = now - timedelta(days=3)
        db.get(Repo, beta).updated_at = now
        db.commit()
    code = _account_code(client)
    _post(client, _message(1006, f"/start {code}"))

    _post(client, _message(1006, "go", message_id=2))

    assert [e.repo_id for e in _events(app)] == [beta]


def test_repo_pin_and_repo_auto(env):
    app, client, sends = env
    headers = _account(client)
    _login_session(client, headers)
    alpha = _repo(client, headers, "alpha")
    _repo(client, headers, "beta")
    code = _account_code(client)
    _post(client, _message(1007, f"/start {code}"))

    _post(client, _message(1007, "/repo Gurio/alpha", message_id=2))
    route = _route(app, 1007)
    assert route.repo_id == alpha
    _post(client, _message(1007, "/status", message_id=3))
    assert "Gurio/alpha" in sends[-1]["text"]

    _post(client, _message(1007, "/repo auto", message_id=4))
    route = _route(app, 1007)
    assert route.repo_id is None
    _post(client, _message(1007, "/status", message_id=5))
    assert "auto" in sends[-1]["text"]


def test_stale_pin_falls_through_to_account_resolution(env):
    """A pinned repo that no longer exists must not dead-end the chat —
    the pairing belongs to the account and outlives any one repo."""
    app, client, sends = env
    headers = _account(client)
    _login_session(client, headers)
    _repo(client, headers, "alpha")
    beta_name = "beta"
    beta = _repo(client, headers, beta_name)
    code = _account_code(client)
    _post(client, _message(1008, f"/start {code}"))
    _post(client, _message(1008, "/repo Gurio/beta", message_id=2))

    r = client.post(f"/v1/repos/{beta}/disconnect")
    assert r.status_code == 200, r.text

    # The route survived, un-pinned — and the next message still runs.
    route = _route(app, 1008)
    assert route is not None
    assert route.repo_id is None
    _post(client, _message(1008, "keep going", message_id=3))
    events = _events(app)
    assert len(events) == 1
    with app.state.SessionLocal() as db:
        assert db.get(Repo, events[0].repo_id).repo_full_name == "Gurio/alpha"


# ── legacy repo-scoped codes are unchanged ───────────────────────────


def test_repo_scoped_code_still_pins(env):
    app, client, sends = env
    headers = _account(client)
    _login_session(client, headers)
    repo_id = _repo(client, headers, "classic")
    code = client.post(
        "/v1/accounts/pair/telegram", json={"repo_id": repo_id}, headers=headers
    ).json()["pair_code"]

    _post(client, _message(1009, f"/start {code}"))

    route = _route(app, 1009)
    assert route.repo_id == repo_id
    # #1464 — names the bound account too, not just the repo.
    assert "Paired with octocat's brnrd account, repo 'Gurio/classic'" in sends[-1]["text"]

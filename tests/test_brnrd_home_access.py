"""The home gate (brnrd.home_access): off by default, one decision, refusals through the real routers."""
import re
import time
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from sqlalchemy import select

from brnrd import billing, home_access
from brnrd.auth import require_daemon
from brnrd.models import Account
from _helpers import brnrd_account_headers, brnrd_client, brnrd_repo_and_daemon

NOW = datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# Every protected operation: (method, path, json body); {repo} is the paired repo's id.
SESSION_ROUTES = [
    ("GET", "/v1/dashboard/quota", None),
    ("GET", "/v1/dashboard/news", None),
    ("GET", "/v1/dashboard/loom", None),
    ("GET", "/v1/dashboard/runners", None),
    ("GET", "/v1/dashboard/bench", None),
    ("GET", "/v1/dashboard/bench/brr/x.md", None),
    ("GET", "/v1/dashboard/live-runs", None),
    ("GET", "/v1/dashboard/pr-review-queue", None),
    ("GET", "/v1/dashboard/run-ledger", None),
    ("GET", "/v1/dashboard/surface", None),
    ("GET", "/v1/dashboard/warp/asks.json", None),
    ("GET", "/v1/dashboard/warp/says/evt_x", None),
    ("GET", "/v1/dashboard/activity", None),
    ("GET", "/v1/loom/index", None),
    ("GET", "/v1/loom/beads", None),
    ("GET", "/v1/loom/topics", None),
    ("GET", "/v1/loom/unthreaded", None),
    ("GET", "/v1/loom/thread/main", None),
    ("GET", "/v1/loom/events", None),
    ("POST", "/v1/loom/letters", {"body": "hello"}),
    ("POST", "/v1/dashboard/loom/letters", {"body": "hello"}),
    ("POST", "/v1/dashboard/runners/wake-request", {}),
    ("POST", "/v1/dashboard/runs/run_x/respawn", {}),
    ("POST", "/v1/dashboard/telegram-pair", None),
    ("POST", "/v1/dashboard/pair", {"platform": "telegram"}),
    ("POST", "/v1/repos/{repo}/telegram-pair", None),
]
DAEMON_ROUTES = [
    ("GET", "/v1/daemons/inbox?wait=0", None),
    ("PUT", "/v1/daemons/activity", {"records": []}),
    ("PUT", "/v1/daemons/surface", {"files": []}),
    ("PUT", "/v1/daemons/quota", {"shells": []}),
    ("PUT", "/v1/daemons/news", {"items": []}),
    ("PUT", "/v1/daemons/loom", {"v": 1}),
    ("PUT", "/v1/daemons/loom/parts", {"keys": [], "parts": {}}),
    ("PUT", "/v1/daemons/runners", {"runners": []}),
    ("PUT", "/v1/daemons/live-runs", {"runs": []}),
    ("PUT", "/v1/daemons/pr-review-queue", {"items": []}),
    ("PUT", "/v1/daemons/run-ledger", {"rows": []}),
    ("POST", "/v1/daemons/loom/presence", []),
    ("POST", "/v1/daemons/loom/typing", []),
    ("POST", "/v1/daemons/runners/wake-request/claim", {}),
    ("POST", "/v1/daemons/responses", {"event_id": "evt_x", "text": "hi"}),
    ("POST", "/v1/daemons/messages", {"text": "hi"}),
    ("POST", "/v1/daemons/card", {"text": "hi"}),
    ("POST", "/v1/daemons/pack", {"text": "hi"}),
    ("POST", "/v1/daemons/door", {}),
    ("POST", "/v1/daemons/publishing-credential", {}),
]
# Account controls, billing, deletion, connecting: open to everyone, gate on or off.
OPEN_SESSION = [
    ("GET", "/v1/accounts/subscription", None),
    ("GET", "/v1/accounts/home-trial", None),
    ("GET", "/v1/accounts/wallet", None),
    ("GET", "/v1/dashboard/repos", None),
    ("GET", "/v1/dashboard/config", None),
    ("GET", "/v1/dashboard/config-requests", None),
    ("GET", "/v1/dashboard/paired-chats", None),
    ("GET", "/v1/dashboard/terms-status", None),
    ("POST", "/v1/accounts/subscription/checkout", {"cadence": "monthly"}),
    ("POST", "/v1/accounts/subscription/portal", {}),
]
OPEN_DAEMON = [
    ("POST", "/v1/daemons/register", {"daemon_name": "computer"}),
    ("GET", "/v1/daemons/whoami", None),
    ("GET", "/v1/daemons/chats", None),
]


def _setup(gate_from: str = "", started: datetime | None = NOW - timedelta(days=30), tier: str = Account.TIER_FREE, **kw):
    client = brnrd_client(home_gate_from=gate_from, home_gate_hold_s=0, inbox_long_poll_max_s=0, **kw)
    account, daemon, repo = brnrd_repo_and_daemon(client)
    with client.app.state.SessionLocal() as db:
        row = db.execute(select(Account)).scalar_one()
        row.home_trial_started_at = None if started is None else started.replace(tzinfo=None)
        row.tier = tier
        db.commit()
    client.cookies.set("brnrd_session", account["Authorization"].removeprefix("Bearer "))
    return client, account, daemon, repo


def _bytes(r):
    """The response as sent, but for the clock a route stamps on every answer."""
    return r.status_code, re.sub(rb'"generated_at":"[^"]*"', b'"generated_at":""', r.content), r.headers.get("content-type")


def _call(client, method, path, body, headers, repo):
    return client.request(method, path.replace("{repo}", repo), json=body, headers=headers)


def _refused(r) -> bool:
    if r.status_code not in (402, 429):
        return False
    detail = r.json().get("detail")
    return isinstance(detail, dict) and detail.get("reason") == home_access.REASON


def _all(client, daemon, repo, *, refused: bool = False):
    """Call every protected route; the event stream only where a refusal is expected (admitted, it streams)."""
    out = []
    for method, path, body in SESSION_ROUTES:
        if path.endswith("/events") and not refused:
            continue
        out.append(((method, path), _call(client, method, path, body, {}, repo)))
    for method, path, body in DAEMON_ROUTES:
        out.append(((method, path), _call(client, method, path, body, daemon, repo)))
    return out


def test_flag_off_changes_no_response_through_the_real_routers():
    """Off, an account long past its trial gets exactly what it got with no gate in the code at all."""
    client, account, daemon, repo = _setup()
    routes = [(m, p, b, {}) for m, p, b in SESSION_ROUTES] + [(m, p, b, daemon) for m, p, b in DAEMON_ROUTES]
    reads = [(m, p, b, h) for m, p, b, h in routes if m == "GET" and not p.endswith("/events")]
    gated = {(m, p): _bytes(r) for m, p, b, h in reads
             for r in [_call(client, m, p, b, h, repo)]}
    client.app.dependency_overrides[home_access.session_gate] = lambda: None
    client.app.dependency_overrides[home_access.daemon_gate] = require_daemon
    bare = {(m, p): _bytes(r) for m, p, b, h in reads
            for r in [_call(client, m, p, b, h, repo)]}
    assert gated == bare  # byte for byte, but for generated_at
    # Writes change state, so each runs once on twin setups: same status, same JSON shape.
    twin = _setup()
    twin[0].app.dependency_overrides[home_access.session_gate] = lambda: None
    twin[0].app.dependency_overrides[home_access.daemon_gate] = require_daemon
    for m, p, b, h in routes:
        if m == "GET":
            continue
        a = _call(client, m, p, b, h, repo)
        z = _call(twin[0], m, p, b, twin[2] if h else {}, twin[3])
        assert a.status_code == z.status_code, (m, p, a.text, z.text)
        assert not _refused(a)
        if a.headers.get("content-type", "").startswith("application/json"):
            ja, jz = a.json(), z.json()
            assert type(ja) is type(jz)
            if isinstance(ja, dict):
                assert set(ja) == set(jz), (m, p)
    assert client.get("/v1/accounts/home-trial").json()["enforcement_enabled"] is False


def test_unparseable_or_future_switch_on_stays_off():
    for value in ["soon", _iso(NOW + timedelta(days=1))]:
        client, account, daemon, repo = _setup(gate_from=value)
        assert not any(_refused(r) for _, r in _all(client, daemon, repo)), value
        assert client.get("/v1/accounts/home-trial").json()["enforcement_enabled"] is False


def test_on_refuses_every_protected_operation_and_names_account():
    client, account, daemon, repo = _setup(gate_from=_iso(NOW - timedelta(days=15)))
    for (method, path), r in _all(client, daemon, repo, refused=True):
        assert _refused(r), (method, path, r.status_code, r.text)
        detail = r.json()["detail"]
        assert detail["account_url"].endswith("/account")
        assert "/account" in detail["message"]
        if path.startswith("/v1/daemons/"):
            assert r.status_code == 429 and int(r.headers["Retry-After"]) == client.app.state.settings.home_gate_retry_after_s
        else:
            assert r.status_code == 402
    trial = client.get("/v1/accounts/home-trial").json()
    assert trial["status"] == "expired" and trial["enforcement_enabled"] is True


def test_on_keeps_account_controls_billing_deletion_and_connecting_open():
    client, account, daemon, repo = _setup(gate_from=_iso(NOW - timedelta(days=15)))
    for method, path, body in OPEN_SESSION:
        r = _call(client, method, path, body, {}, repo)
        assert not _refused(r), (path, r.status_code, r.text)
    for method, path, body in OPEN_DAEMON:
        r = _call(client, method, path, body, daemon, repo)
        assert r.status_code == 200, (path, r.text)
    # A new machine can still connect (pairing starts no second clock), and deletion works.
    _, second_daemon, _ = brnrd_repo_and_daemon(client)
    assert client.post("/v1/daemons/revoke", headers=second_daemon).status_code == 200
    r = client.post("/v1/accounts/delete", headers=account, json={"confirm": "delete"})
    assert not _refused(r) and r.status_code < 500, r.text


@pytest.mark.parametrize("status,price", [
    ("active", "price_unknown_old_7usd"),   # an old supporter price nothing configures any more
    ("past_due", "price_unknown_old_7usd"),
    ("active", "price_founder_own"),
])
def test_on_any_live_subscription_keeps_everything(status, price):
    client, account, daemon, repo = _setup(gate_from=_iso(NOW - timedelta(days=15)))
    with client.app.state.SessionLocal() as db:
        account_id = db.execute(select(Account.id)).scalar_one()
        billing.handle_stripe_event(db, client.app.state.settings, {
            "id": f"evt_{status}_{price}", "type": "customer.subscription.created",
            "data": {"object": {
                "id": "sub_legacy", "status": status, "customer": "cus_legacy", "cancel_at_period_end": False,
                "current_period_end": 2000000000, "metadata": {"brnrd_account_id": account_id},
                "items": {"data": [{"price": {"id": price, "recurring": {"interval": "month"}}}]},
            }},
        })
        db.commit()
        assert db.get(Account, account_id).tier == Account.TIER_SUBSCRIBED
    assert not any(_refused(r) for _, r in _all(client, daemon, repo))


def test_canceled_subscription_past_its_window_is_refused():
    client, account, daemon, repo = _setup(gate_from=_iso(NOW - timedelta(days=15)), tier=Account.TIER_SUBSCRIBED)
    with client.app.state.SessionLocal() as db:
        row = db.execute(select(Account)).scalar_one()
        row.tier = Account.TIER_FREE  # what _refresh_tier writes once no live row remains
        db.commit()
    assert _refused(client.get("/v1/dashboard/loom"))


def test_clock_started_before_switch_on_gets_a_fresh_window_never_a_day_one_cutoff():
    # Started 30 days ago; switched on yesterday: 13 days left, counted from the switch-on.
    gate = NOW - timedelta(days=1)
    client, account, daemon, repo = _setup(gate_from=_iso(gate), started=NOW - timedelta(days=30))
    assert not any(_refused(r) for _, r in _all(client, daemon, repo))
    trial = client.get("/v1/accounts/home-trial").json()
    assert trial["status"] == "active" and trial["enforcement_enabled"] is True
    assert datetime.fromisoformat(trial["ends_at"].replace("Z", "+00:00")) == gate + timedelta(days=14)
    # A clock started after the switch-on keeps its own 14 days.
    client, *_ = _setup(gate_from=_iso(NOW - timedelta(days=20)), started=NOW - timedelta(days=3))
    ends = datetime.fromisoformat(client.get("/v1/accounts/home-trial").json()["ends_at"].replace("Z", "+00:00"))
    assert abs(ends - (NOW + timedelta(days=11))) < timedelta(seconds=5)


def test_never_connected_account_is_not_refused():
    client, account, daemon, repo = _setup(gate_from=_iso(NOW - timedelta(days=15)), started=None)
    # The first successful connect starts the clock inside the admitted request.
    assert client.post("/v1/daemons/register", headers=daemon, json={"daemon_name": "c"}).status_code == 200
    assert client.get("/v1/accounts/home-trial").json()["status"] == "active"
    assert not any(_refused(r) for _, r in _all(client, daemon, repo))


def test_decide_is_the_one_decision():
    settings = brnrd_client().app.state.settings
    off = home_access.decide(None, settings, "acc_any")  # no database touched while off
    assert off.allowed and off.why == "gate_off"


def test_billing_read_error_admits():
    client, account, daemon, repo = _setup(gate_from=_iso(NOW - timedelta(days=15)))

    class Broken:
        def get(self, *a, **k):
            raise RuntimeError("billing down")

    assert home_access.decide(Broken(), client.app.state.settings, "acc_x").why == "degraded"


def test_device_refusal_is_held_before_it_answers():
    """An old client retries a refusal with no backoff on its 3 s publish tick; the hold paces it."""
    client = brnrd_client(home_gate_from=_iso(NOW - timedelta(days=15)), home_gate_hold_s=0.3, inbox_long_poll_max_s=0.6)
    account, daemon, repo = brnrd_repo_and_daemon(client)
    with client.app.state.SessionLocal() as db:
        db.execute(select(Account)).scalar_one().home_trial_started_at = (NOW - timedelta(days=30)).replace(tzinfo=None)
        db.commit()
    began = time.monotonic()
    assert _refused(client.put("/v1/daemons/activity", headers=daemon, json={"records": []}))
    assert time.monotonic() - began >= 0.3
    began = time.monotonic()
    assert _refused(client.get("/v1/daemons/inbox?wait=25", headers=daemon))
    assert time.monotonic() - began >= 0.6  # the long-poll's own timeout


def test_held_refusals_have_a_ceiling_past_which_they_answer_at_once():
    import asyncio
    import httpx

    client = brnrd_client(home_gate_from=_iso(NOW - timedelta(days=15)), home_gate_hold_s=3.0,
                          home_gate_max_holds=2)
    account, daemon, repo = brnrd_repo_and_daemon(client)
    with client.app.state.SessionLocal() as db:
        db.execute(select(Account)).scalar_one().home_trial_started_at = (NOW - timedelta(days=30)).replace(tzinfo=None)
        db.commit()

    async def one(http):
        began = time.monotonic()
        r = await http.put("/v1/daemons/activity", headers=daemon, json={"records": []})
        return _refused(r), time.monotonic() - began

    async def burst():
        transport = httpx.ASGITransport(app=client.app)
        async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as http:
            return await asyncio.gather(*(one(http) for _ in range(5)))

    results = asyncio.run(burst())
    assert all(refused for refused, _ in results), results
    took = sorted(t for _, t in results)
    assert sum(t >= 3.0 for t in took) == 2  # the ceiling's worth held
    assert all(t < 2.0 for t in took[:3]), took  # the rest answered at once
    assert home_access._holds_open == 0

"""The home gate (brnrd.home_access): always on, one decision, refusals through the real routers."""
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
from _helpers import brnrd_client, brnrd_repo_and_daemon

NOW = datetime.now(timezone.utc)


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
    ("POST", "/v1/loom/consent", {"send": True}),
    ("POST", "/v1/loom/letters", {"body": "hello"}),
    ("POST", "/v1/dashboard/loom/letters", {"body": "hello"}),
    ("POST", "/v1/dashboard/runners/wake-request", {}),
    ("POST", "/v1/dashboard/runs/run_x/respawn", {}),
    ("POST", "/v1/dashboard/telegram-pair", None),
    ("POST", "/v1/dashboard/pair", {"platform": "telegram"}),
    ("POST", "/v1/repos/{repo}/telegram-pair", None),
]
DAEMON_ROUTES = [
    ("POST", "/v1/daemons/register", {"daemon_name": "computer"}),
    ("GET", "/v1/daemons/events/evt_x/attachments/0", None),
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
    ("POST", "/v1/daemons/config-requests", {}),
    ("POST", "/v1/daemons/publishing-credential", {}),
]
# Account controls, billing, deletion: open to everyone, gate on or off.
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
    ("GET", "/v1/daemons/whoami", None),
    ("GET", "/v1/daemons/chats", None),
]


def _setup(started: datetime | None = NOW - timedelta(days=30), tier: str = Account.TIER_FREE, **kw):
    client = brnrd_client(home_gate_hold_s=0, inbox_long_poll_max_s=0, **kw)
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


def test_inside_its_window_changes_no_response_through_the_real_routers():
    """An account inside its 14 days gets exactly what it got with no gate in the code at all."""
    client, account, daemon, repo = _setup(started=NOW - timedelta(days=1))
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
    twin = _setup(started=NOW - timedelta(days=1))
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
    trial = client.get("/v1/accounts/home-trial").json()
    assert trial["status"] == "active" and trial["enforcement_enabled"] is True


def test_on_refuses_every_protected_operation_and_names_account():
    client, account, daemon, repo = _setup()
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
    assert not trial["household"] and not trial["subscribed"]


def test_on_keeps_account_controls_billing_and_deletion_open():
    client, account, daemon, repo = _setup()
    for method, path, body in OPEN_SESSION:
        r = _call(client, method, path, body, {}, repo)
        assert not _refused(r), (path, r.status_code, r.text)
    for method, path, body in OPEN_DAEMON:
        r = _call(client, method, path, body, daemon, repo)
        assert r.status_code == 200, (path, r.text)
    assert client.post("/v1/daemons/revoke", headers=daemon).status_code == 200
    r = client.post("/v1/accounts/delete", headers=account, json={"confirm": "delete"})
    assert not _refused(r) and r.status_code < 500, r.text


@pytest.mark.parametrize("kind", ["legacy", "loom"])
def test_expired_account_cannot_approve_or_pick_up_a_device_credential(kind):
    client, _, _, repo = _setup(started=None)
    capabilities = {"kind": "loom", "board": True} if kind == "loom" else {}
    pair = client.post("/v1/accounts/pair", json=capabilities).json()
    reply = client.post(f"/v1/connect/{pair['pair_code']}", json={
        "repo_id": repo, "approve_secret": pair["approve_secret"],
    })
    assert reply.status_code == 200, reply.text

    # The window ends after one approval, before the machine claims it.
    with client.app.state.SessionLocal() as db:
        db.execute(select(Account)).scalar_one().home_trial_started_at = (NOW - timedelta(days=15)).replace(tzinfo=None)
        db.commit()
    poll = client.get(f"/v1/accounts/pair/{pair['pair_code']}", params={"poll_secret": pair["poll_secret"]})
    assert poll.status_code == 402 and _refused(poll)
    assert "daemon_token" not in poll.json()

    new_pair = client.post("/v1/accounts/pair", json=capabilities).json()
    reply = client.post(f"/v1/connect/{new_pair['pair_code']}", json={
        "repo_id": repo, "approve_secret": new_pair["approve_secret"],
    })
    assert reply.status_code == 402 and _refused(reply)
    pending = client.get(f"/v1/accounts/pair/{new_pair['pair_code']}", params={"poll_secret": new_pair["poll_secret"]})
    assert pending.json()["status"] == "pending"


@pytest.mark.parametrize("status,price", [
    ("active", "price_unknown_old_7usd"),   # an old supporter price nothing configures any more
    ("past_due", "price_unknown_old_7usd"),
    ("active", "price_founder_own"),
])
def test_on_any_live_subscription_keeps_everything(status, price):
    client, account, daemon, repo = _setup()
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
    client, account, daemon, repo = _setup(tier=Account.TIER_SUBSCRIBED)
    with client.app.state.SessionLocal() as db:
        row = db.execute(select(Account)).scalar_one()
        row.tier = Account.TIER_FREE  # what _refresh_tier writes once no live row remains
        db.commit()
    assert _refused(client.get("/v1/dashboard/loom"))


def test_never_connected_account_is_not_refused():
    client, account, daemon, repo = _setup(started=None)
    # The first successful connect starts the clock inside the admitted request.
    assert client.post("/v1/daemons/register", headers=daemon, json={"daemon_name": "computer"}).status_code == 200
    assert client.get("/v1/accounts/home-trial").json()["status"] == "active"
    assert not any(_refused(r) for _, r in _all(client, daemon, repo))


def test_billing_read_error_admits():
    client, account, daemon, repo = _setup()

    class Broken:
        def get(self, *a, **k):
            raise RuntimeError("billing down")

    assert home_access.decide(Broken(), client.app.state.settings, "acc_x").why == "degraded"


def test_device_refusal_is_held_before_it_answers():
    """An old client retries a refusal with no backoff on its 3 s publish tick; the hold paces it."""
    client = brnrd_client(home_gate_hold_s=0.3, inbox_long_poll_max_s=0.6)
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


def test_held_refusals_have_a_ceiling_past_which_they_answer_at_once(tmp_path):
    import asyncio
    import httpx

    # Concurrent worker threads need independent connections and transactions.
    client = brnrd_client(
        database_url=f"sqlite:///{tmp_path / 'home-access.db'}",
        home_gate_hold_s=3.0,
        home_gate_max_holds=2,
    )
    account, daemon, repo = brnrd_repo_and_daemon(client)
    with client.app.state.SessionLocal() as db:
        db.execute(select(Account)).scalar_one().home_trial_started_at = (NOW - timedelta(days=30)).replace(tzinfo=None)
        db.commit()

    async def one(http):
        began = time.monotonic()
        r = await http.put("/v1/daemons/activity", headers=daemon, json={"records": []})
        return r.status_code, _refused(r), time.monotonic() - began

    async def burst():
        transport = httpx.ASGITransport(app=client.app)
        async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as http:
            return await asyncio.gather(*(one(http) for _ in range(5)))

    results = asyncio.run(burst())
    assert all(refused for _, refused, _ in results), results
    took = sorted(t for _, _, t in results)
    assert sum(t >= 3.0 for t in took) == 2  # the ceiling's worth held
    assert all(t < 2.0 for t in took[:3]), took  # the rest answered at once
    assert home_access._holds_open == 0


def test_named_login_is_admitted_without_a_subscription():
    # The household (w-e080rjxmdya4): past its window, no subscription, named by login.
    client, account, daemon, repo = _setup(home_gate_admit_github_logins=("someone", "octocat"))
    assert not any(_refused(r) for _, r in _all(client, daemon, repo))
    with client.app.state.SessionLocal() as db:
        row = db.execute(select(Account)).scalar_one()
        assert home_access.decide(db, client.app.state.settings, row).why == "named"
        # Login case does not matter: GitHub logins are case-insensitive.
        row.github_login = "OctoCat"
        db.commit()
        assert home_access.decide(db, client.app.state.settings, row.id).why == "named"


def test_named_list_is_empty_by_default_and_names_only_its_logins():
    assert brnrd_client().app.state.settings.home_gate_admit_github_logins == ()
    client, account, daemon, repo = _setup(home_gate_admit_github_logins=("octocat-other",))
    assert all(_refused(r) for _, r in _all(client, daemon, repo, refused=True))

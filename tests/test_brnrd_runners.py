"""Tests for the runner-catalog mirror (#328 spool rack): daemon publish
endpoint + dashboard JSON twin. Mirrors ``tests/test_brnrd_quota.py``'s shape.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402

from brnrd import create_app  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.oauth import GitHubIdentity  # noqa: E402
from brnrd.routers.accounts import account_for_github_identity, issue_session_token  # noqa: E402
from _helpers import PUBLISH_EVERYTHING, brnrd_account_headers  # noqa: E402


def _client() -> TestClient:
    app = create_app(
        Settings(
            database_url="sqlite:///:memory:",
            public_base_url="https://brnrd.example",
            github_oauth_client_id="gh-client",
            github_oauth_client_secret="gh-secret",
        )
    )
    return TestClient(app, base_url="https://testserver")


def _repo_and_daemon(client: TestClient) -> tuple[dict[str, str], dict[str, str], str]:
    account_headers = brnrd_account_headers(
        client.app, github_id="123", login="octocat", email="a@b.com",
    )
    repo = client.post(
        "/v1/accounts/repos",
        json={"repo_full_name": "Gurio/brr", "default_branch": "main", "publish_layers": PUBLISH_EVERYTHING},
        headers=account_headers,
    ).json()
    pair = client.post("/v1/accounts/pair").json()
    client.post(
        f"/v1/accounts/pair/{pair['pair_code']}/approve",
        json={"repo_id": repo["repo_id"], "approve_secret": pair["approve_secret"]},
        headers=account_headers,
    )
    paired = client.get(
        f"/v1/accounts/pair/{pair['pair_code']}",
        params={"poll_secret": pair["poll_secret"]},
    ).json()
    daemon_headers = {"Authorization": f"Bearer {paired['daemon_token']}"}
    return account_headers, daemon_headers, repo["repo_id"]


def _login_cookie(client: TestClient) -> None:
    with client.app.state.SessionLocal() as db:
        account = account_for_github_identity(
            db, GitHubIdentity(github_id="123", login="octocat", email="a@b.com")
        )
        token = issue_session_token(db, account)
    client.cookies.set("brnrd_session", token)


_CATALOG_PAYLOAD = {
    "default": "claude-fable",
    "environment_default": "worktree",
    "environments": [
        {"name": "worktree", "available": True},
        {"name": "docker", "available": False, "reason": "docker.image is not configured"},
        {"name": "solitary", "available": False, "reason": "docker.image is not configured"},
    ],
    "profiles": [
        {
            "name": "claude-haiku",
            "shell": "claude",
            "model": "claude-haiku-4-5-20251001",
            "class": "economy",
            "cost_rank": 10,
            "quota_source": "claude-local",
        },
        {
            "name": "claude-fable",
            "shell": "claude",
            "model": "claude-fable-5",
            "class": "economy",
            "cost_rank": 15,
            "quota_source": "claude-local",
            "selected": True,
        },
        {
            "name": "codex",
            "shell": "codex",
            "class": "balanced",
            "cost_rank": 25,
            "quota_source": "codex-local",
        },
    ],
}


def test_daemon_runners_snapshot_replaces_catalog():
    client = _client()
    _, daemon_headers, _repo_id = _repo_and_daemon(client)
    assert client.post(
        "/v1/daemons/register", json={"daemon_name": "laptop"}, headers=daemon_headers,
    ).status_code == 200

    posted = client.put("/v1/daemons/runners", json=_CATALOG_PAYLOAD, headers=daemon_headers)
    assert posted.status_code == 200, posted.text
    body = posted.json()
    assert body["default"] == "claude-fable"
    # `class` survives the pydantic alias round-trip on the wire.
    assert body["profiles"][0]["class"] == "economy"
    assert body["runners_updated_at"] is not None
    assert body["environment_default"] == "worktree"
    assert body["environments"][1]["available"] is False

    _login_cookie(client)
    repo = client.get("/v1/dashboard/repos").json()["connected_repos"][0]
    assert repo["repo_full_name"] == "Gurio/brr"
    assert repo["dispatch_default"] is True
    assert repo["environment_default"] == "worktree"
    assert repo["environments"][1]["reason"] == "docker.image is not configured"

    # Last-write-wins, same shape as the quota/plans mirrors.
    replaced = client.put(
        "/v1/daemons/runners",
        json={"default": None, "profiles": [{"name": "codex", "shell": "codex"}]},
        headers=daemon_headers,
    )
    assert replaced.status_code == 200
    assert replaced.json()["default"] is None
    assert [p["name"] for p in replaced.json()["profiles"]] == ["codex"]


def test_dashboard_runners_api_serves_merged_catalog():
    client = _client()
    _, daemon_headers, _repo_id = _repo_and_daemon(client)
    assert client.post(
        "/v1/daemons/register", json={"daemon_name": "laptop"}, headers=daemon_headers,
    ).status_code == 200
    assert client.put(
        "/v1/daemons/runners", json=_CATALOG_PAYLOAD, headers=daemon_headers,
    ).status_code == 200

    # Unauthenticated fetch: JSON 401, not a redirect.
    anon = client.get("/v1/dashboard/runners")
    assert anon.status_code == 401

    _login_cookie(client)
    res = client.get("/v1/dashboard/runners")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["default"] == "claude-fable"
    assert body["stale"] is False
    assert body["reported_at"] is not None
    # Sorted cheapest-first by cost_rank.
    assert [p["name"] for p in body["profiles"]] == ["claude-haiku", "claude-fable", "codex"]
    assert body["profiles"][1]["selected"] is True
    # No tap parked yet (#328 tap-to-request).
    assert body["wake_request"] is None


# ── #328 tap-to-request ──────────────────────────────────────────────────


def test_wake_request_tap_cancel_lifecycle():
    client = _client()
    _, daemon_headers, _repo_id = _repo_and_daemon(client)
    assert client.post(
        "/v1/daemons/register", json={"daemon_name": "laptop"}, headers=daemon_headers,
    ).status_code == 200

    # Unauthenticated tap: 401.
    assert client.post(
        "/v1/dashboard/runners/wake-request", json={"profile": "codex"},
    ).status_code == 401

    _login_cookie(client)
    # Empty profile: 422.
    assert client.post(
        "/v1/dashboard/runners/wake-request", json={"profile": ""},
    ).status_code == 422

    tapped = client.post(
        "/v1/dashboard/runners/wake-request",
        json={
            "profile": "codex-mini",
            "repo_label": "Gurio/brr",
            "environment": "worktree",
        },
    )
    assert tapped.status_code == 200, tapped.text
    wake = tapped.json()["wake_request"]
    assert wake["profile"] == "codex-mini"
    assert wake["repo_label"] == "Gurio/brr"
    assert wake["environment"] == "worktree"
    assert wake["status"] == "pending"

    # The dashboard view carries the pending tap.
    body = client.get("/v1/dashboard/runners").json()
    assert body["wake_request"]["request_id"] == wake["request_id"]

    # A second tap supersedes the first rather than queueing.
    retapped = client.post(
        "/v1/dashboard/runners/wake-request", json={"profile": "claude-haiku"},
    ).json()["wake_request"]
    assert retapped["request_id"] != wake["request_id"]
    body = client.get("/v1/dashboard/runners").json()
    assert body["wake_request"]["profile"] == "claude-haiku"

    # Cancel clears the chip.
    canceled = client.delete(
        f"/v1/dashboard/runners/wake-request/{retapped['request_id']}",
    )
    assert canceled.status_code == 200
    assert canceled.json()["wake_request"]["status"] == "canceled"
    assert client.get("/v1/dashboard/runners").json()["wake_request"] is None

    # Unknown id: 404.
    assert client.delete(
        "/v1/dashboard/runners/wake-request/wake_nope",
    ).status_code == 404


def test_wake_request_rejects_unknown_repo_and_environment():
    client = _client()
    _repo_and_daemon(client)
    _login_cookie(client)

    bad_repo = client.post(
        "/v1/dashboard/runners/wake-request",
        json={"profile": "codex", "repo_label": "other/missing"},
    )
    assert bad_repo.status_code == 422
    assert "repo_label" in bad_repo.json()["detail"]

    bad_env = client.post(
        "/v1/dashboard/runners/wake-request",
        json={"profile": "codex", "environment": "moon"},
    )
    assert bad_env.status_code == 422
    assert "environment" in bad_env.json()["detail"]


# ── #733 claim at dispatch ───────────────────────────────────────────────
#
# The server owns the tap's whole lifecycle. These pin the guard ladder that
# used to run — twice, divergently — on the daemon.

_CLAIM = "/v1/daemons/runners/wake-request/claim"


def _park(client: TestClient, daemon_headers: dict[str, str], profile: str = "codex") -> dict:
    """Register + publish the rack, then tap `profile` from the dashboard."""
    assert client.post(
        "/v1/daemons/register", json={"daemon_name": "laptop"}, headers=daemon_headers,
    ).status_code == 200
    posted = client.put("/v1/daemons/runners", json=_CATALOG_PAYLOAD, headers=daemon_headers)
    assert posted.status_code == 200
    # No tap yet: the publish response piggybacks nothing.
    assert posted.json()["pending_wake_request"] is None
    _login_cookie(client)
    wake = client.post(
        "/v1/dashboard/runners/wake-request", json={"profile": profile},
    ).json()["wake_request"]
    # Next publish tick: the daemon learns a tap exists.
    pending = client.put(
        "/v1/daemons/runners", json=_CATALOG_PAYLOAD, headers=daemon_headers,
    ).json()["pending_wake_request"]
    assert pending is not None and pending["request_id"] == wake["request_id"]
    return wake


def test_wake_request_claim_applies_and_retires_the_row_in_one_transaction():
    client = _client()
    _, daemon_headers, _repo_id = _repo_and_daemon(client)
    wake = _park(client, daemon_headers)

    claimed = client.post(
        _CLAIM,
        json={"request_id": wake["request_id"], "event_id": "evt-1", "source": "telegram"},
        headers=daemon_headers,
    )
    assert claimed.status_code == 200, claimed.text
    verdict = claimed.json()
    assert verdict["apply"] is True
    assert verdict["reason"] is None
    assert verdict["profile"] == "codex"
    # The row is already at its final status in the answer — no ack round trip.
    assert verdict["status"] == "consumed"

    # The chip is gone and the publish tick stops offering it.
    assert client.get("/v1/dashboard/runners").json()["wake_request"] is None
    assert client.put(
        "/v1/daemons/runners", json=_CATALOG_PAYLOAD, headers=daemon_headers,
    ).json()["pending_wake_request"] is None

    # A second claim of the same id — a daemon whose mirror is one publish
    # tick stale — is refused rather than double-spent. This is the only
    # staleness left in the system, and it is harmless.
    again = client.post(
        _CLAIM, json={"request_id": wake["request_id"], "source": "telegram"},
        headers=daemon_headers,
    ).json()
    assert again["apply"] is False
    assert again["reason"] == "the tap is already consumed"

    # Cancel-after-consume stays truthful: the wake fired.
    canceled = client.delete(
        f"/v1/dashboard/runners/wake-request/{wake['request_id']}",
    )
    assert canceled.json()["wake_request"]["status"] == "consumed"


def test_wake_request_claim_expires_a_lapsed_tap_never_consumes_it():
    """#733's core failure: a lapsed tap that reported as spent.

    `expired` and `consumed` are different answers to "what happened to my
    tap", and only one of them is true here.
    """
    from datetime import datetime, timedelta, timezone

    from brnrd.models import RunnerWakeRequest

    client = _client()
    _, daemon_headers, _repo_id = _repo_and_daemon(client)
    wake = _park(client, daemon_headers)

    with client.app.state.SessionLocal() as db:
        row = db.get(RunnerWakeRequest, wake["request_id"])
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()

    verdict = client.post(
        _CLAIM, json={"request_id": wake["request_id"], "source": "telegram"},
        headers=daemon_headers,
    ).json()
    assert verdict["apply"] is False
    assert verdict["status"] == "expired"
    assert "expired" in verdict["reason"]

    with client.app.state.SessionLocal() as db:
        assert db.get(RunnerWakeRequest, wake["request_id"]).status == "expired"


def test_wake_request_claim_refuses_a_schedule_wake_and_keeps_the_tap_armed():
    """#564: a scheduled firing is never the interactive wake a tap was
    parked for — and a refusal must not burn the row either."""
    client = _client()
    _, daemon_headers, _repo_id = _repo_and_daemon(client)
    wake = _park(client, daemon_headers)

    verdict = client.post(
        _CLAIM, json={"request_id": wake["request_id"], "source": "schedule"},
        headers=daemon_headers,
    ).json()
    assert verdict["apply"] is False
    assert "schedule" in verdict["reason"]
    assert verdict["status"] == "pending"

    # Still offered on the next publish tick, and still claimable by a real
    # interactive wake.
    assert client.put(
        "/v1/daemons/runners", json=_CATALOG_PAYLOAD, headers=daemon_headers,
    ).json()["pending_wake_request"]["request_id"] == wake["request_id"]
    assert client.post(
        _CLAIM, json={"request_id": wake["request_id"], "source": "telegram"},
        headers=daemon_headers,
    ).json()["apply"] is True


def test_wake_request_claim_refuses_a_profile_missing_from_the_published_rack():
    """A drop is not a spend: the row stays pending for a rack that has it."""
    from brnrd.models import RunnerWakeRequest

    client = _client()
    _, daemon_headers, _repo_id = _repo_and_daemon(client)
    wake = _park(client, daemon_headers)

    # The daemon republishes a rack that no longer carries `codex`.
    shrunk = dict(_CATALOG_PAYLOAD)
    shrunk["profiles"] = [
        p for p in _CATALOG_PAYLOAD["profiles"] if p["name"] != "codex"
    ]
    client.put("/v1/daemons/runners", json=shrunk, headers=daemon_headers)

    verdict = client.post(
        _CLAIM, json={"request_id": wake["request_id"], "source": "telegram"},
        headers=daemon_headers,
    ).json()
    assert verdict["apply"] is False
    assert "codex" in verdict["reason"]
    assert verdict["status"] == "pending"
    with client.app.state.SessionLocal() as db:
        assert db.get(RunnerWakeRequest, wake["request_id"]).status == "pending"


def test_wake_request_claim_judges_the_tap_against_when_the_event_existed():
    """#577's one surviving rule. The mirror lags its source by up to a
    publish tick, so a tap minted seconds ago may not be on the daemon's
    disk yet — but a tap parked *after* the event existed was meant for the
    next wake anyway. The lag and the rule are the same rule."""
    from datetime import datetime, timedelta, timezone

    client = _client()
    _, daemon_headers, _repo_id = _repo_and_daemon(client)
    wake = _park(client, daemon_headers)
    now = datetime.now(timezone.utc)

    # Event created *before* the tap was parked ⇒ the tap is for a later wake.
    verdict = client.post(
        _CLAIM,
        json={
            "request_id": wake["request_id"],
            "source": "telegram",
            "event_created": (now - timedelta(minutes=5)).isoformat(),
            "daemon_now": now.isoformat(),
        },
        headers=daemon_headers,
    ).json()
    assert verdict["apply"] is False
    assert "parked after" in verdict["reason"]
    assert verdict["status"] == "pending"

    # Event created *after* the tap was parked ⇒ this is the wake it meant,
    # however long ago that was: the server's 24h TTL is the only horizon.
    verdict = client.post(
        _CLAIM,
        json={
            "request_id": wake["request_id"],
            "source": "telegram",
            "event_created": (now + timedelta(minutes=5)).isoformat(),
            "daemon_now": now.isoformat(),
        },
        headers=daemon_headers,
    ).json()
    assert verdict["apply"] is True


def test_wake_request_claim_survives_a_daemon_clock_behind_the_server():
    """The rung compares two *ages*, each measured inside one machine.

    Comparing the absolute stamps instead — server ``created_at`` against a
    daemon-stamped ``event_created`` — is skew-sensitive in the one direction
    that costs taps. A daemon clock 90s behind makes a tap parked a minute
    before the event look parked *after* it, and because a refusal leaves the
    row pending, the same skew refuses again on the next wake and the one
    after: not "deferred once" but never applied, which is exactly the
    invisible expiry #733 exists to end.
    """
    from datetime import datetime, timedelta, timezone

    from brnrd.models import RunnerWakeRequest

    client = _client()
    _, daemon_headers, _repo_id = _repo_and_daemon(client)
    wake = _park(client, daemon_headers)
    server_now = datetime.now(timezone.utc)
    with client.app.state.SessionLocal() as db:
        row = db.get(RunnerWakeRequest, wake["request_id"])
        row.created_at = server_now - timedelta(seconds=60)
        db.commit()

    # This daemon's clock runs 90s behind the server's. On its own clock the
    # event is 30s old — comfortably younger than the 60s-old tap.
    daemon_now = server_now - timedelta(seconds=90)
    verdict = client.post(
        _CLAIM,
        json={
            "request_id": wake["request_id"],
            "source": "telegram",
            "event_created": (daemon_now - timedelta(seconds=30)).isoformat(),
            "daemon_now": daemon_now.isoformat(),
        },
        headers=daemon_headers,
    ).json()
    assert verdict["apply"] is True, verdict.get("reason")


def test_wake_request_claim_skips_the_age_rung_without_a_daemon_clock():
    """An older daemon sends no ``daemon_now``. Skip the rung rather than
    guess at it — this module never silently drops a tap, and a guess here
    would be a second opinion in the one place #733 removed them from."""
    from datetime import datetime, timedelta, timezone

    client = _client()
    _, daemon_headers, _repo_id = _repo_and_daemon(client)
    wake = _park(client, daemon_headers)
    verdict = client.post(
        _CLAIM,
        json={
            "request_id": wake["request_id"],
            "source": "telegram",
            "event_created": (
                datetime.now(timezone.utc) - timedelta(minutes=5)
            ).isoformat(),
        },
        headers=daemon_headers,
    ).json()
    assert verdict["apply"] is True


def test_wake_request_claim_unknown_id_and_unregistered_daemon():
    client = _client()
    _, daemon_headers, _repo_id = _repo_and_daemon(client)

    # No daemon registered for this token yet.
    assert client.post(
        _CLAIM, json={"request_id": "wake_nope"}, headers=daemon_headers,
    ).status_code == 404

    _park(client, daemon_headers)
    verdict = client.post(
        _CLAIM, json={"request_id": "wake_nope"}, headers=daemon_headers,
    ).json()
    assert verdict["apply"] is False
    assert verdict["reason"] == "no such wake request"
    assert verdict["status"] == "absent"

    # Unauthenticated: the claim is daemon-principal only.
    assert client.post(_CLAIM, json={"request_id": "wake_nope"}).status_code == 401


def test_wake_request_claim_skips_availability_when_the_rack_is_blank():
    """An empty published catalog (never published, or a publish-scope
    denial) must not refuse every tap — that would turn a consent setting
    into a silent wake-tap outage, exactly the invisible failure #733 is
    about."""
    from brnrd.models import Daemon

    client = _client()
    _, daemon_headers, _repo_id = _repo_and_daemon(client)
    wake = _park(client, daemon_headers)

    with client.app.state.SessionLocal() as db:
        row = db.query(Daemon).one()
        row.runners_json = "[]"
        db.commit()

    verdict = client.post(
        _CLAIM, json={"request_id": wake["request_id"], "source": "telegram"},
        headers=daemon_headers,
    ).json()
    assert verdict["apply"] is True


# ── #932 conversation-sticky mirror + the exit tap (2026-08-08) ──────────


_STICKY = {
    "profile": "claude-haiku",
    "persistent": False,
    "claimed_at": "2026-08-08T10:00:00+00:00",
    # Far future: these tests pin mirroring and lifecycle, not the clock.
    "expires_at": "2126-08-08T12:00:00+00:00",
    "correspondent_key": "telegram:user-id:1",
    "request_id": "wake_abc",
}


def test_sticky_rides_the_runners_mirror_to_the_dashboard():
    client = _client()
    _, daemon_headers, _repo_id = _repo_and_daemon(client)
    assert client.post(
        "/v1/daemons/register", json={"daemon_name": "laptop"}, headers=daemon_headers,
    ).status_code == 200
    assert client.put(
        "/v1/daemons/runners",
        json={**_CATALOG_PAYLOAD, "sticky": _STICKY},
        headers=daemon_headers,
    ).status_code == 200

    _login_cookie(client)
    body = client.get("/v1/dashboard/runners").json()
    assert body["sticky"]["profile"] == "claude-haiku"
    assert body["sticky"]["persistent"] is False
    assert body["sticky"]["expires_at"].startswith("2126-08-08T12:00:00")

    # A sticky past its expiry never renders, even off a stale mirror.
    assert client.put(
        "/v1/daemons/runners",
        json={**_CATALOG_PAYLOAD, "sticky": {**_STICKY, "expires_at": "2020-01-01T00:00:00+00:00"}},
        headers=daemon_headers,
    ).status_code == 200
    late = client.get("/v1/dashboard/runners").json()
    assert late["sticky"] is None

    # The explicit regime flag outranks a legacy expiry stamp.
    assert client.put(
        "/v1/daemons/runners",
        json={**_CATALOG_PAYLOAD, "sticky": {**_STICKY, "persistent": True, "expires_at": "2020-01-01T00:00:00+00:00"}},
        headers=daemon_headers,
    ).status_code == 200
    persistent = client.get("/v1/dashboard/runners").json()
    assert persistent["sticky"]["persistent"] is True

    # A publish with no sticky clears the mirror.
    assert client.put(
        "/v1/daemons/runners",
        json={**_CATALOG_PAYLOAD, "sticky": _STICKY},
        headers=daemon_headers,
    ).status_code == 200
    assert client.put(
        "/v1/daemons/runners", json=_CATALOG_PAYLOAD, headers=daemon_headers,
    ).status_code == 200
    cleared = client.get("/v1/dashboard/runners").json()
    assert cleared["sticky"] is None


def test_sticky_release_parks_an_ask_and_the_publish_tick_retires_it():
    client = _client()
    _, daemon_headers, _repo_id = _repo_and_daemon(client)
    assert client.post(
        "/v1/daemons/register", json={"daemon_name": "laptop"}, headers=daemon_headers,
    ).status_code == 200
    assert client.put(
        "/v1/daemons/runners",
        json={**_CATALOG_PAYLOAD, "sticky": _STICKY},
        headers=daemon_headers,
    ).status_code == 200

    _login_cookie(client)
    # No sticky ⇒ 404 later; with one in force the ask parks.
    released = client.post("/v1/dashboard/runners/sticky-release")
    assert released.status_code == 200, released.text
    asked_at = released.json()["requested_at"]

    # The ask rides back on the daemon's next catalog publish...
    still_stuck = client.put(
        "/v1/daemons/runners",
        json={**_CATALOG_PAYLOAD, "sticky": _STICKY},
        headers=daemon_headers,
    ).json()
    assert still_stuck["sticky_release_at"] is not None
    assert still_stuck["sticky_release_at"][:19] == asked_at[:19]

    # ...and retires once the daemon reports the sticky gone.
    honoured = client.put(
        "/v1/daemons/runners", json=_CATALOG_PAYLOAD, headers=daemon_headers,
    ).json()
    assert honoured["sticky_release_at"] is None
    after = client.put(
        "/v1/daemons/runners", json=_CATALOG_PAYLOAD, headers=daemon_headers,
    ).json()
    assert after["sticky_release_at"] is None


def test_sticky_release_spares_a_record_claimed_after_the_ask():
    client = _client()
    _, daemon_headers, _repo_id = _repo_and_daemon(client)
    assert client.post(
        "/v1/daemons/register", json={"daemon_name": "laptop"}, headers=daemon_headers,
    ).status_code == 200
    assert client.put(
        "/v1/daemons/runners",
        json={**_CATALOG_PAYLOAD, "sticky": _STICKY},
        headers=daemon_headers,
    ).status_code == 200

    _login_cookie(client)
    assert client.post("/v1/dashboard/runners/sticky-release").status_code == 200

    # A *newer* tap (claimed after the ask) reports in: the ask is obsolete
    # and must clear without touching the new record.
    fresh = {**_STICKY, "claimed_at": "2030-01-01T00:00:00+00:00",
             "expires_at": "2030-01-01T02:00:00+00:00"}
    body = client.put(
        "/v1/daemons/runners",
        json={**_CATALOG_PAYLOAD, "sticky": fresh},
        headers=daemon_headers,
    ).json()
    assert body["sticky_release_at"] is None


def test_sticky_release_without_a_sticky_is_a_named_404():
    client = _client()
    _, daemon_headers, _repo_id = _repo_and_daemon(client)
    assert client.post(
        "/v1/daemons/register", json={"daemon_name": "laptop"}, headers=daemon_headers,
    ).status_code == 200
    assert client.put(
        "/v1/daemons/runners", json=_CATALOG_PAYLOAD, headers=daemon_headers,
    ).status_code == 200
    _login_cookie(client)
    assert client.post("/v1/dashboard/runners/sticky-release").status_code == 404

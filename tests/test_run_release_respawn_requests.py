"""the-parked-seat-has-two-buttons — release / respawn request lifecycle.

Close sibling of ``test_run_stop_requests.py``: a browser parks a
release/respawn row, the daemon picks it up on its next live-runs sync, and
acks it back. The one structural difference throughout: these two act on a
*held* row (``status: "held"``), never a live one — a stop's target is "any
live run the account can see"; these are narrower on purpose.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402

from brnrd import create_app, run_release_requests, run_respawn_requests  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.models import Account, Daemon, RunReleaseRequest, RunRespawnRequest  # noqa: E402
from brnrd.oauth import GitHubIdentity  # noqa: E402
from brnrd.routers.accounts import (  # noqa: E402
    account_for_github_identity,
    issue_session_token,
)


def _client(**overrides) -> TestClient:
    kwargs = dict(
        database_url="sqlite:///:memory:",
        public_base_url="https://brnrd.example",
        github_oauth_client_id="gh-client",
        github_oauth_client_secret="gh-secret",
    )
    kwargs.update(overrides)
    return TestClient(create_app(Settings(**kwargs)), base_url="https://testserver")


def _login(client: TestClient, *, github_id: str = "12345", login: str = "Gurio") -> str:
    with client.app.state.SessionLocal() as db:
        account = account_for_github_identity(
            db, GitHubIdentity(github_id=github_id, login=login, email=None)
        )
        token = issue_session_token(db, account)
    client.cookies.set("brnrd_session", token)
    return token


def _account_id(client: TestClient, login: str = "Gurio") -> str:
    with client.app.state.SessionLocal() as db:
        return db.query(Account).filter(Account.github_login == login).one().id


def _create_repo(client: TestClient, token: str, repo: str = "Gurio/brr") -> str:
    r = client.post(
        "/v1/accounts/repos",
        json={"repo_full_name": repo, "default_branch": "main"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["repo_id"]


def _publish_run(
    client: TestClient, repo_id: str, run_id: str = "run-h", *, status: str | None = None,
    resource_hold: dict | None = None,
) -> None:
    """Seed a live-runs row the way a daemon's publish would — live or held."""
    row = {
        "id": f"pres-{run_id}",
        "kind": "daemon",
        "stream": "telegram:x:",
        "label": "parked" if status == "held" else "burning",
        "run_id": run_id,
        "repo_label": "Gurio/brr",
        "started_at": "2026-07-19T15:00:00Z",
        "last_seen": "2026-07-19T15:05:00Z",
    }
    if status is not None:
        row["status"] = status
    if resource_hold is not None:
        row["resource_hold"] = resource_hold
    with client.app.state.SessionLocal() as db:
        db.add(
            Daemon(
                id=f"dmn-{run_id}",
                repo_id=repo_id,
                token_id=f"tok-{run_id}",
                daemon_name="laptop",
                live_runs_json=json.dumps([row]),
                live_runs_updated_at=datetime.now(timezone.utc),
            )
        )
        db.commit()


def _publish_runner_catalog(client: TestClient, repo_id: str, profiles: list[dict]) -> None:
    with client.app.state.SessionLocal() as db:
        daemon = db.query(Daemon).filter(Daemon.repo_id == repo_id).first()
        daemon.runners_json = json.dumps(profiles)
        daemon.runners_updated_at = datetime.now(timezone.utc)
        db.commit()


_HOLD = {
    "reason": "quota_exhausted", "provider": "anthropic", "resume_condition": "operator",
    "armed_at": "2026-09-08T12:00:00Z", "released": False,
}


# ── release ─────────────────────────────────────────────────────────


def test_release_requires_a_session():
    client = _client()
    r = client.post("/v1/dashboard/runs/run-h/release")
    assert r.status_code == 401


def test_release_parks_a_pending_request_for_a_held_run():
    client = _client()
    token = _login(client)
    repo_id = _create_repo(client, token)
    _publish_run(client, repo_id, status="held", resource_hold=_HOLD)

    r = client.post("/v1/dashboard/runs/run-h/release")
    assert r.status_code == 200, r.text
    row = r.json()["release_request"]
    assert row["run_id"] == "run-h"
    assert row["status"] == "pending"


def test_release_refuses_a_live_run():
    """Release is narrower than stop: a row that isn't published `held` is
    409'd rather than silently treated as a stop."""
    client = _client()
    token = _login(client)
    repo_id = _create_repo(client, token)
    _publish_run(client, repo_id, status=None)

    r = client.post("/v1/dashboard/runs/run-h/release")
    assert r.status_code == 409


def test_release_refuses_a_run_the_account_cannot_see():
    client = _client()
    token = _login(client)
    _create_repo(client, token)

    r = client.post("/v1/dashboard/runs/run-somebody-elses/release")
    assert r.status_code == 404


def test_live_runs_view_marks_the_run_releasing():
    client = _client()
    token = _login(client)
    repo_id = _create_repo(client, token)
    _publish_run(client, repo_id, status="held", resource_hold=_HOLD)

    before = client.get("/v1/dashboard/live-runs").json()["runs"][0]
    assert before["release_requested"] is False
    assert before["status"] == "held"
    assert before["resource_hold"]["reason"] == "quota_exhausted"

    client.post("/v1/dashboard/runs/run-h/release")
    after = client.get("/v1/dashboard/live-runs").json()["runs"][0]
    assert after["release_requested"] is True


# ── respawn ─────────────────────────────────────────────────────────


def test_respawn_requires_a_session():
    client = _client()
    r = client.post("/v1/dashboard/runs/run-h/respawn", json={})
    assert r.status_code == 401


def test_respawn_parks_a_pending_request_with_shell_and_core():
    client = _client()
    token = _login(client)
    repo_id = _create_repo(client, token)
    _publish_run(client, repo_id, status="held", resource_hold=_HOLD)
    _publish_runner_catalog(client, repo_id, [{"name": "claude-opus", "shell": "claude", "core": "opus"}])

    r = client.post(
        "/v1/dashboard/runs/run-h/respawn", json={"shell": "claude", "core": "opus"},
    )
    assert r.status_code == 200, r.text
    row = r.json()["respawn_request"]
    assert row["run_id"] == "run-h"
    assert row["shell"] == "claude"
    assert row["core"] == "opus"
    assert row["status"] == "pending"


def test_respawn_accepts_an_empty_body_as_the_current_runner():
    client = _client()
    token = _login(client)
    repo_id = _create_repo(client, token)
    _publish_run(client, repo_id, status="held", resource_hold=_HOLD)

    r = client.post("/v1/dashboard/runs/run-h/respawn", json={})
    assert r.status_code == 200, r.text
    row = r.json()["respawn_request"]
    assert row["shell"] is None
    assert row["core"] is None


def test_respawn_refuses_a_shell_core_pair_outside_the_catalog():
    client = _client()
    token = _login(client)
    repo_id = _create_repo(client, token)
    _publish_run(client, repo_id, status="held", resource_hold=_HOLD)
    _publish_runner_catalog(client, repo_id, [{"name": "claude-sonnet", "shell": "claude", "core": "sonnet"}])

    r = client.post(
        "/v1/dashboard/runs/run-h/respawn", json={"shell": "codex", "core": "gpt-5.6-sol"},
    )
    assert r.status_code == 422


def test_respawn_refuses_a_live_run():
    client = _client()
    token = _login(client)
    repo_id = _create_repo(client, token)
    _publish_run(client, repo_id, status=None)

    r = client.post("/v1/dashboard/runs/run-h/respawn", json={})
    assert r.status_code == 409


def test_live_runs_view_marks_the_run_respawning():
    client = _client()
    token = _login(client)
    repo_id = _create_repo(client, token)
    _publish_run(client, repo_id, status="held", resource_hold=_HOLD)

    client.post("/v1/dashboard/runs/run-h/respawn", json={})
    after = client.get("/v1/dashboard/live-runs").json()["runs"][0]
    assert after["respawn_requested"] is True


# ── the store ───────────────────────────────────────────────────────


def test_release_pending_expires_lazily_on_read():
    client = _client()
    _login(client)
    account_id = _account_id(client)

    with client.app.state.SessionLocal() as db:
        row = run_release_requests.create(db, account_id, "run-old")
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        assert run_release_requests.pending_for_account(db, account_id) == []
        assert db.get(RunReleaseRequest, row.id).status == "expired"


def test_respawn_second_tap_replaces_the_runner_choice():
    """Unlike a stop/release's plain idempotency, a second respawn tap before
    the daemon's next tick should mean "respawn on Y", not two rows."""
    client = _client()
    _login(client)
    account_id = _account_id(client)

    with client.app.state.SessionLocal() as db:
        first = run_respawn_requests.create(db, account_id, "run-h", shell="claude", core="opus")
        second = run_respawn_requests.create(db, account_id, "run-h", shell="claude", core="sonnet")
        assert first.id == second.id
        assert db.get(RunRespawnRequest, first.id).core == "sonnet"


def test_mark_consumed_is_account_scoped_for_both():
    client = _client()
    _login(client)
    account_id = _account_id(client)

    with client.app.state.SessionLocal() as db:
        rel = run_release_requests.create(db, account_id, "run-a")
        run_release_requests.mark_consumed(db, "acc-someone-else", [rel.id])
        assert db.get(RunReleaseRequest, rel.id).status == "pending"
        run_release_requests.mark_consumed(db, account_id, [rel.id])
        assert db.get(RunReleaseRequest, rel.id).status == "consumed"

        resp = run_respawn_requests.create(db, account_id, "run-a")
        run_respawn_requests.mark_consumed(db, "acc-someone-else", [resp.id])
        assert db.get(RunRespawnRequest, resp.id).status == "pending"
        run_respawn_requests.mark_consumed(db, account_id, [resp.id])
        assert db.get(RunRespawnRequest, resp.id).status == "consumed"


# ── the daemon-sync seam ────────────────────────────────────────────


def _pair_daemon(client: TestClient, token: str, repo_id: str) -> str:
    account_headers = {"Authorization": f"Bearer {token}"}
    pair = client.post("/v1/accounts/pair").json()
    client.post(
        f"/v1/accounts/pair/{pair['pair_code']}/approve",
        json={"repo_id": repo_id, "approve_secret": pair["approve_secret"]},
        headers=account_headers,
    )
    paired = client.get(
        f"/v1/accounts/pair/{pair['pair_code']}",
        params={"poll_secret": pair["poll_secret"]},
    ).json()
    daemon_token = paired["daemon_token"]
    client.post(
        "/v1/daemons/register",
        json={"daemon_name": "laptop"},
        headers={"Authorization": f"Bearer {daemon_token}"},
    )
    return daemon_token


def test_live_runs_publish_hands_down_pending_releases_and_respawns():
    client = _client()
    token = _login(client)
    repo_id = _create_repo(client, token)
    account_id = _account_id(client)
    daemon_token = _pair_daemon(client, token, repo_id)

    with client.app.state.SessionLocal() as db:
        parked_release = run_release_requests.create(db, account_id, "run-h").id
        parked_respawn = run_respawn_requests.create(
            db, account_id, "run-h2", shell="claude", core="opus",
        ).id

    served = client.put(
        "/v1/daemons/live-runs",
        json={
            "runs": [],
            "consumed_run_stop_request_ids": [],
            "consumed_run_release_request_ids": [],
            "consumed_run_respawn_request_ids": [],
        },
        headers={"Authorization": f"Bearer {daemon_token}"},
    )
    assert served.status_code == 200, served.text
    body = served.json()
    assert [row["request_id"] for row in body["pending_run_release_requests"]] == [parked_release]
    assert [row["request_id"] for row in body["pending_run_respawn_requests"]] == [parked_respawn]
    assert body["pending_run_respawn_requests"][0]["shell"] == "claude"

    acked = client.put(
        "/v1/daemons/live-runs",
        json={
            "runs": [],
            "consumed_run_stop_request_ids": [],
            "consumed_run_release_request_ids": [parked_release],
            "consumed_run_respawn_request_ids": [parked_respawn],
        },
        headers={"Authorization": f"Bearer {daemon_token}"},
    )
    assert acked.json()["pending_run_release_requests"] == []
    assert acked.json()["pending_run_respawn_requests"] == []

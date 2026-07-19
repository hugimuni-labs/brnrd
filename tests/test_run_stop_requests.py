"""The user-side stop affordance (#476 wyrd §3) — request lifecycle.

PR #461 shipped the kill; this covers the *button*: a browser parks a stop
row, the daemon picks it up on its next live-runs sync, dispatches it into
the same kill path the ``stop:`` verb uses, and acks it back.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402

from brnrd import create_app, run_stop_requests  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.models import Account, Daemon, RunStopRequest  # noqa: E402
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


def _publish_live_run(client: TestClient, repo_id: str, run_id: str = "run-b") -> None:
    """Seed a live run the way a daemon's live-runs publish would."""
    with client.app.state.SessionLocal() as db:
        db.add(
            Daemon(
                id=f"dmn-{run_id}",
                repo_id=repo_id,
                token_id=f"tok-{run_id}",
                daemon_name="laptop",
                live_runs_json=json.dumps(
                    [
                        {
                            "id": f"pres-{run_id}",
                            "kind": "daemon",
                            "stream": "telegram:x:",
                            "label": "burning",
                            "run_id": run_id,
                            "repo_label": "Gurio/brr",
                            "started_at": "2026-07-19T15:00:00Z",
                            "last_seen": "2026-07-19T15:05:00Z",
                        }
                    ]
                ),
                live_runs_updated_at=datetime.now(timezone.utc),
            )
        )
        db.commit()


# ── the endpoint ────────────────────────────────────────────────────


def test_stop_requires_a_session():
    """Anonymous is 401, same auth shape as every other dashboard write."""
    client = _client()
    r = client.post("/v1/dashboard/runs/run-b/stop")
    assert r.status_code == 401


def test_stop_parks_a_pending_request_for_a_live_run():
    client = _client()
    token = _login(client)
    repo_id = _create_repo(client, token)
    _publish_live_run(client, repo_id)

    r = client.post("/v1/dashboard/runs/run-b/stop")
    assert r.status_code == 200, r.text
    row = r.json()["stop_request"]
    assert row["run_id"] == "run-b"
    assert row["status"] == "pending"


def test_stop_refuses_a_run_the_account_cannot_see():
    """Not authorization theatre: parking a stop for a run this account has
    nothing burning under would sit pending until its TTL and expire silently."""
    client = _client()
    token = _login(client)
    _create_repo(client, token)

    r = client.post("/v1/dashboard/runs/run-somebody-elses/stop")
    assert r.status_code == 404


def test_second_tap_is_idempotent():
    """One kill, one ack. A duplicate row would mean two acks for one stop."""
    client = _client()
    token = _login(client)
    repo_id = _create_repo(client, token)
    _publish_live_run(client, repo_id)

    first = client.post("/v1/dashboard/runs/run-b/stop").json()["stop_request"]
    second = client.post("/v1/dashboard/runs/run-b/stop").json()["stop_request"]
    assert first["request_id"] == second["request_id"]


def test_live_runs_view_marks_the_run_stopping():
    """The cell must say "stopping", not jump to a state the system has not
    reached — and must keep saying it across a reload, so the fact lives on
    the server rather than in the client's memory."""
    client = _client()
    token = _login(client)
    repo_id = _create_repo(client, token)
    _publish_live_run(client, repo_id)

    before = client.get("/v1/dashboard/live-runs").json()["runs"][0]
    assert before["stop_requested"] is False

    client.post("/v1/dashboard/runs/run-b/stop")
    after = client.get("/v1/dashboard/live-runs").json()["runs"][0]
    assert after["stop_requested"] is True


# ── the store ───────────────────────────────────────────────────────


def test_pending_expires_lazily_on_read():
    """A stop names a run burning *now*; a stale one must not land on a much
    later run that happens to reuse the handle."""
    client = _client()
    _login(client)
    account_id = _account_id(client)

    with client.app.state.SessionLocal() as db:
        row = run_stop_requests.create(db, account_id, "run-old")
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        assert run_stop_requests.pending_for_account(db, account_id) == []
        assert db.get(RunStopRequest, row.id).status == "expired"


def test_pending_holds_one_row_per_run():
    """Unlike a wake request (one account, one "next wake"), two runs can burn
    at once and each deserves its own stop."""
    client = _client()
    _login(client)
    account_id = _account_id(client)

    with client.app.state.SessionLocal() as db:
        run_stop_requests.create(db, account_id, "run-a")
        run_stop_requests.create(db, account_id, "run-b")
        pending = run_stop_requests.pending_for_account(db, account_id)
        assert {row.run_id for row in pending} == {"run-a", "run-b"}


def test_mark_consumed_is_account_scoped():
    """A daemon cannot ack another account's stop rows."""
    client = _client()
    _login(client)
    account_id = _account_id(client)

    with client.app.state.SessionLocal() as db:
        row = run_stop_requests.create(db, account_id, "run-a")
        run_stop_requests.mark_consumed(db, "acc-someone-else", [row.id])
        assert db.get(RunStopRequest, row.id).status == "pending"
        run_stop_requests.mark_consumed(db, account_id, [row.id])
        assert db.get(RunStopRequest, row.id).status == "consumed"


# ── the daemon-sync seam ────────────────────────────────────────────


def test_live_runs_publish_hands_down_pending_stops_and_retires_acked():
    """One publish tick carries both halves: the daemon acks what it killed,
    and learns of anything newly parked."""
    client = _client()
    token = _login(client)
    repo_id = _create_repo(client, token)
    account_id = _account_id(client)
    account_headers = {"Authorization": f"Bearer {token}"}

    pair = client.post("/v1/accounts/pair").json()
    client.post(
        f"/v1/accounts/pair/{pair['pair_code']}/approve",
        json={"repo_id": repo_id},
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

    with client.app.state.SessionLocal() as db:
        parked = run_stop_requests.create(db, account_id, "run-b").id

    served = client.put(
        "/v1/daemons/live-runs",
        json={"runs": [], "consumed_run_stop_request_ids": []},
        headers={"Authorization": f"Bearer {daemon_token}"},
    )
    assert served.status_code == 200, served.text
    handed = served.json()["pending_run_stop_requests"]
    assert [row["request_id"] for row in handed] == [parked]
    assert handed[0]["run_id"] == "run-b"

    acked = client.put(
        "/v1/daemons/live-runs",
        json={"runs": [], "consumed_run_stop_request_ids": [parked]},
        headers={"Authorization": f"Bearer {daemon_token}"},
    )
    # Retired before the pending list is recomputed, so a consumed stop is
    # never served twice — a second delivery would kill a second run.
    assert acked.json()["pending_run_stop_requests"] == []

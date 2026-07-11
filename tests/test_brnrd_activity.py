"""Tests for brnrd Activity snapshot API and dashboard view."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from brnrd import create_app  # noqa: E402
from brnrd.activity_records import ACTIVITY_STALE_TTL  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.models import ActivityRecord  # noqa: E402
from brnrd.oauth import GitHubIdentity  # noqa: E402
from brnrd.routers.accounts import account_for_github_identity, issue_session_token  # noqa: E402
from _helpers import brnrd_account_headers  # noqa: E402


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
        json={"repo_full_name": "Gurio/brr", "default_branch": "main"},
        headers=account_headers,
    ).json()
    pair = client.post("/v1/accounts/pair").json()
    client.post(
        f"/v1/accounts/pair/{pair['pair_code']}/approve",
        json={"repo_id": repo["repo_id"]},
        headers=account_headers,
    )
    paired = client.get(
        f"/v1/accounts/pair/{pair['pair_code']}",
        params={"poll_secret": pair["poll_secret"]},
    ).json()
    daemon_headers = {"Authorization": f"Bearer {paired['daemon_token']}"}
    return account_headers, daemon_headers, repo["repo_id"]


def _pair_daemon(client: TestClient, account_headers: dict[str, str], repo_id: str) -> dict[str, str]:
    pair = client.post("/v1/accounts/pair").json()
    approved = client.post(
        f"/v1/accounts/pair/{pair['pair_code']}/approve",
        json={"repo_id": repo_id},
        headers=account_headers,
    )
    assert approved.status_code == 200
    paired = client.get(
        f"/v1/accounts/pair/{pair['pair_code']}",
        params={"poll_secret": pair["poll_secret"]},
    ).json()
    return {"Authorization": f"Bearer {paired['daemon_token']}"}


def _login_cookie(client: TestClient) -> None:
    with client.app.state.SessionLocal() as db:
        account = account_for_github_identity(
            db, GitHubIdentity(github_id="123", login="octocat", email="a@b.com")
        )
        token = issue_session_token(db, account)
    client.cookies.set("brnrd_session", token)


def _backdate_activity(client: TestClient, record_id: str) -> None:
    with client.app.state.SessionLocal() as db:
        row = db.execute(
            select(ActivityRecord).where(ActivityRecord.record_id == record_id)
        ).scalar_one()
        row.reported_at = datetime.now(timezone.utc) - ACTIVITY_STALE_TTL - ACTIVITY_STALE_TTL
        db.commit()


def _activity_record(record_id: str, summary: str, *, status: str = "running") -> dict[str, str]:
    return {
        "id": record_id,
        "kind": "run",
        "summary": summary,
        "status": status,
        "updated_at": "2026-06-29T06:00:00Z",
    }


def test_daemon_activity_snapshot_is_account_readable():
    client = _client()
    account_headers, daemon_headers, repo_id = _repo_and_daemon(client)
    assert client.post(
        "/v1/daemons/register",
        json={"daemon_name": "laptop"},
        headers=daemon_headers,
    ).status_code == 200

    posted = client.put(
        "/v1/daemons/activity",
        json={
            "records": [
                {
                    "id": "run:run-1",
                    "kind": "run",
                    "source": "telegram",
                    "conversation_key": "telegram:42:",
                    "summary": "implement the missing parts",
                    "runner": {"shell": "codex", "core": "gpt-5-codex"},
                    "status": "running",
                    "phase": "coding",
                    "branch": "brr/initial-context-reweave",
                    "updated_at": "2026-06-29T00:00:00Z",
                },
                {
                    "id": "respawn:evt-2",
                    "kind": "respawn",
                    "summary": "rerun on stronger core",
                    "status": "scheduled",
                    "defer_until": "2026-06-29T01:00:00Z",
                },
            ]
        },
        headers=daemon_headers,
    )
    assert posted.status_code == 200, posted.text
    assert len(posted.json()["activity"]) == 2

    listing = client.get("/v1/accounts/activity", headers=account_headers)
    assert listing.status_code == 200
    rows = listing.json()["activity"]
    assert {row["id"] for row in rows} == {"run:run-1", "respawn:evt-2"}
    assert all(row["repo_id"] == repo_id for row in rows)
    assert rows[0]["runner"]["shell"] == "codex"


def test_activity_dashboard_renders_snapshot():
    client = _client()
    _, daemon_headers, _repo_id = _repo_and_daemon(client)
    assert client.post(
        "/v1/daemons/register",
        json={"daemon_name": "laptop"},
        headers=daemon_headers,
    ).status_code == 200
    client.put(
        "/v1/daemons/activity",
        json={
            "records": [
                {
                    "id": "run:run-1",
                    "kind": "run",
                    "source": "telegram",
                    "summary": "implement the missing parts",
                    "runner": {"shell": "codex", "core": "gpt-5-codex"},
                    "status": "running",
                    "phase": "coding",
                    "started_at": "2026-06-29T05:59:00Z",
                    "updated_at": "2026-06-29T06:00:00Z",
                    "branch": "brr/activity",
                },
                {
                    "id": "schedule:daily-sweep",
                    "kind": "scheduled",
                    "source": "schedule",
                    "summary": "run upkeep",
                    "status": "scheduled",
                    "scheduled_for": "2026-06-29T06:00:00Z",
                }
            ]
        },
        headers=daemon_headers,
    )
    _login_cookie(client)

    r = client.get("/v1/dashboard/activity", params={"kind": "run"})

    assert r.status_code == 200
    body = r.json()
    rows = body["rows"]
    assert [row["summary"] for row in rows] == ["implement the missing parts"]
    assert rows[0]["runner"]["summary"] == "codex / gpt-5-codex"
    assert rows[0]["daemon_name"] == "laptop"
    assert rows[0]["bucket"] == "running"
    assert rows[0]["branch"] == "brr/activity"
    # Filter vocab comes from the repo-scoped (kind/status-unfiltered) view:
    # the scheduled record still contributes even though `kind=run` hid it.
    assert "scheduled" in body["kinds"]
    assert "scheduled" in body["statuses"]
    assert body["total"] == 1


def test_dashboard_activity_api_requires_login():
    client = _client()
    r = client.get("/v1/dashboard/activity")
    assert r.status_code == 401


def test_dashboard_activity_api_bounds_rows_and_reports_total():
    """#327: the JSON twin is bounded where the Jinja page it replaces
    rendered every record — `limit` caps rows, `total` keeps the real count.
    """
    client = _client()
    _, daemon_headers, _repo_id = _repo_and_daemon(client)
    assert client.post(
        "/v1/daemons/register",
        json={"daemon_name": "laptop"},
        headers=daemon_headers,
    ).status_code == 200
    client.put(
        "/v1/daemons/activity",
        json={
            "records": [
                {
                    "id": f"run:run-{i}",
                    "kind": "run",
                    "summary": f"job {i}",
                    "status": "completed",
                    "updated_at": f"2026-06-29T06:00:0{i}Z",
                }
                for i in range(3)
            ]
        },
        headers=daemon_headers,
    )
    _login_cookie(client)

    r = client.get("/v1/dashboard/activity", params={"limit": 2})

    assert r.status_code == 200
    body = r.json()
    assert len(body["rows"]) == 2
    assert body["total"] == 3
    # Newest-updated first, same ordering the legacy view used.
    assert [row["summary"] for row in body["rows"]] == ["job 2", "job 1"]


def test_activity_page_redirects_to_dashboard():
    """#327 Jinja cut: the legacy /activity page is gone; the URL stays
    alive as a 308 to "/" (same shape as /plans, #326). In production the
    passthru no longer routes /activity here at all — the SPA serves it.
    """
    client = _client()
    r = client.get("/activity", follow_redirects=False)
    assert r.status_code == 308
    assert r.headers["location"] == "/"


def test_activity_views_collapse_repeat_snapshots_across_daemon_tokens():
    client = _client()
    account_headers, daemon_headers, repo_id = _repo_and_daemon(client)
    assert client.post(
        "/v1/daemons/register",
        json={"daemon_name": "laptop"},
        headers=daemon_headers,
    ).status_code == 200

    snapshot = {
        "records": [
            {
                "id": "run:dup",
                "kind": "run",
                "source": "telegram",
                "summary": "duplicate snapshot",
                "runner": {"shell": "codex", "core": "gpt-5-codex"},
                "status": "completed",
                "phase": "done",
                "updated_at": "2026-06-29T06:00:00Z",
            }
        ]
    }
    assert client.put("/v1/daemons/activity", json=snapshot, headers=daemon_headers).status_code == 200

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
    second_headers = {"Authorization": f"Bearer {paired['daemon_token']}"}
    assert client.post(
        "/v1/daemons/register",
        json={"daemon_name": "backup"},
        headers=second_headers,
    ).status_code == 200
    assert client.put("/v1/daemons/activity", json=snapshot, headers=second_headers).status_code == 200

    listing = client.get("/v1/accounts/activity", headers=account_headers)
    assert listing.status_code == 200
    assert [row["id"] for row in listing.json()["activity"]] == ["run:dup"]

    _login_cookie(client)

    activity_api = client.get("/v1/dashboard/activity", params={"kind": "run"})
    assert activity_api.status_code == 200
    assert [row["summary"] for row in activity_api.json()["rows"]] == ["duplicate snapshot"]

    dashboard_page = client.get("/")
    assert dashboard_page.status_code == 200
    assert dashboard_page.text.count("run.completed") == 1


def test_daemon_activity_put_reaps_stale_rows_from_previous_token():
    client = _client()
    account_headers, daemon_headers, repo_id = _repo_and_daemon(client)
    assert client.post(
        "/v1/daemons/register",
        json={"daemon_name": "laptop"},
        headers=daemon_headers,
    ).status_code == 200
    assert client.put(
        "/v1/daemons/activity",
        json={"records": [_activity_record("run:stale", "stale token row")]},
        headers=daemon_headers,
    ).status_code == 200
    _backdate_activity(client, "run:stale")

    second_headers = _pair_daemon(client, account_headers, repo_id)
    assert client.post(
        "/v1/daemons/register",
        json={"daemon_name": "backup"},
        headers=second_headers,
    ).status_code == 200
    assert client.put(
        "/v1/daemons/activity",
        json={"records": [_activity_record("run:fresh", "fresh token row")]},
        headers=second_headers,
    ).status_code == 200

    with client.app.state.SessionLocal() as db:
        record_ids = set(
            db.execute(
                select(ActivityRecord.record_id).where(ActivityRecord.repo_id == repo_id)
            ).scalars()
        )
    assert record_ids == {"run:fresh"}


def test_activity_reads_hide_stale_rows_and_keep_fresh_rows():
    client = _client()
    account_headers, daemon_headers, repo_id = _repo_and_daemon(client)
    assert client.post(
        "/v1/daemons/register",
        json={"daemon_name": "laptop"},
        headers=daemon_headers,
    ).status_code == 200
    assert client.put(
        "/v1/daemons/activity",
        json={"records": [_activity_record("run:stale", "stale visible row")]},
        headers=daemon_headers,
    ).status_code == 200

    second_headers = _pair_daemon(client, account_headers, repo_id)
    assert client.post(
        "/v1/daemons/register",
        json={"daemon_name": "backup"},
        headers=second_headers,
    ).status_code == 200
    assert client.put(
        "/v1/daemons/activity",
        json={"records": [_activity_record("run:fresh", "fresh visible row")]},
        headers=second_headers,
    ).status_code == 200
    _backdate_activity(client, "run:stale")

    listing = client.get("/v1/accounts/activity", headers=account_headers)
    assert listing.status_code == 200
    assert {row["id"] for row in listing.json()["activity"]} == {"run:fresh"}

    _login_cookie(client)
    dashboard_api = client.get("/v1/dashboard/activity", params={"repo_id": repo_id})
    assert dashboard_api.status_code == 200
    assert {row["id"] for row in dashboard_api.json()["rows"]} == {"run:fresh"}


def test_daemon_activity_put_keeps_fresh_rows_from_other_tokens():
    client = _client()
    account_headers, daemon_headers, repo_id = _repo_and_daemon(client)
    assert client.post(
        "/v1/daemons/register",
        json={"daemon_name": "laptop"},
        headers=daemon_headers,
    ).status_code == 200
    assert client.put(
        "/v1/daemons/activity",
        json={"records": [_activity_record("run:laptop", "laptop row")]},
        headers=daemon_headers,
    ).status_code == 200

    second_headers = _pair_daemon(client, account_headers, repo_id)
    assert client.post(
        "/v1/daemons/register",
        json={"daemon_name": "backup"},
        headers=second_headers,
    ).status_code == 200
    assert client.put(
        "/v1/daemons/activity",
        json={"records": [_activity_record("run:backup", "backup row")]},
        headers=second_headers,
    ).status_code == 200

    with client.app.state.SessionLocal() as db:
        record_ids = set(
            db.execute(
                select(ActivityRecord.record_id).where(ActivityRecord.repo_id == repo_id)
            ).scalars()
        )
    assert record_ids == {"run:laptop", "run:backup"}

"""Tests for brnrd Activity snapshot API and dashboard view."""

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


def _login_cookie(client: TestClient) -> None:
    with client.app.state.SessionLocal() as db:
        account = account_for_github_identity(
            db, GitHubIdentity(github_id="123", login="octocat", email="a@b.com")
        )
        token = issue_session_token(db, account)
    client.cookies.set("brnrd_session", token)


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
    client.put(
        "/v1/daemons/activity",
        json={
            "records": [
                {
                    "id": "schedule:daily-sweep",
                    "kind": "scheduled",
                    "summary": "run upkeep",
                    "status": "scheduled",
                    "scheduled_for": "2026-06-29T06:00:00Z",
                }
            ]
        },
        headers=daemon_headers,
    )
    _login_cookie(client)

    page = client.get("/activity")

    assert page.status_code == 200
    assert "Activity" in page.text
    assert "run upkeep" in page.text
    assert "scheduled" in page.text

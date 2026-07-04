"""Tests for the CPS (Current Planned State) plan/ledger mirror + dashboard view."""

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


def test_daemon_plans_snapshot_replaces_repo_and_account_fields():
    client = _client()
    _, daemon_headers, _repo_id = _repo_and_daemon(client)
    assert client.post(
        "/v1/daemons/register",
        json={"daemon_name": "laptop"},
        headers=daemon_headers,
    ).status_code == 200

    posted = client.put(
        "/v1/daemons/plans",
        json={
            "repo_plan_md": "# active plan\n\nship the CPS view",
            "cross_repo_plan_md": "# cross-repo\n\ncoordinate release",
            "decision_ledger_md": "## decisions\n\n- shipped X",
        },
        headers=daemon_headers,
    )
    assert posted.status_code == 200, posted.text
    body = posted.json()
    assert body["repo_plan_md"] == "# active plan\n\nship the CPS view"
    assert body["cross_repo_plan_md"] == "# cross-repo\n\ncoordinate release"
    assert body["decision_ledger_md"] == "## decisions\n\n- shipped X"
    assert body["plans_updated_at"] is not None

    # Republishing overwrites rather than accumulating (last-write-wins).
    replaced = client.put(
        "/v1/daemons/plans",
        json={"repo_plan_md": "# active plan\n\nrevised", "cross_repo_plan_md": "", "decision_ledger_md": ""},
        headers=daemon_headers,
    )
    assert replaced.status_code == 200
    assert replaced.json()["repo_plan_md"] == "# active plan\n\nrevised"
    assert replaced.json()["cross_repo_plan_md"] == ""


def test_plans_dashboard_renders_mirrored_snapshot():
    client = _client()
    _, daemon_headers, _repo_id = _repo_and_daemon(client)
    assert client.post(
        "/v1/daemons/register",
        json={"daemon_name": "laptop"},
        headers=daemon_headers,
    ).status_code == 200
    client.put(
        "/v1/daemons/plans",
        json={
            "repo_plan_md": "Ship the CPS view plain, skin later.",
            "cross_repo_plan_md": "Coordinate the brr/brnrd naming rollout.",
            "decision_ledger_md": "Accepted the ToS disclaimer posture.",
        },
        headers=daemon_headers,
    )
    _login_cookie(client)

    page = client.get("/plans")

    assert page.status_code == 200
    assert "Ship the CPS view plain, skin later." in page.text
    assert "Coordinate the brr/brnrd naming rollout." in page.text
    assert "Accepted the ToS disclaimer posture." in page.text
    assert "Gurio/brr" in page.text


def test_plans_dashboard_empty_state_when_nothing_mirrored():
    client = _client()
    _repo_and_daemon(client)
    _login_cookie(client)

    page = client.get("/plans")

    assert page.status_code == 200
    assert "No plans mirrored yet." in page.text


def test_plans_dashboard_requires_login():
    client = _client()
    page = client.get("/plans", follow_redirects=False)
    assert page.status_code == 303
    assert page.headers["location"].startswith("/login")

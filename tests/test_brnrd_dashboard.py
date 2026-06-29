"""Tests for the brnrd dashboard repo view."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402

from brnrd import create_app  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.models import Repo  # noqa: E402
from brnrd.oauth import GitHubIdentity  # noqa: E402
from brnrd.routers.accounts import account_for_github_identity, issue_session_token  # noqa: E402


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


def _login(client: TestClient, *, github_id: str = "12345", login: str = "Gurio") -> str:
    with client.app.state.SessionLocal() as db:
        account = account_for_github_identity(
            db, GitHubIdentity(github_id=github_id, login=login, email=None)
        )
        token = issue_session_token(db, account)
    client.cookies.set("brnrd_session", token)
    return token


def _create_repo(client: TestClient, token: str, repo: str = "Gurio/brr") -> str:
    r = client.post(
        "/v1/accounts/repos",
        json={"repo_full_name": repo, "default_branch": "main"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["repo_id"]


def test_dashboard_shows_enabled_repo():
    client = _client()
    token = _login(client, login="Gurio")
    _create_repo(client, token)

    r = client.get("/")

    assert r.status_code == 200
    assert "Gurio/brr" in r.text
    assert "Waiting for local daemon" in r.text
    assert "/activity" in r.text


def test_dashboard_disconnect_removes_repo():
    client = _client()
    token = _login(client, login="Gurio")
    repo_id = _create_repo(client, token)

    r = client.post(f"/repos/{repo_id}/disconnect", follow_redirects=False)

    assert r.status_code == 303
    with client.app.state.SessionLocal() as db:
        assert db.get(Repo, repo_id) is None

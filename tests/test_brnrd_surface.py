"""Tests for the discovered work-surface mirror and dashboard view."""

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
    app = create_app(Settings(database_url="sqlite:///:memory:", public_base_url="https://brnrd.example", github_oauth_client_id="gh-client", github_oauth_client_secret="gh-secret"))
    return TestClient(app, base_url="https://testserver")


def _repo_and_daemon(client: TestClient) -> tuple[dict[str, str], dict[str, str]]:
    account_headers = brnrd_account_headers(client.app, github_id="123", login="octocat", email="a@b.com")
    repo = client.post("/v1/accounts/repos", json={"repo_full_name": "Gurio/brr", "default_branch": "main"}, headers=account_headers).json()
    pair = client.post("/v1/accounts/pair").json()
    client.post(f"/v1/accounts/pair/{pair['pair_code']}/approve", json={"repo_id": repo["repo_id"]}, headers=account_headers)
    paired = client.get(f"/v1/accounts/pair/{pair['pair_code']}", params={"poll_secret": pair["poll_secret"]}).json()
    return account_headers, {"Authorization": f"Bearer {paired['daemon_token']}"}


def _login_cookie(client: TestClient) -> None:
    with client.app.state.SessionLocal() as db:
        account = account_for_github_identity(db, GitHubIdentity(github_id="123", login="octocat", email="a@b.com"))
        token = issue_session_token(db, account)
    client.cookies.set("brnrd_session", token)


def test_daemon_surface_snapshot_replaces_the_discovered_set():
    client = _client()
    _, daemon_headers = _repo_and_daemon(client)
    posted = client.put("/v1/daemons/surface", json={"files": [
        {"path": "index.md", "markdown": "# Work surface"},
        {"path": "plans/Gurio__brr/active.md", "markdown": "# Ranked moves"},
    ]}, headers=daemon_headers)
    assert posted.status_code == 200, posted.text
    assert [item["path"] for item in posted.json()["files"]] == ["index.md", "plans/Gurio__brr/active.md"]
    assert posted.json()["surface_updated_at"] is not None

    replaced = client.put("/v1/daemons/surface", json={"files": [{"path": "index.md", "markdown": "revised"}]}, headers=daemon_headers)
    assert replaced.status_code == 200
    assert replaced.json()["files"] == [{"path": "index.md", "markdown": "revised"}]


@pytest.mark.parametrize("path", ["../secret.md", "/absolute.md", ".hidden.md"])
def test_daemon_surface_refuses_paths_outside_the_declared_root(path: str):
    client = _client()
    _, daemon_headers = _repo_and_daemon(client)
    response = client.put("/v1/daemons/surface", json={"files": [{"path": path, "markdown": "no"}]}, headers=daemon_headers)
    assert response.status_code == 422


def test_dashboard_surface_returns_the_same_generic_file_set():
    client = _client()
    _, daemon_headers = _repo_and_daemon(client)
    files = [
        {"path": "index.md", "markdown": "[Plan](plans/Gurio__brr/active.md)"},
        {"path": "workflow.md", "markdown": "## Gating\nvisibility over approval"},
    ]
    client.put("/v1/daemons/surface", json={"files": files}, headers=daemon_headers)
    _login_cookie(client)

    response = client.get("/v1/dashboard/surface")

    assert response.status_code == 200
    assert response.json()["files"] == files
    assert response.json()["reported_at"] is not None


def test_dashboard_surface_requires_session():
    assert _client().get("/v1/dashboard/surface").status_code == 401

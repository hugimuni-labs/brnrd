"""Tests for the brnrd dashboard repository-binding view."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from brnrd import create_app, ids  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.models import Account, Project, RepoBinding  # noqa: E402
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


def _login(client: TestClient, *, github_id: str = "12345", login: str = "Gurio") -> Account:
    with client.app.state.SessionLocal() as db:
        account = account_for_github_identity(
            db, GitHubIdentity(github_id=github_id, login=login, email=None)
        )
        token = issue_session_token(db, account)
        db.refresh(account)
    client.cookies.set("brnrd_session", token)
    return account


def _legacy_repo_binding(client: TestClient, *, repo: str = "Gurio/brr") -> str:
    with client.app.state.SessionLocal() as db:
        legacy = Account(
            id=ids.account_id(),
            github_id="legacy-github-id",
            github_login="legacy",
            email=None,
        )
        project = Project(id=ids.project_id(), account_id=legacy.id, name="legacy")
        binding = RepoBinding(
            id=ids.repo_binding_id(),
            installation_id="42",
            repo_full_name=repo,
            account_id=legacy.id,
            project_id=project.id,
        )
        db.add_all([legacy, project, binding])
        db.commit()
        return binding.id


def test_dashboard_shows_recoverable_personal_repo_binding():
    client = _client()
    _login(client, login="Gurio")
    _legacy_repo_binding(client)

    r = client.get("/")

    assert r.status_code == 200
    assert "Gurio/brr" in r.text
    assert "Claim" in r.text
    assert "recoverable" in r.text


def test_dashboard_claims_recoverable_personal_repo_binding():
    client = _client()
    account = _login(client, login="Gurio")
    binding_id = _legacy_repo_binding(client)

    with client.app.state.SessionLocal() as db:
        project = db.execute(
            select(Project).where(Project.account_id == account.id, Project.name == "default")
        ).scalar_one()
        project_id = project.id

    r = client.post(
        f"/bindings/repo/{binding_id}/claim",
        data={"project_id": project_id},
        follow_redirects=False,
    )

    assert r.status_code == 303
    with client.app.state.SessionLocal() as db:
        binding = db.get(RepoBinding, binding_id)
        assert binding is not None
        assert binding.account_id == account.id
        assert binding.project_id == project_id

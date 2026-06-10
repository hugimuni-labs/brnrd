"""Tests for the brnrd_web dashboard (GitHub login + approve page)."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from brnrd import create_app  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.models import Account, Project  # noqa: E402
from brnrd.oauth import GitHubIdentity, OAuthError  # noqa: E402
from _helpers import brnrd_account_headers  # noqa: E402

_EMAIL = "owner@example.com"
_GITHUB_ID = "12345"
_LOGIN = "octocat"


@pytest.fixture()
def client():
    app = create_app(
        Settings(
            database_url="sqlite:///:memory:",
            public_base_url="https://brnrd.example",
            github_oauth_client_id="gh-client",
            github_oauth_client_secret="gh-secret",
            github_oauth_authorize_url="https://github.example/login/oauth/authorize",
            github_oauth_token_url="https://github.example/login/oauth/access_token",
            github_api_base_url="https://api.github.example",
        )
    )
    # brnrd is served over HTTPS in production (public_base_url is https),
    # so the session/OAuth cookies carry the Secure flag. Model that here
    # so a Secure cookie round-trips back to the app on follow-up requests.
    return TestClient(app, base_url="https://testserver")


def _account_and_project(client):
    headers = brnrd_account_headers(
        client.app, github_id=_GITHUB_ID, login=_LOGIN, email=_EMAIL
    )
    project_id = client.post(
        "/v1/accounts/projects", json={"name": "laptop"}, headers=headers
    ).json()["project_id"]
    return project_id


def _oauth_start(client, *, next="/"):
    return client.get(
        f"/auth/github/start?next={next}", follow_redirects=False
    )


def _login_web(
    client,
    monkeypatch,
    *,
    next="/",
    identity=GitHubIdentity(github_id=_GITHUB_ID, login=_LOGIN, email=_EMAIL),
):
    seen: dict[str, str] = {}

    def fake_resolve(settings, *, code, redirect_uri, code_verifier):
        seen["code"] = code
        seen["redirect_uri"] = redirect_uri
        seen["code_verifier"] = code_verifier
        return identity

    monkeypatch.setattr("brnrd_web.routes.oauth.resolve_identity", fake_resolve)
    start = _oauth_start(client, next=next)
    location = urlparse(start.headers["location"])
    query = parse_qs(location.query)
    state = query["state"][0]
    callback = client.get(
        f"/auth/github/callback?code=ok&state={state}", follow_redirects=False
    )
    return start, callback, seen


def test_login_page_uses_github_only(client):
    r = client.get("/login?next=/connect/BR-123")
    assert r.status_code == 200
    assert '<meta name="viewport"' in r.text
    assert "/static/brnrd_web/app.css" in r.text
    assert "managed brr control plane" in r.text
    assert "preview-frame" in r.text
    assert "Sign in with GitHub" in r.text
    assert "password" not in r.text.lower()


def test_web_static_assets_are_served(client):
    r = client.get("/static/brnrd_web/app.css")
    assert r.status_code == 200
    assert "text/css" in r.headers["content-type"]
    assert ".auth-shell" in r.text


def test_github_login_redirect_uses_state_and_pkce(client):
    r = _oauth_start(client, next="/connect/BR-123")
    assert r.status_code == 303
    location = urlparse(r.headers["location"])
    query = parse_qs(location.query)
    assert location.scheme == "https"
    assert location.netloc == "github.example"
    assert query["client_id"] == ["gh-client"]
    assert query["redirect_uri"] == ["https://brnrd.example/auth/github/callback"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["scope"] == ["user:email"]
    assert query["state"][0]
    assert query["code_challenge"][0]


def test_github_callback_sets_session_cookie_and_seeds_default_project(
    client, monkeypatch
):
    _, callback, seen = _login_web(client, monkeypatch, next="/connect/BR-123")
    assert callback.status_code == 303
    assert callback.headers["location"] == "/connect/BR-123"
    assert "brnrd_session" in callback.cookies or "brnrd_session" in client.cookies
    assert seen["code"] == "ok"
    assert seen["redirect_uri"] == "https://brnrd.example/auth/github/callback"
    assert seen["code_verifier"]

    with client.app.state.SessionLocal() as db:
        account = db.execute(
            select(Account).where(Account.github_id == _GITHUB_ID)
        ).scalar_one()
        assert account.github_login == _LOGIN
        assert account.email == _EMAIL
        projects = db.execute(
            select(Project).where(Project.account_id == account.id)
        ).scalars().all()
        assert [p.name for p in projects] == ["default"]


def test_github_login_is_not_the_identity_key(client):
    brnrd_account_headers(
        client.app, github_id="1", login="octocat", email="one@example.com"
    )
    brnrd_account_headers(
        client.app, github_id="2", login="octocat", email="two@example.com"
    )
    with client.app.state.SessionLocal() as db:
        accounts = db.execute(
            select(Account).where(Account.github_login == "octocat")
        ).scalars().all()
    assert {account.github_id for account in accounts} == {"1", "2"}


def test_github_callback_rejects_state_mismatch(client, monkeypatch):
    monkeypatch.setattr(
        "brnrd_web.routes.oauth.resolve_identity",
        lambda *a, **k: GitHubIdentity(github_id=_GITHUB_ID, login=_LOGIN),
    )
    _oauth_start(client)
    r = client.get(
        "/auth/github/callback?code=ok&state=wrong", follow_redirects=False
    )
    assert r.status_code == 400


def test_github_callback_surfaces_provider_failure(client, monkeypatch):
    def fail(*_args, **_kwargs):
        raise OAuthError("provider down")

    monkeypatch.setattr("brnrd_web.routes.oauth.resolve_identity", fail)
    start = _oauth_start(client)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    r = client.get(
        f"/auth/github/callback?code=ok&state={state}", follow_redirects=False
    )
    assert r.status_code == 502
    assert "provider down" in r.text


def test_connect_page_requires_login(client):
    _account_and_project(client)
    pair = client.post("/v1/accounts/pair").json()
    r = client.get(f"/connect/{pair['pair_code']}", follow_redirects=False)
    assert r.status_code == 303
    assert "/login" in r.headers["location"]


def test_connect_page_lists_projects(client, monkeypatch):
    _account_and_project(client)
    _login_web(client, monkeypatch)
    pair = client.post("/v1/accounts/pair").json()
    r = client.get(f"/connect/{pair['pair_code']}")
    assert r.status_code == 200
    assert "laptop" in r.text
    assert pair["pair_code"] in r.text


def test_approve_makes_poll_return_token(client, monkeypatch):
    project_id = _account_and_project(client)
    _login_web(client, monkeypatch)
    pair = client.post("/v1/accounts/pair").json()

    approve = client.post(
        f"/connect/{pair['pair_code']}",
        data={"project_id": project_id},
        follow_redirects=False,
    )
    assert approve.status_code == 200
    assert "Approved" in approve.text

    # The CLI's poll now returns the freshly minted daemon token.
    polled = client.get(
        f"/v1/accounts/pair/{pair['pair_code']}",
        params={"poll_secret": pair["poll_secret"]},
    ).json()
    assert polled["status"] == "paired"
    assert polled["daemon_token"]
    assert polled["project_id"] == project_id

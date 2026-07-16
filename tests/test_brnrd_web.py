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
from brnrd.models import Account, Repo, TgPairCode  # noqa: E402
from brnrd.oauth import GitHubIdentity, OAuthError  # noqa: E402
from _helpers import brnrd_account_headers  # noqa: E402

_EMAIL = "owner@example.com"
_GITHUB_ID = "12345"
_LOGIN = "octocat"


def _make_client(**settings_overrides):
    kwargs = dict(
        database_url="sqlite:///:memory:",
        public_base_url="https://brnrd.example",
        github_oauth_client_id="gh-client",
        github_oauth_client_secret="gh-secret",
        github_oauth_authorize_url="https://github.example/login/oauth/authorize",
        github_oauth_token_url="https://github.example/login/oauth/access_token",
        github_api_base_url="https://api.github.example",
    )
    kwargs.update(settings_overrides)
    app = create_app(
        Settings(**kwargs)
    )
    # brnrd is served over HTTPS in production (public_base_url is https),
    # so the session/OAuth cookies carry the Secure flag. Model that here
    # so a Secure cookie round-trips back to the app on follow-up requests.
    return TestClient(app, base_url="https://testserver")


@pytest.fixture()
def client():
    return _make_client()


def _account_and_repo(client):
    headers = brnrd_account_headers(
        client.app, github_id=_GITHUB_ID, login=_LOGIN, email=_EMAIL
    )
    repo_id = client.post(
        "/v1/accounts/repos",
        json={"repo_full_name": "Gurio/laptop"},
        headers=headers,
    ).json()["repo_id"]
    return repo_id


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

    monkeypatch.setattr("brnrd.routers.web_auth.oauth.resolve_identity", fake_resolve)
    start = _oauth_start(client, next=next)
    location = urlparse(start.headers["location"])
    query = parse_qs(location.query)
    state = query["state"][0]
    callback = client.get(
        f"/auth/github/callback?code=ok&state={state}", follow_redirects=False
    )
    return start, callback, seen


def test_login_context_carries_backend_validated_next(client):
    """#327 /login slice: the SPA renders the OAuth start URL the backend
    hands back — `_safe_next` stays server-owned, exactly like the retired
    Jinja page."""
    r = client.get("/v1/dashboard/login-context?next=/connect/BR-123")
    assert r.status_code == 200
    body = r.json()
    assert body["authenticated"] is False
    assert body["oauth_ready"] is True
    assert body["signin_url"] == "/auth/github/start?next=/connect/BR-123"
    assert body["next"] == "/connect/BR-123"

    hostile = client.get("/v1/dashboard/login-context?next=//evil.example")
    assert hostile.json()["signin_url"] == "/auth/github/start?next=/"


def test_login_context_reports_authenticated_session(client, monkeypatch):
    _login_web(client, monkeypatch)
    r = client.get("/v1/dashboard/login-context?next=/repos")
    body = r.json()
    assert body["authenticated"] is True
    assert body["next"] == "/repos"


def test_login_page_is_spa_owned(client):
    # The SPA serves /login in production (passthru removed); the backend
    # route only survives for bare uvicorn, same 308 shape as /repos.
    r = client.get("/login", follow_redirects=False)
    assert r.status_code == 308
    assert r.headers["location"] == "/"


def _message_page(client, monkeypatch):
    """A guaranteed non-dashboard Jinja render (message.html via the
    oauth-unready path) — the probe the retired /login page used to be for
    the two regression tests below."""
    monkeypatch.setattr("brnrd.routers.web_auth._github_oauth_ready", lambda _request: False)
    r = client.get("/auth/github/start")
    assert r.status_code == 503
    return r


def test_non_dashboard_pages_do_not_load_the_legacy_dashboard_stylesheet(client, monkeypatch):
    """Live-caught 2026-07-09 (screenshot from the user): the then-Jinja
    /login and /terms pages rendered a green GitHub-identity card and button
    against the amber brand palette PR #301 (2026-07-08) shipped in app.css.
    Root cause was not a caching regression (the cache-busting fix below
    already covers that) but a cascade bug: base.html loaded dashboard.css
    unconditionally on every page, and dashboard.css — the legacy mint/teal
    control-deck sheet for the plans/activity dashboards — defines unscoped
    `.eyebrow`/`.button`/`.button-primary` rules that, loaded after app.css
    with identical specificity, always won the cascade and clobbered the
    amber values on every non-dashboard page. Fixed by only linking
    dashboard.css when body_class is 'dashboard-page'."""
    r = _message_page(client, monkeypatch)
    assert "dashboard.css" not in r.text


def test_static_asset_urls_carry_a_real_cache_busting_version(client, monkeypatch):
    """Live-caught 2026-07-08: base.html's `?v={{ asset_version }}` was never
    wired to a value, so every deploy served the identical `app.css?v=` URL —
    Cloudflare kept serving pre-fix (green) CSS bytes under that stable cache
    key for its full max-age after the brand-palette fix (PR #301) had
    already shipped and deployed. A non-empty version tied to file content
    means a real static-asset change always mints a new URL."""
    r = _message_page(client, monkeypatch)
    assert "app.css?v=" in r.text
    assert "app.css?v=\"" not in r.text
    assert "dashboard.css?v=\"" not in r.text


def test_logout_clears_session_cookie_and_redirects_to_login(client, monkeypatch):
    """Named directly as a real gap (2026-07-08): no way to end a browser
    session short of clearing cookies by hand."""
    _start, callback, _seen = _login_web(client, monkeypatch)
    session_cookie_name = client.app.state.settings.session_cookie
    assert client.cookies.get(session_cookie_name)

    r = client.get("/logout", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
    assert client.cookies.get(session_cookie_name) is None


def test_web_static_assets_are_served(client):
    r = client.get("/static/brnrd_web/app.css")
    assert r.status_code == 200
    assert "text/css" in r.headers["content-type"]
    assert ".state-shell" in r.text
    assert ".panel::after" in r.text
    assert ".auth-shell" not in r.text


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


def test_terms_status_is_public_for_anonymous_users(client):
    r = client.get("/v1/dashboard/terms-status")
    assert r.status_code == 200
    assert r.json() == {
        "authenticated": False,
        "needs_accept": False,
        "terms_version": "2026-07-08",
        "accepted_at": None,
    }


def test_terms_status_reports_authenticated_acceptance_state(client, monkeypatch):
    _login_web(client, monkeypatch, next="/connect/BR-123")
    r = client.get("/v1/dashboard/terms-status")
    assert r.status_code == 200
    assert r.json() == {
        "authenticated": True,
        "needs_accept": True,
        "terms_version": "2026-07-08",
        "accepted_at": None,
    }


def test_github_callback_requires_terms_acceptance_without_seed_repo(
    client, monkeypatch
):
    _, callback, seen = _login_web(client, monkeypatch, next="/connect/BR-123")
    assert callback.status_code == 303
    assert callback.headers["location"] == "/terms?next=/connect/BR-123"
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
        assert account.hosted_terms_accepted_at is None
        assert account.hosted_terms_version == ""
        repos = db.execute(
            select(Repo).where(Repo.account_id == account.id)
        ).scalars().all()
        assert repos == []


def test_terms_acceptance_requires_session(client):
    r = client.post("/v1/terms/accept", json={"accept_terms": "yes"})
    assert r.status_code == 401
    assert r.json() == {"detail": "unauthenticated"}


def test_terms_acceptance_requires_checkbox(client, monkeypatch):
    _login_web(client, monkeypatch, next="/connect/BR-123")
    r = client.post(
        "/v1/terms/accept",
        json={},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert r.json() == {
        "ok": False,
        "notice": "You need to accept the beta hosted-execution terms before continuing.",
    }
    with client.app.state.SessionLocal() as db:
        account = db.execute(
            select(Account).where(Account.github_id == _GITHUB_ID)
        ).scalar_one()
        assert account.hosted_terms_accepted_at is None


def test_terms_acceptance_records_account_and_redirects(client, monkeypatch):
    _login_web(client, monkeypatch, next="/connect/BR-123")
    r = client.post(
        "/v1/terms/accept",
        json={"accept_terms": "yes"},
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    with client.app.state.SessionLocal() as db:
        account = db.execute(
            select(Account).where(Account.github_id == _GITHUB_ID)
        ).scalar_one()
        assert account.hosted_terms_accepted_at is not None
        assert account.hosted_terms_version == "2026-07-08"
    status = client.get("/v1/dashboard/terms-status")
    assert status.status_code == 200
    assert status.json()["authenticated"] is True
    assert status.json()["needs_accept"] is False
    assert status.json()["accepted_at"] is not None


def test_terms_acceptance_shim_redirects_to_spa(client):
    r = client.get("/terms/accept?next=/connect/BR-123", follow_redirects=False)
    assert r.status_code == 308
    assert r.headers["location"] == "/terms?next=/connect/BR-123"


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
        "brnrd.routers.web_auth.oauth.resolve_identity",
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

    monkeypatch.setattr("brnrd.routers.web_auth.oauth.resolve_identity", fail)
    start = _oauth_start(client)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    r = client.get(
        f"/auth/github/callback?code=ok&state={state}", follow_redirects=False
    )
    assert r.status_code == 502
    assert "provider down" in r.text


def test_connect_page_requires_login(client):
    _account_and_repo(client)
    pair = client.post("/v1/accounts/pair").json()
    r = client.get(f"/connect/{pair['pair_code']}", follow_redirects=False)
    assert r.status_code == 303
    assert "/login" in r.headers["location"]


def test_connect_page_lists_repos(client, monkeypatch):
    _account_and_repo(client)
    _login_web(client, monkeypatch)
    pair = client.post("/v1/accounts/pair").json()
    r = client.get(f"/connect/{pair['pair_code']}")
    assert r.status_code == 200
    assert "flow-lockup" in r.text
    assert "pairing handshake" in r.text
    assert "laptop" in r.text
    assert pair["pair_code"] in r.text


def test_approve_makes_poll_return_token(client, monkeypatch):
    repo_id = _account_and_repo(client)
    _login_web(client, monkeypatch)
    pair = client.post("/v1/accounts/pair").json()

    approve = client.post(
        f"/connect/{pair['pair_code']}",
        data={"repo_id": repo_id},
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
    assert polled["repo_id"] == repo_id
    assert polled["telegram_pair"]["pair_code"].startswith("TG-")
    assert f"/start {polled['telegram_pair']['pair_code']}" in polled["telegram_pair"]["instructions"]


def test_approve_offers_telegram_pair_link(monkeypatch):
    client = _make_client(telegram_bot_username="@brnrd_bot")
    repo_id = _account_and_repo(client)
    _login_web(client, monkeypatch)
    pair = client.post("/v1/accounts/pair").json()

    approve = client.post(
        f"/connect/{pair['pair_code']}",
        data={"repo_id": repo_id},
        follow_redirects=False,
    )
    assert approve.status_code == 200
    assert "Your daemon is connected" in approve.text
    assert "https://t.me/brnrd_bot?start=TG-" in approve.text
    assert "Open Telegram and press Start" in approve.text

    polled = client.get(
        f"/v1/accounts/pair/{pair['pair_code']}",
        params={"poll_secret": pair["poll_secret"]},
    ).json()

    with client.app.state.SessionLocal() as db:
        tg_pair = db.execute(select(TgPairCode)).scalar_one()
        assert tg_pair.repo_id == repo_id
        assert polled["telegram_pair"]["pair_code"] == tg_pair.code
        assert polled["telegram_pair"]["deep_link"] == f"https://t.me/brnrd_bot?start={tg_pair.code}"

"""Tests for brnrd's GitHub OAuth provider adapter."""

from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from brnrd import oauth  # noqa: E402
from brnrd.config import Settings  # noqa: E402


class _Response:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


def _settings() -> Settings:
    return Settings(
        github_oauth_client_id="gh-client",
        github_oauth_client_secret="gh-secret",
        github_oauth_authorize_url="https://github.example/login/oauth/authorize",
        github_oauth_token_url="https://github.example/login/oauth/access_token",
        github_api_base_url="https://api.github.example",
    )


def test_authorize_url_uses_pkce_state_and_callback():
    verifier, challenge = oauth.new_pkce_pair()
    assert verifier
    assert challenge
    url = oauth.authorize_url(
        _settings(),
        state="state123",
        redirect_uri="https://brnrd.example/auth/github/callback",
        code_challenge=challenge,
    )
    assert url.startswith("https://github.example/login/oauth/authorize?")
    assert "client_id=gh-client" in url
    assert "state=state123" in url
    assert "code_challenge_method=S256" in url
    assert f"code_challenge={challenge}" in url
    # w-57 (2026-08-16): brnrd stopped requesting `user:email` — the
    # default scope is empty, so no `scope` param is sent at all.
    assert "scope=" not in url


def test_exchange_code_requests_json_access_token(monkeypatch):
    seen = {}

    def fake_post(url, *, data, headers, timeout):
        seen["url"] = url
        seen["data"] = data
        seen["headers"] = headers
        seen["timeout"] = timeout
        return _Response({"access_token": "ghu_token", "token_type": "bearer"})

    monkeypatch.setattr(oauth.httpx, "post", fake_post)
    token = oauth.exchange_code(
        _settings(),
        code="code123",
        redirect_uri="https://brnrd.example/auth/github/callback",
        code_verifier="verifier123",
    )
    assert token == "ghu_token"
    assert seen["url"] == "https://github.example/login/oauth/access_token"
    assert seen["data"]["client_secret"] == "gh-secret"
    assert seen["data"]["code_verifier"] == "verifier123"
    assert seen["headers"]["Accept"] == "application/json"


def test_fetch_identity_never_collects_email(monkeypatch):
    """w-57 (2026-08-16): no `/user/emails` fallback, and `/user`'s own
    `email` field (present when a user has a public profile email, even
    with no scope requested) is ignored too — the decision was to stop
    collecting a login email outright, not merely to narrow the scope.
    """
    calls = []

    def fake_get(url, *, headers, timeout):
        calls.append((url, headers))
        if url.endswith("/user"):
            # A public-profile email would appear here with no scope at
            # all; fetch_identity must not read it.
            return _Response({"id": 123, "login": "octocat", "email": "octo@example.com"})
        raise AssertionError(url)

    monkeypatch.setattr(oauth.httpx, "get", fake_get)
    identity = oauth.fetch_identity(_settings(), "ghu_token")
    assert identity == oauth.GitHubIdentity(github_id="123", login="octocat")
    assert identity.email is None
    assert identity.access_token == "ghu_token"
    # Exactly one call — no `/user/emails` fallback fired.
    assert [call[0] for call in calls] == ["https://api.github.example/user"]
    assert calls[0][1]["Authorization"] == "Bearer ghu_token"

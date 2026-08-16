"""GitHub OAuth helpers for brnrd account identity.

brnrd uses GitHub as the user-identity provider and keeps its own
high-entropy bearer tokens for API/session/daemon authorization. This
module contains only the provider-facing pieces of the OAuth web flow.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass, field as dataclass_field
from urllib.parse import urlencode

import httpx

from .config import Settings


class OAuthError(RuntimeError):
    """Provider-side OAuth failure safe to surface as login failure."""


@dataclass(frozen=True)
class GitHubIdentity:
    github_id: str
    login: str
    # w-57 (2026-08-16): always ``None`` now. brnrd stopped requesting
    # `user:email` and stopped reading GitHub's own `email` field off
    # `/user` — see fetch_identity(). The field stays on this dataclass
    # (rather than being deleted) only because a wide set of call sites
    # still pass it positionally/by keyword; nothing sets it to anything
    # but ``None``.
    email: str | None = None
    # Kept only for the callback request so installation discovery can use
    # GitHub's user-scoped view.  Account records never persist this token.
    access_token: str | None = dataclass_field(
        default=None, repr=False, compare=False
    )


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def new_state() -> str:
    return secrets.token_urlsafe(32)


def new_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def authorize_url(
    settings: Settings,
    *,
    state: str,
    redirect_uri: str,
    code_challenge: str,
) -> str:
    params = {
        "client_id": settings.github_oauth_client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if settings.github_oauth_scope:
        params["scope"] = settings.github_oauth_scope
    return f"{settings.github_oauth_authorize_url}?{urlencode(params)}"


def exchange_code(
    settings: Settings,
    *,
    code: str,
    redirect_uri: str,
    code_verifier: str,
) -> str:
    payload = {
        "client_id": settings.github_oauth_client_id,
        "client_secret": settings.github_oauth_client_secret,
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }
    try:
        response = httpx.post(
            settings.github_oauth_token_url,
            data=payload,
            headers={"Accept": "application/json"},
            timeout=15.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise OAuthError("GitHub token exchange failed") from exc

    data = response.json()
    if data.get("error"):
        raise OAuthError(str(data.get("error_description") or data["error"]))
    token = data.get("access_token")
    if not token:
        raise OAuthError("GitHub token response did not include an access token")
    return str(token)


def _github_headers(token: str, settings: Settings) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": settings.github_api_version,
    }


def _fetch_json(settings: Settings, token: str, path: str):
    url = f"{settings.github_api_base_url.rstrip('/')}{path}"
    try:
        response = httpx.get(
            url, headers=_github_headers(token, settings), timeout=15.0
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise OAuthError("GitHub identity lookup failed") from exc
    return response.json()


def fetch_identity(settings: Settings, token: str) -> GitHubIdentity:
    """Resolve the authenticated user's id/login from GitHub.

    w-57 (2026-08-16): deliberately does not read ``/user``'s own ``email``
    field or fall back to ``/user/emails`` — brnrd stopped collecting a
    login email at all (kb decision, maintainer-signed 2026-08-16). Stripe
    now collects a billing email directly from the payer at checkout
    instead (see ``routers/billing.py::_ensure_customer``).
    """
    user = _fetch_json(settings, token, "/user")
    github_id = user.get("id")
    login = user.get("login")
    if github_id is None or not login:
        raise OAuthError("GitHub identity response was missing id/login")

    return GitHubIdentity(
        github_id=str(github_id),
        login=str(login),
        access_token=token,
    )


def resolve_identity(
    settings: Settings,
    *,
    code: str,
    redirect_uri: str,
    code_verifier: str,
) -> GitHubIdentity:
    token = exchange_code(
        settings,
        code=code,
        redirect_uri=redirect_uri,
        code_verifier=code_verifier,
    )
    return fetch_identity(settings, token)

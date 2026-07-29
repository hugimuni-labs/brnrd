"""GitHub App installation-token helpers."""

from __future__ import annotations

import base64
import time
from typing import Any
from urllib.parse import quote

import httpx
import jwt


class GitHubAppConfigError(RuntimeError):
    pass


def _private_key(settings) -> str:
    value = settings.github_app_private_key_b64.strip()
    if not value:
        raise GitHubAppConfigError("BRNRD_GITHUB_APP_PRIVATE_KEY_B64 is not configured")
    return base64.b64decode(value).decode("utf-8")


def app_jwt(settings) -> str:
    if not settings.github_app_id:
        raise GitHubAppConfigError("BRNRD_GITHUB_APP_ID is not configured")
    now = int(time.time())
    payload = {"iat": now - 60, "exp": now + 9 * 60, "iss": settings.github_app_id}
    return jwt.encode(payload, _private_key(settings), algorithm="RS256")


def _headers(settings, token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": settings.github_api_version,
    }


def get_app_installation(settings, installation_id: str) -> dict[str, Any]:
    """Fetch one installation by id without enumerating the App's estate."""
    url = (
        f"{settings.github_api_base_url.rstrip('/')}"
        f"/app/installations/{installation_id}"
    )
    with httpx.Client(timeout=20) as client:
        response = client.get(url, headers=_headers(settings, app_jwt(settings)))
        response.raise_for_status()
        return response.json()


def list_user_installations(
    settings, user_access_token: str
) -> list[dict[str, Any]]:
    """List App installations visible to the just-authenticated GitHub user."""
    installations: list[dict[str, Any]] = []
    url = f"{settings.github_api_base_url.rstrip('/')}/user/installations"
    with httpx.Client(timeout=20) as client:
        while url:
            response = client.get(
                url,
                headers=_headers(settings, user_access_token),
                params={"per_page": 100} if "?" not in url else None,
            )
            response.raise_for_status()
            data = response.json()
            installations.extend(data.get("installations") or [])
            url = response.links.get("next", {}).get("url")
    return installations


def installation_access_credential(
    settings,
    installation_id: str,
    *,
    repository_ids: list[int] | None = None,
    repositories: list[str] | None = None,
) -> dict[str, str]:
    """Mint a short-lived installation credential, optionally repo-scoped."""
    jwt_token = app_jwt(settings)
    url = f"{settings.github_api_base_url.rstrip('/')}/app/installations/{installation_id}/access_tokens"
    body = None
    if repository_ids:
        body = {"repository_ids": repository_ids}
    elif repositories:
        body = {"repositories": repositories}
    with httpx.Client(timeout=20) as client:
        response = client.post(url, headers=_headers(settings, jwt_token), json=body)
        response.raise_for_status()
        data = response.json()
    token = data.get("token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("GitHub installation token response did not include a token")
    expires_at = data.get("expires_at")
    if not isinstance(expires_at, str) or not expires_at:
        raise RuntimeError("GitHub installation token response did not include expires_at")
    return {"token": token, "expires_at": expires_at}


def installation_access_token(settings, installation_id: str) -> str:
    return installation_access_credential(settings, installation_id)["token"]


def list_installation_repositories(
    settings,
    installation_id: str,
    *,
    token: str | None = None,
) -> list[dict[str, Any]]:
    token = token or installation_access_token(settings, installation_id)
    repos: list[dict[str, Any]] = []
    url = f"{settings.github_api_base_url.rstrip('/')}/installation/repositories"
    with httpx.Client(timeout=20) as client:
        while url:
            response = client.get(url, headers=_headers(settings, token), params={"per_page": 100} if "?" not in url else None)
            response.raise_for_status()
            data = response.json()
            repos.extend(data.get("repositories") or [])
            url = response.links.get("next", {}).get("url")
    return repos


def ensure_repository_label(
    settings,
    token: str,
    repo: str,
    label: str,
) -> None:
    """Create the App-native summons label when a repo first appears.

    Labels need only the App's existing Issues permission.  This is the
    universal assignment affordance; making a separate user account an
    assignee would require collaborator access and, to automate it, the much
    broader repository Administration permission.
    """
    base = settings.github_api_base_url.rstrip("/")
    encoded = quote(label, safe="")
    headers = _headers(settings, token)
    with httpx.Client(timeout=20) as client:
        existing = client.get(
            f"{base}/repos/{repo}/labels/{encoded}",
            headers=headers,
        )
        if existing.status_code == 200:
            return
        if existing.status_code != 404:
            existing.raise_for_status()
        created = client.post(
            f"{base}/repos/{repo}/labels",
            headers=headers,
            json={
                "name": label,
                "color": "6f42c1",
                "description": "Summon the brnrd resident",
            },
        )
        created.raise_for_status()


def organization_membership(
    settings,
    installation_id: str,
    organization: str,
    username: str,
) -> dict[str, Any] | None:
    """The user's membership as seen by this organization installation.

    ``Members: read`` is the narrow permission that makes organization
    ownership provable. A 404 means no membership; other failures stay loud so
    a missing grant cannot be misreported as a clean refusal.
    """
    token = installation_access_token(settings, installation_id)
    base = settings.github_api_base_url.rstrip("/")
    with httpx.Client(timeout=20) as client:
        response = client.get(
            f"{base}/orgs/{quote(organization, safe='')}/memberships/"
            f"{quote(username, safe='')}",
            headers=_headers(settings, token),
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
    return data if isinstance(data, dict) else None

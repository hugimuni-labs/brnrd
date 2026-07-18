"""GitHub App installation-token helpers."""

from __future__ import annotations

import base64
import time
from typing import Any

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


def list_app_installations(settings) -> list[dict[str, Any]]:
    """List installations visible to this GitHub App.

    This uses the App JWT, not a user OAuth token. It lets brnrd recover when
    GitHub sends an already-installed user to the GitHub permissions page
    instead of back through the setup callback.
    """
    jwt_token = app_jwt(settings)
    installations: list[dict[str, Any]] = []
    url = f"{settings.github_api_base_url.rstrip('/')}/app/installations"
    with httpx.Client(timeout=20) as client:
        while url:
            response = client.get(
                url,
                headers=_headers(settings, jwt_token),
                params={"per_page": 100} if "?" not in url else None,
            )
            response.raise_for_status()
            installations.extend(response.json() or [])
            url = response.links.get("next", {}).get("url")
    return installations


def installation_access_token(settings, installation_id: str) -> str:
    jwt_token = app_jwt(settings)
    url = f"{settings.github_api_base_url.rstrip('/')}/app/installations/{installation_id}/access_tokens"
    with httpx.Client(timeout=20) as client:
        response = client.post(url, headers=_headers(settings, jwt_token))
        response.raise_for_status()
        data = response.json()
    token = data.get("token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("GitHub installation token response did not include a token")
    return token


def list_installation_repositories(settings, installation_id: str) -> list[dict[str, Any]]:
    token = installation_access_token(settings, installation_id)
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


def invite_collaborator(
    settings,
    installation_id: str,
    repo_full_name: str,
    username: str,
    *,
    permission: str = "push",
) -> dict[str, Any]:
    """Invite a GitHub user as a repository collaborator using the App installation.

    This is for the human-facing bot user identity such as ``brnrd-bot``. It is
    distinct from the GitHub App identity such as ``brnrd-dev[bot]``.
    """
    token = installation_access_token(settings, installation_id)
    url = f"{settings.github_api_base_url.rstrip('/')}/repos/{repo_full_name}/collaborators/{username}"
    with httpx.Client(timeout=20) as client:
        response = client.put(
            url,
            headers=_headers(settings, token),
            json={"permission": permission},
        )
        # 201 = invitation created, 204 = already collaborator or permission updated
        if response.status_code not in (201, 204):
            response.raise_for_status()
        data = response.json() if response.content else {}
    data["status_code"] = response.status_code
    return data

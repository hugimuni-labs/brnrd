"""GitHub App platform client for brnrd-managed ingress and delivery.

This module is intentionally transport-only: webhook normalization lives in
``routers.webhooks`` and endpoint strings come from ``brr.gates.github.paths``
so the managed App and OSS gate do not drift.
"""

from __future__ import annotations

from urllib.parse import quote

import httpx

from brr.gates.github import paths


def _headers(token: str, api_version: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": api_version,
    }


def _url(api_base_url: str, path: str) -> str:
    return api_base_url.rstrip("/") + path


def post_issue_comment(
    token: str,
    api_base_url: str,
    api_version: str,
    repo: str,
    issue_number: int,
    body: str,
    *,
    timeout: float = 30.0,
) -> None:
    resp = httpx.post(
        _url(api_base_url, paths.issue_comments(repo, issue_number)),
        headers=_headers(token, api_version),
        json={"body": body},
        timeout=timeout,
    )
    resp.raise_for_status()


def post_review_reply(
    token: str,
    api_base_url: str,
    api_version: str,
    repo: str,
    pr_number: int,
    comment_id: int,
    body: str,
    *,
    timeout: float = 30.0,
) -> None:
    resp = httpx.post(
        _url(api_base_url, paths.pull_comment_replies(repo, pr_number, comment_id)),
        headers=_headers(token, api_version),
        json={"body": body},
        timeout=timeout,
    )
    resp.raise_for_status()


def list_repository_invitations(
    token: str,
    api_base_url: str,
    api_version: str,
    *,
    timeout: float = 20.0,
) -> list[dict]:
    """``GET /user/repository_invitations`` — invites pending for *this token's
    own account* (brnrd-bot's, never the calling human's).
    """
    invitations: list[dict] = []
    url = _url(api_base_url, "/user/repository_invitations")
    with httpx.Client(timeout=timeout) as client:
        while url:
            resp = client.get(
                url,
                headers=_headers(token, api_version),
                params={"per_page": 100} if "?" not in url else None,
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                invitations.extend(item for item in data if isinstance(item, dict))
            url = resp.links.get("next", {}).get("url")
    return invitations


def accept_repository_invitation(
    token: str,
    api_base_url: str,
    api_version: str,
    invitation_id: int | str,
    *,
    timeout: float = 20.0,
) -> None:
    """``PATCH /user/repository_invitations/{id}`` — accept one invite as the
    token's own account."""
    resp = httpx.patch(
        _url(api_base_url, f"/user/repository_invitations/{invitation_id}"),
        headers=_headers(token, api_version),
        timeout=timeout,
    )
    resp.raise_for_status()


def check_repository_collaborator(
    token: str,
    api_base_url: str,
    api_version: str,
    repo: str,
    username: str,
    *,
    timeout: float = 20.0,
) -> bool:
    """``GET /repos/{repo}/collaborators/{username}`` — is ``username`` a
    collaborator on ``repo``, from GitHub's own documented contract for this
    endpoint: 204 = yes, 404 = no. Anything else (403 lacking push access,
    5xx, a network error) is a genuine "couldn't tell" and is raised rather
    than folded into either answer — the caller records that as unknown, not
    a guess (see ``github_marker.sync_marker_for_repos``).
    """
    resp = httpx.get(
        _url(api_base_url, f"/repos/{repo}/collaborators/{quote(username, safe='')}"),
        headers=_headers(token, api_version),
        timeout=timeout,
    )
    if resp.status_code == 204:
        return True
    if resp.status_code == 404:
        return False
    resp.raise_for_status()
    raise RuntimeError(f"unexpected collaborator-check status {resp.status_code}")


def fetch_pull_head_ref(
    token: str,
    api_base_url: str,
    api_version: str,
    repo: str,
    pr_number: int,
    *,
    timeout: float = 30.0,
) -> str | None:
    resp = httpx.get(
        _url(api_base_url, paths.pull(repo, pr_number)),
        headers=_headers(token, api_version),
        timeout=timeout,
    )
    resp.raise_for_status()
    payload = resp.json() or {}
    ref = ((payload.get("head") or {}).get("ref") or "").strip()
    return ref or None

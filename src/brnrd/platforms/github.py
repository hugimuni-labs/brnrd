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


_REACTION_PATHS = {
    "issue_comment": "/repos/{repo}/issues/comments/{id}/reactions",
    "issue": "/repos/{repo}/issues/{id}/reactions",
    "review_comment": "/repos/{repo}/pulls/comments/{id}/reactions",
}


def add_reaction(
    token: str,
    api_base_url: str,
    api_version: str,
    repo: str,
    *,
    target: str,
    target_id: int,
    content: str = "eyes",
    timeout: float = 30.0,
) -> bool:
    """React to the thing that summoned a run — the "I saw this" signal.

    ``target`` selects the endpoint: ``issue_comment`` (a timeline comment),
    ``issue`` (the issue or PR itself — a PR *is* an issue for this
    endpoint), or ``review_comment`` (an inline PR review-line comment).

    GitHub returns 200 when the reaction already exists and 201 when it is
    newly created; both count as success, which makes this call naturally
    idempotent — no bookkeeping needed on the caller's side. Any other
    status (403/404/422/5xx) returns False rather than raising, so a bad
    response never becomes an exception the caller has to catch. A
    transport-level failure (network error, timeout) still propagates —
    callers wrap this non-fatally, same as ``post_issue_comment``.
    """
    template = _REACTION_PATHS.get(target)
    if template is None:
        return False
    resp = httpx.post(
        _url(api_base_url, template.format(repo=repo, id=target_id)),
        headers=_headers(token, api_version),
        json={"content": content},
        timeout=timeout,
    )
    return resp.status_code in (200, 201)


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
    endpoint: 204 = yes, 404 = no.

    The ``…/permission`` endpoint is NOT a substitute, driven live 2026-08-01
    (#976 review): it reports *effective* permission, so on a public repo a
    complete stranger answers 200 with ``permission: read`` — under a
    200-means-member reading, everyone becomes a collaborator. Membership and
    effective access are different questions, and this check exists for
    membership (will assigns / review-requests / comment-tags reach the
    resident). A 403 here does NOT uniformly mean the calling credential
    lacks a grant (rescoped 2026-08-05, #1141): on the GitHub App
    installation token it does — that credential only needs
    ``Metadata: read``, which the App already holds, so a 403 is a genuine
    gap. On a *user* access token, GitHub's own docs require the caller to
    already have push access just to use this endpoint at all, so a bot
    lacking push access 403s regardless of whether it is itself a
    collaborator — "not a collaborator" wearing a permission fault's
    clothes. The caller disambiguates by passing the right
    ``MarkerCheckPrincipal`` to
    ``github_marker.classify_marker_check_failure``; this function only
    makes the HTTP call and stays credential-agnostic. Anything else (5xx,
    network) is a genuine "couldn't tell" and is raised rather than folded
    into either answer — the caller records that as unknown, not a guess
    (see ``github_marker.sync_marker_for_repos``).
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


def fetch_collaborator_permission(
    token: str,
    api_base_url: str,
    api_version: str,
    repo: str,
    username: str,
    *,
    timeout: float = 15.0,
) -> str | None:
    """``username``'s permission on ``repo`` — the managed lane's half of #408.

    Mirrors ``brr.gates.github.client.get_collaborator_permission``, which
    the self-hosted gate has always used, so both lanes read the same fact
    from the same endpoint. Returns one of
    ``admin``/``write``/``maintain``/``read``/``none``, or ``None`` when the
    lookup could not be made — which callers must treat as *unknown*, never
    as *permitted*.
    """
    resp = httpx.get(
        _url(api_base_url, paths.collaborator_permission(repo, username)),
        headers=_headers(token, api_version),
        timeout=timeout,
    )
    if resp.status_code == 404:
        # Not a collaborator at all. A definite answer, not a failure.
        return "none"
    resp.raise_for_status()
    payload = resp.json() or {}
    permission = payload.get("permission")
    return str(permission) if isinstance(permission, str) and permission else None

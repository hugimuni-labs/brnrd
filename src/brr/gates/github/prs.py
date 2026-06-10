"""Pull-request delivery for agent-addressed GitHub outbox sends."""

from __future__ import annotations

from . import client
from .paths import pull, pulls


def _head_param(repo: str, head: str) -> str:
    """Return GitHub's ``head`` query value for an in-repo branch."""
    if ":" in head:
        return head
    owner = repo.split("/", 1)[0]
    return f"{owner}:{head}"


def existing_open_pr(token: str, repo: str, head: str) -> dict | None:
    """Return the first open PR for *head*, or ``None`` when absent."""
    rows = client._api_get(
        token,
        pulls(repo),
        params={"state": "open", "head": _head_param(repo, head), "per_page": 1},
    )
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        return rows[0]
    return None


def open_or_refresh_pr(
    token: str,
    repo: str,
    *,
    head: str,
    title: str,
    body: str,
    base: str | None = None,
) -> str | None:
    """Create or update an open pull request and return its URL if known."""
    existing = existing_open_pr(token, repo, head)
    if existing is not None:
        number = existing.get("number")
        if not isinstance(number, int):
            return None
        payload = client._api_patch(
            token, pull(repo, number), body={"title": title, "body": body},
        )
    else:
        create_body = {"title": title, "head": head, "body": body}
        if base:
            create_body["base"] = base
        payload = client._api_post(token, pulls(repo), body=create_body)
    if isinstance(payload, dict):
        url = payload.get("html_url")
        if isinstance(url, str) and url:
            return url
    return None

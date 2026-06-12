"""GitHub App platform client for brnrd-managed ingress and delivery.

This module is intentionally transport-only: webhook normalization lives in
``routers.webhooks`` and endpoint strings come from ``brr.gates.github.paths``
so the managed App and OSS gate do not drift.
"""

from __future__ import annotations

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

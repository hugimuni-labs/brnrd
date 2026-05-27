"""GitHub REST API path builders.

Pure functions, no transport. Brnrd-reusable: the managed-mode backend
(``src/brnrd/``) hits the same endpoints from its async client, so
keeping these as one-line helpers makes the wire surface explicit and
keeps both sides from drifting. See
``kb/design-github-gate-vs-brnrd-app.md`` for the OSS-vs-brnrd split.
"""

from __future__ import annotations


def user() -> str:
    """``GET /user`` — authenticated principal lookup."""
    return "/user"


def repo_issues(repo: str) -> str:
    """``GET /repos/{repo}/issues`` — list issues (and PRs)."""
    return f"/repos/{repo}/issues"


def repo_issue_comments(repo: str) -> str:
    """``GET /repos/{repo}/issues/comments`` — every issue/PR timeline
    comment across the repo. Note: also includes top-level PR comments
    (the timeline ones), but *not* inline review-line comments — those
    live on ``/pulls/comments``."""
    return f"/repos/{repo}/issues/comments"


def repo_pulls_comments(repo: str) -> str:
    """``GET /repos/{repo}/pulls/comments`` — every inline PR review
    comment (diff line thread) across the repo."""
    return f"/repos/{repo}/pulls/comments"


def pull(repo: str, number: int) -> str:
    """``GET /repos/{repo}/pulls/{number}`` — single PR metadata."""
    return f"/repos/{repo}/pulls/{number}"


def issue_comments(repo: str, number: int) -> str:
    """``POST /repos/{repo}/issues/{n}/comments`` — post a top-level
    comment on issue or PR ``#n``."""
    return f"/repos/{repo}/issues/{number}/comments"


def issue_comment(repo: str, comment_id: int) -> str:
    """``PATCH /repos/{repo}/issues/comments/{id}`` — edit one timeline
    comment in place (progress-card flow)."""
    return f"/repos/{repo}/issues/comments/{comment_id}"


def pull_comment_replies(repo: str, pr_number: int, comment_id: int) -> str:
    """``POST /repos/{repo}/pulls/{pr}/comments/{cid}/replies`` — reply
    to an inline review comment in-thread."""
    return f"/repos/{repo}/pulls/{pr_number}/comments/{comment_id}/replies"

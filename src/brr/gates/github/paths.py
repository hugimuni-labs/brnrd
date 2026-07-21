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


def pulls(repo: str) -> str:
    """``GET/POST /repos/{repo}/pulls`` — list or create pull requests."""
    return f"/repos/{repo}/pulls"


def pull_review(repo: str, number: int, review_id: int) -> str:
    """``GET /repos/{repo}/pulls/{n}/reviews/{review_id}`` — fetch one
    PR review (summary body + state). Used to check whether the review
    that owns a freshly-seen line comment mentions us in its summary
    body even when no individual line comment did."""
    return f"/repos/{repo}/pulls/{number}/reviews/{review_id}"


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


def issue_events(repo: str, number: int) -> str:
    """``GET /repos/{repo}/issues/{n}/events`` — the issue's event
    timeline (labeled/assigned/closed/…). The assignee trigger reads it
    to learn *who performed* the assignment: the assigner, not the issue
    author, is the trust principal the #408 authorization gate should
    judge."""
    return f"/repos/{repo}/issues/{number}/events"


def collaborator_permission(repo: str, username: str) -> str:
    """``GET /repos/{repo}/collaborators/{username}/permission`` — the
    permission level (``admin``/``write``/``maintain``/``read``/``none``)
    *username* holds on the repo. Used by the authorization gate (#408)
    to decide whether a non-member's GitHub activity may enqueue an
    autonomous run."""
    return f"/repos/{repo}/collaborators/{username}/permission"

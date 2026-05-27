"""Trigger pollers — turn new GitHub activity into inbox events.

Three triggers:

- ``label`` — labelled open issues become events (PRs excluded; they
  almost always carry ongoing back-and-forth that label-once doesn't
  capture).
- ``mention`` — comments containing the configured mention string fire
  events. Covers both issue/PR timeline comments (``/issues/comments``)
  and inline PR review-line comments (``/pulls/comments``); the latter
  gets fetched on its own cursor.
- ``any`` — every new issue, PR, and comment fires an event. Overrides
  label and mention; off by default because it's token-expensive.

The pollers share ``_fetch_pr_head_branch`` to attach ``branch_target``
to PR-anchored events, so the daemon's pre-task fetch+ff hook can
refresh the PR head before the worker runs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ... import protocol
from . import cache, client, parse
from .constants import _SEEN_CAP
from .paths import (
    pull as _pull_path,
    repo_issue_comments,
    repo_issues,
    repo_pulls_comments,
)


def _fetch_pr_head_branch(token: str, repo: str, pr_number: int) -> str | None:
    pr = client._api_get(token, _pull_path(repo, pr_number))
    if not isinstance(pr, dict):
        return None
    head = pr.get("head") or {}
    ref = head.get("ref")
    return str(ref) if isinstance(ref, str) and ref else None


# ── label trigger ──────────────────────────────────────────────────


def _poll_label_trigger(
    token: str,
    repo: str,
    label: str,
    cursor: dict,
    inbox_dir: Path,
) -> None:
    since = cursor.get("issues_since") or cache._initial_since()
    seen = set(cursor.get("seen_issue_numbers") or [])
    issues = client._api_get(
        token, repo_issues(repo),
        params={
            "state": "open",
            "labels": label,
            "since": since,
            "per_page": 100,
            "sort": "updated",
            "direction": "asc",
        },
    ) or []

    latest_seen = since
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        number = issue.get("number")
        if not isinstance(number, int):
            continue
        if number in seen:
            continue
        # GitHub returns PRs from /issues too; skip them — PR work belongs
        # to the mention trigger, not the label trigger, because PRs almost
        # always have ongoing back-and-forth and a label trigger would
        # only fire once per PR which is rarely what an operator wants.
        if "pull_request" in issue:
            continue

        title = str(issue.get("title") or "").strip()
        body = str(issue.get("body") or "").strip()
        author = (issue.get("user") or {}).get("login") or ""
        protocol.create_event(
            inbox_dir,
            source="github",
            body=parse._format_event_body(title, body),
            github_repo=repo,
            github_kind="issue",
            github_issue_number=number,
            github_author=author,
            github_html_url=issue.get("html_url") or "",
            github_trigger="label",
            github_label=label,
            title=title,
        )
        seen.add(number)
        ts = issue.get("updated_at") or issue.get("created_at")
        if isinstance(ts, str) and ts > latest_seen:
            latest_seen = ts

    cursor["issues_since"] = latest_seen
    cursor["seen_issue_numbers"] = sorted(seen)[-_SEEN_CAP:]


# ── mention trigger ───────────────────────────────────────────────


def _poll_mention_trigger(
    token: str,
    repo: str,
    mention: str,
    token_login: str,
    cursor: dict,
    inbox_dir: Path,
) -> None:
    since = cursor.get("comments_since") or cache._initial_since()
    seen = set(cursor.get("seen_comment_ids") or [])
    comments = client._api_get(
        token, repo_issue_comments(repo),
        params={
            "since": since,
            "per_page": 100,
            "sort": "updated",
            "direction": "asc",
        },
    ) or []

    pr_branch_cache: dict[int, str] = {}
    latest_seen = since
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        cid = comment.get("id")
        if not isinstance(cid, int):
            continue
        if cid in seen:
            continue
        body = str(comment.get("body") or "")
        if mention not in body:
            continue
        author = (comment.get("user") or {}).get("login") or ""
        if parse._skip_mention_comment_author(author, mention, token_login):
            # Don't re-trigger when the named @-account echoes the trigger
            # (or, for non-@ triggers, the token holder's own comments).
            continue

        html_url = str(comment.get("html_url") or "")
        is_pr = "/pull/" in html_url
        issue_number = parse._extract_issue_number(comment.get("issue_url") or "")
        if issue_number is None:
            continue

        meta: dict[str, Any] = {
            "github_repo": repo,
            "github_kind": "pr-comment" if is_pr else "issue-comment",
            "github_issue_number": issue_number,
            "github_comment_id": cid,
            "github_author": author,
            "github_html_url": html_url,
            "github_trigger": "mention",
            "github_mention": mention,
        }
        if is_pr:
            meta["github_pr_number"] = issue_number
            branch = pr_branch_cache.get(issue_number) or _fetch_pr_head_branch(
                token, repo, issue_number,
            )
            if branch:
                pr_branch_cache[issue_number] = branch
                meta["branch_target"] = branch

        protocol.create_event(
            inbox_dir,
            source="github",
            body=parse._format_event_body("", body),
            **meta,
        )
        seen.add(cid)
        ts = comment.get("updated_at") or comment.get("created_at")
        if isinstance(ts, str) and ts > latest_seen:
            latest_seen = ts

    cursor["comments_since"] = latest_seen
    cursor["seen_comment_ids"] = sorted(seen)[-_SEEN_CAP:]

    _poll_mention_review_comments(
        token, repo, mention, token_login, cursor, inbox_dir, pr_branch_cache,
    )


def _poll_mention_review_comments(
    token: str,
    repo: str,
    mention: str,
    token_login: str,
    cursor: dict,
    inbox_dir: Path,
    pr_branch_cache: dict[int, str],
) -> None:
    """Poll inline PR review comments (diff line threads) for *mention*."""
    since = cursor.get("review_comments_since") or cache._initial_since()
    seen = set(cursor.get("seen_review_comment_ids") or [])
    comments = client._api_get(
        token, repo_pulls_comments(repo),
        params={
            "since": since,
            "per_page": 100,
            "sort": "updated",
            "direction": "asc",
        },
    ) or []

    latest_seen = since
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        cid = comment.get("id")
        if not isinstance(cid, int):
            continue
        if cid in seen:
            continue
        body = str(comment.get("body") or "")
        if mention not in body:
            continue
        author = (comment.get("user") or {}).get("login") or ""
        if parse._skip_mention_comment_author(author, mention, token_login):
            continue

        pr_number = parse._extract_pr_number(comment.get("pull_request_url") or "")
        if pr_number is None:
            continue

        html_url = str(comment.get("html_url") or "")
        path = str(comment.get("path") or "").strip()
        line = comment.get("line")
        meta: dict[str, Any] = {
            "github_repo": repo,
            "github_kind": "pr-review-comment",
            "github_issue_number": pr_number,
            "github_pr_number": pr_number,
            "github_comment_id": cid,
            "github_author": author,
            "github_html_url": html_url,
            "github_trigger": "mention",
            "github_mention": mention,
        }
        if path:
            meta["github_path"] = path
        if isinstance(line, int):
            meta["github_line"] = line

        branch = pr_branch_cache.get(pr_number) or _fetch_pr_head_branch(
            token, repo, pr_number,
        )
        if branch:
            pr_branch_cache[pr_number] = branch
            meta["branch_target"] = branch

        protocol.create_event(
            inbox_dir,
            source="github",
            body=parse._format_review_comment_body(path, line, body),
            **meta,
        )
        seen.add(cid)
        ts = comment.get("updated_at") or comment.get("created_at")
        if isinstance(ts, str) and ts > latest_seen:
            latest_seen = ts

    cursor["review_comments_since"] = latest_seen
    cursor["seen_review_comment_ids"] = sorted(seen)[-_SEEN_CAP:]


# ── any trigger ───────────────────────────────────────────────────


def _poll_any_activity(
    token: str,
    repo: str,
    bot_login: str,
    cursor: dict,
    inbox_dir: Path,
) -> None:
    """Poll all issues, PRs, and comments without filtering.

    Used when ``triggers["any"]`` is set. Emits one event per new issue,
    PR, and comment. PR events carry ``branch_target`` so the daemon's
    pre-task fetch+ff refreshes the PR head branch. Bot's own comments
    are filtered to prevent self-triggering loops.
    """
    # --- Issues and PRs -----------------------------------------------
    since = cursor.get("any_issues_since") or cache._initial_since()
    seen = set(cursor.get("any_seen_issue_numbers") or [])
    items = client._api_get(
        token, repo_issues(repo),
        params={
            "state": "all",
            "since": since,
            "per_page": 100,
            "sort": "updated",
            "direction": "asc",
        },
    ) or []

    latest_seen = since
    for item in items:
        if not isinstance(item, dict):
            continue
        number = item.get("number")
        if not isinstance(number, int):
            continue
        if number in seen:
            continue

        is_pr = "pull_request" in item
        title = str(item.get("title") or "").strip()
        body_text = str(item.get("body") or "").strip()
        author = (item.get("user") or {}).get("login") or ""
        meta: dict[str, Any] = {
            "github_repo": repo,
            "github_issue_number": number,
            "github_author": author,
            "github_html_url": item.get("html_url") or "",
            "github_trigger": "any",
        }
        if is_pr:
            meta["github_kind"] = "pr"
            meta["github_pr_number"] = number
            branch = _fetch_pr_head_branch(token, repo, number)
            if branch:
                meta["branch_target"] = branch
        else:
            meta["github_kind"] = "issue"
        protocol.create_event(
            inbox_dir,
            source="github",
            body=parse._format_event_body(title, body_text),
            title=title,
            **meta,
        )
        seen.add(number)
        ts = item.get("updated_at") or item.get("created_at")
        if isinstance(ts, str) and ts > latest_seen:
            latest_seen = ts

    cursor["any_issues_since"] = latest_seen
    cursor["any_seen_issue_numbers"] = sorted(seen)[-_SEEN_CAP:]

    # --- Comments -----------------------------------------------------
    since_c = cursor.get("any_comments_since") or cache._initial_since()
    seen_c = set(cursor.get("any_seen_comment_ids") or [])
    comments = client._api_get(
        token, repo_issue_comments(repo),
        params={
            "since": since_c,
            "per_page": 100,
            "sort": "updated",
            "direction": "asc",
        },
    ) or []

    pr_branch_cache: dict[int, str] = {}
    latest_seen_c = since_c
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        cid = comment.get("id")
        if not isinstance(cid, int):
            continue
        if cid in seen_c:
            continue
        author = (comment.get("user") or {}).get("login") or ""
        if author and bot_login and author == bot_login:
            continue
        html_url = str(comment.get("html_url") or "")
        is_pr_comment = "/pull/" in html_url
        issue_number = parse._extract_issue_number(comment.get("issue_url") or "")
        if issue_number is None:
            continue
        body_text = str(comment.get("body") or "")
        meta_c: dict[str, Any] = {
            "github_repo": repo,
            "github_kind": "pr-comment" if is_pr_comment else "issue-comment",
            "github_issue_number": issue_number,
            "github_comment_id": cid,
            "github_author": author,
            "github_html_url": html_url,
            "github_trigger": "any",
        }
        if is_pr_comment:
            meta_c["github_pr_number"] = issue_number
            branch = pr_branch_cache.get(issue_number) or _fetch_pr_head_branch(
                token, repo, issue_number,
            )
            if branch:
                pr_branch_cache[issue_number] = branch
                meta_c["branch_target"] = branch
        protocol.create_event(
            inbox_dir,
            source="github",
            body=parse._format_event_body("", body_text),
            **meta_c,
        )
        seen_c.add(cid)
        ts = comment.get("updated_at") or comment.get("created_at")
        if isinstance(ts, str) and ts > latest_seen_c:
            latest_seen_c = ts

    cursor["any_comments_since"] = latest_seen_c
    cursor["any_seen_comment_ids"] = sorted(seen_c)[-_SEEN_CAP:]

    _poll_any_review_comments(
        token, repo, bot_login, cursor, inbox_dir, pr_branch_cache,
    )


def _poll_any_review_comments(
    token: str,
    repo: str,
    bot_login: str,
    cursor: dict,
    inbox_dir: Path,
    pr_branch_cache: dict[int, str],
) -> None:
    since = cursor.get("any_review_comments_since") or cache._initial_since()
    seen = set(cursor.get("any_seen_review_comment_ids") or [])
    comments = client._api_get(
        token, repo_pulls_comments(repo),
        params={
            "since": since,
            "per_page": 100,
            "sort": "updated",
            "direction": "asc",
        },
    ) or []

    latest_seen = since
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        cid = comment.get("id")
        if not isinstance(cid, int):
            continue
        if cid in seen:
            continue
        author = (comment.get("user") or {}).get("login") or ""
        if author and bot_login and author == bot_login:
            continue

        pr_number = parse._extract_pr_number(comment.get("pull_request_url") or "")
        if pr_number is None:
            continue

        html_url = str(comment.get("html_url") or "")
        path = str(comment.get("path") or "").strip()
        line = comment.get("line")
        body_text = str(comment.get("body") or "")
        meta: dict[str, Any] = {
            "github_repo": repo,
            "github_kind": "pr-review-comment",
            "github_issue_number": pr_number,
            "github_pr_number": pr_number,
            "github_comment_id": cid,
            "github_author": author,
            "github_html_url": html_url,
            "github_trigger": "any",
        }
        if path:
            meta["github_path"] = path
        if isinstance(line, int):
            meta["github_line"] = line

        branch = pr_branch_cache.get(pr_number) or _fetch_pr_head_branch(
            token, repo, pr_number,
        )
        if branch:
            pr_branch_cache[pr_number] = branch
            meta["branch_target"] = branch

        protocol.create_event(
            inbox_dir,
            source="github",
            body=parse._format_review_comment_body(path, line, body_text),
            **meta,
        )
        seen.add(cid)
        ts = comment.get("updated_at") or comment.get("created_at")
        if isinstance(ts, str) and ts > latest_seen:
            latest_seen = ts

    cursor["any_review_comments_since"] = latest_seen
    cursor["any_seen_review_comment_ids"] = sorted(seen)[-_SEEN_CAP:]

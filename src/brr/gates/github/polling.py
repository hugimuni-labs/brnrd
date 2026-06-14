"""Trigger pollers — turn new GitHub activity into inbox events.

Four triggers:

- ``label`` — labelled open issues become events (PRs excluded; they
  almost always carry ongoing back-and-forth that label-once doesn't
  capture).
- ``mention`` — comments containing the configured mention string fire
  events. Covers both issue/PR timeline comments (``/issues/comments``)
  and inline PR review-line comments (``/pulls/comments``); the latter
  gets fetched on its own cursor.
- ``opened`` — newly opened issues and PRs become events without also
  subscribing to every comment. This is the low-volume maintainer inbox.
- ``any`` — every new issue, PR, and comment fires an event. Overrides
  opened, label, and mention; off by default because it's token-expensive.

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
    pull_review as _pull_review_path,
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


def _created_in_window(item: dict, since: str) -> bool:
    created = item.get("created_at")
    return isinstance(created, str) and created >= since


def _poll_opened_items(
    token: str,
    repo: str,
    *,
    since: str,
    seen: set[int],
    etags: dict,
    inbox_dir: Path,
    trigger: str,
    bot_login: str = "",
) -> str:
    items = client._api_get(
        token,
        repo_issues(repo),
        params={
            "state": "all",
            "since": since,
            "per_page": 100,
            "sort": "updated",
            "direction": "asc",
        },
        etag_store=etags,
    ) or []

    latest_seen = since
    for item in items:
        if not isinstance(item, dict):
            continue
        number = item.get("number")
        if not isinstance(number, int):
            continue
        ts = item.get("updated_at") or item.get("created_at")
        if isinstance(ts, str) and ts > latest_seen:
            latest_seen = ts
        if number in seen:
            continue
        if not _created_in_window(item, since):
            continue

        is_pr = "pull_request" in item
        title = str(item.get("title") or "").strip()
        body_text = str(item.get("body") or "").strip()
        author = (item.get("user") or {}).get("login") or ""
        # Skip items the token owner authored: the resident opens its own
        # issues/PRs as part of its work (e.g. carving a follow-up ticket),
        # and a self-authored event would wake it on its own action. Mirrors
        # the mention-trigger self-skip; keyed on the authenticated login.
        if author and bot_login and author == bot_login:
            seen.add(number)
            continue
        meta: dict[str, Any] = {
            "github_repo": repo,
            "github_issue_number": number,
            "github_author": author,
            "github_html_url": item.get("html_url") or "",
            "github_trigger": trigger,
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

    return latest_seen


# ── label trigger ──────────────────────────────────────────────────


def _poll_label_trigger(
    token: str,
    repo: str,
    label: str,
    cursor: dict,
    inbox_dir: Path,
    bot_login: str = "",
) -> None:
    since = cursor.get("issues_since") or cache._initial_since()
    seen = set(cursor.get("seen_issue_numbers") or [])
    etags = cursor.setdefault("etags", {})
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
        etag_store=etags,
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
        # Skip issues the token owner authored. The resident labels its own
        # carve-out issues (e.g. `gh issue create --label co-maintainer`),
        # which would otherwise fire the label trigger on its own action —
        # the self-loop that produced the duplicate wakes on #114.
        if author and bot_login and author == bot_login:
            seen.add(number)
            ts = issue.get("updated_at") or issue.get("created_at")
            if isinstance(ts, str) and ts > latest_seen:
                latest_seen = ts
            continue
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


# ── opened trigger ─────────────────────────────────────────────────


def _poll_opened_trigger(
    token: str,
    repo: str,
    cursor: dict,
    inbox_dir: Path,
    bot_login: str = "",
) -> None:
    since = cursor.get("opened_since") or cache._initial_since()
    seen = set(cursor.get("seen_opened_issue_numbers") or [])
    etags = cursor.setdefault("etags", {})
    latest_seen = _poll_opened_items(
        token, repo, since=since, seen=seen, etags=etags,
        inbox_dir=inbox_dir, trigger="opened", bot_login=bot_login,
    )
    cursor["opened_since"] = latest_seen
    cursor["seen_opened_issue_numbers"] = sorted(seen)[-_SEEN_CAP:]


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
    etags = cursor.setdefault("etags", {})
    comments = client._api_get(
        token, repo_issue_comments(repo),
        params={
            "since": since,
            "per_page": 100,
            "sort": "updated",
            "direction": "asc",
        },
        etag_store=etags,
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
    etags = cursor.setdefault("etags", {})
    comments = client._api_get(
        token, repo_pulls_comments(repo),
        params={
            "since": since,
            "per_page": 100,
            "sort": "updated",
            "direction": "asc",
        },
        etag_store=etags,
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

    # Now check the parent reviews of freshly-seen line comments for
    # mentions in the review *summary body*. A review can carry a
    # summary plus zero or more line comments; we only see the summary
    # when /pulls/comments tells us a review exists. Standalone summary
    # reviews (no line comments) are not discoverable this cheaply and
    # fall through to the managed brnrd webhook path — see
    # kb/design-github-gate-vs-brnrd-app.md.
    _poll_mention_review_summaries(
        token, repo, mention, token_login, cursor, inbox_dir,
        comments, pr_branch_cache,
    )


def _poll_mention_review_summaries(
    token: str,
    repo: str,
    mention: str,
    token_login: str,
    cursor: dict,
    inbox_dir: Path,
    line_comments: list,
    pr_branch_cache: dict[int, str],
) -> None:
    """Fetch parent reviews of seen line comments; emit pr-review events.

    Reviews are deduplicated across polls via ``cursor["seen_review_ids"]``,
    so each review is fetched at most once across the gate's lifetime.
    """
    candidates: dict[int, int] = {}  # review_id -> pr_number
    for comment in line_comments:
        if not isinstance(comment, dict):
            continue
        review_id = comment.get("pull_request_review_id")
        if not isinstance(review_id, int):
            continue
        pr_number = parse._extract_pr_number(comment.get("pull_request_url") or "")
        if pr_number is None:
            continue
        candidates.setdefault(review_id, pr_number)

    seen = set(cursor.get("seen_review_ids") or [])
    for review_id, pr_number in candidates.items():
        if review_id in seen:
            continue
        _emit_review_event_if_mentioned(
            token, repo, pr_number, review_id, mention, token_login,
            inbox_dir, pr_branch_cache, trigger="mention",
        )
        seen.add(review_id)

    cursor["seen_review_ids"] = sorted(seen)[-_SEEN_CAP:]


def _emit_review_event_if_mentioned(
    token: str,
    repo: str,
    pr_number: int,
    review_id: int,
    mention: str,
    token_login: str,
    inbox_dir: Path,
    pr_branch_cache: dict[int, str],
    *,
    trigger: str,
) -> None:
    """Fetch one PR review; emit a pr-review event when its summary mentions us.

    Note on the reply path: GitHub has no dedicated "reply to a review
    summary" endpoint, so pr-review responses are posted as top-level
    PR comments via ``/issues/{n}/comments`` (handled in delivery), with
    the standard quote pointer linking back to the review.
    """
    review = client._api_get(
        token, _pull_review_path(repo, pr_number, review_id),
    )
    if not isinstance(review, dict):
        return
    body = str(review.get("body") or "")
    if not body or mention not in body:
        return
    author = (review.get("user") or {}).get("login") or ""
    if parse._skip_mention_comment_author(author, mention, token_login):
        return

    meta: dict[str, Any] = {
        "github_repo": repo,
        "github_kind": "pr-review",
        "github_issue_number": pr_number,
        "github_pr_number": pr_number,
        "github_review_id": review_id,
        "github_review_state": str(review.get("state") or ""),
        "github_author": author,
        "github_html_url": str(review.get("html_url") or ""),
        "github_trigger": trigger,
        "github_mention": mention,
    }
    branch = pr_branch_cache.get(pr_number) or _fetch_pr_head_branch(
        token, repo, pr_number,
    )
    if branch:
        pr_branch_cache[pr_number] = branch
        meta["branch_target"] = branch

    protocol.create_event(
        inbox_dir,
        source="github",
        body=parse._format_event_body("", body),
        **meta,
    )


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
    etags = cursor.setdefault("etags", {})
    # --- Newly opened issues and PRs ----------------------------------
    since = cursor.get("any_issues_since") or cache._initial_since()
    seen = set(cursor.get("any_seen_issue_numbers") or [])
    latest_seen = _poll_opened_items(
        token, repo, since=since, seen=seen, etags=etags,
        inbox_dir=inbox_dir, trigger="any",
    )

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
        etag_store=etags,
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
    etags = cursor.setdefault("etags", {})
    comments = client._api_get(
        token, repo_pulls_comments(repo),
        params={
            "since": since,
            "per_page": 100,
            "sort": "updated",
            "direction": "asc",
        },
        etag_store=etags,
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

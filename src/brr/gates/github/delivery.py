"""Response delivery — post the agent's reply to the originating thread.

Three reply shapes:

- Label-triggered issues: a plain top-level comment on the issue. No
  quote pointer; the issue *is* the source.
- Mention-triggered timeline comments (issue or PR): top-level comment
  on the issue/PR, prefixed with a blockquote linking back at the
  triggering comment (mirrors what GitHub's "Quote reply" button does).
- Mention-triggered inline review comments: in-thread reply via the
  pull-request review-replies API, again with a quote pointer header.
"""

from __future__ import annotations

from pathlib import Path

from requests.utils import quote

from ... import protocol
from ...task import Task
from .. import runtime
from . import client
from .constants import _COMMENT_KINDS
from .paths import issue_comments, pull_comment_replies


def _coerce_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _find_task_for_event(brr_dir: Path, event_id: str) -> Task | None:
    """Scan .brr/tasks/ for the task whose event_id matches *event_id*."""
    tasks_dir = brr_dir / "tasks"
    if not tasks_dir.exists():
        return None
    for path in tasks_dir.glob("*.md"):
        task = Task.from_file(path)
        if task and task.event_id == event_id:
            return task
    return None


def _branch_footer(repo: str, task: Task) -> str:
    """Return a Markdown footer with branch / PR links, or empty string.

    Only appended after finalization has identified the branch that
    should be published. The ``?expand=1`` on the compare URL pre-fills
    GitHub's PR-creation form so clicking it is one step from merging.
    """
    branch = task.meta.get("publish_branch")
    if not branch:
        return ""
    base_url = f"https://github.com/{repo}"
    tree_url = f"{base_url}/tree/{quote(branch, safe='/')}"
    compare_url = f"{base_url}/compare/{quote(branch, safe='/')}?expand=1"
    return (
        f"\n\n---\n"
        f"Branch: [`{branch}`]({tree_url}) · "
        f"[Compare & open PR ↗]({compare_url})"
    )


def _thread_reply_body(event: dict, body: str) -> str:
    """Prepend a quote-style pointer back at the triggering comment.

    GitHub's issue/PR comment endpoint has no first-class "reply to a
    specific comment" primitive for timeline comments. The closest
    visible thread anchor is a blockquote linking to the source comment,
    matching what the GitHub web UI's "Quote reply" button generates.
    Skipped for label-triggered events because the issue itself *is*
    the source — the comment doesn't need to point at it. Inline review
    replies *do* have a first-class reply primitive (handled by the
    ``pulls/{n}/comments/{cid}/replies`` endpoint), but we still
    prepend the pointer there because the review-replies API anchors
    only to the *thread*, not to the specific comment we're replying
    to within it.
    """
    kind = str(event.get("github_kind") or "")
    if kind not in _COMMENT_KINDS:
        return body
    url = str(event.get("github_html_url") or "").strip()
    if not url:
        return body
    author = str(event.get("github_author") or "").strip()
    if author:
        preface = f"> Replying to [@{author}'s comment]({url})\n\n"
    else:
        preface = f"> Replying to [the source comment]({url})\n\n"
    return preface + body


def _post_comment(token: str, event: dict, body: str) -> None:
    """Post one message (interim or terminal) as a GitHub comment.

    Raises on a missing target or API error so the streaming driver
    skips the event and retries it on the next loop (rather than
    silently dropping the message or cleaning up prematurely).
    """
    eid = event["id"]
    repo = event.get("github_repo")
    number = _coerce_int(event.get("github_issue_number"))
    if not repo or number is None:
        raise ValueError("missing repo / issue_number")
    threaded_body = _thread_reply_body(event, body)
    kind = str(event.get("github_kind") or "")
    review_cid = _coerce_int(event.get("github_comment_id"))
    pr_number = _coerce_int(
        event.get("github_pr_number") or event.get("github_issue_number"),
    )
    if kind == "pr-review-comment" and review_cid is not None and pr_number is not None:
        post_path = pull_comment_replies(repo, pr_number, review_cid)
    else:
        post_path = issue_comments(repo, number)
    client._api_post(token, post_path, body={"body": threaded_body})


def _deliver_responses(
    brr_dir: Path,
    inbox_dir: Path,
    responses_dir: Path,
    token: str,
) -> None:
    def deliver_partial(event: dict, body: str) -> None:
        _post_comment(token, event, body)

    def deliver_terminal(event: dict, body: str) -> None:
        # The branch footer (committed SHA + compare link) is the
        # thread's closing context, so it rides only the terminal reply.
        repo = event.get("github_repo")
        task = _find_task_for_event(brr_dir, event["id"])
        if repo and task is not None:
            footer = _branch_footer(repo, task)
            if footer:
                body = body.rstrip() + footer + "\n"
        _post_comment(token, event, body)

    runtime.deliver_stream(
        inbox_dir, responses_dir, "github", deliver_partial, deliver_terminal,
    )

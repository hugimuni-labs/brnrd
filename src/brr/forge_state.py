"""Forge-state facet for the wake snapshot (co-maintainer §5, issue #113).

A co-maintainer should see the project the way a human peer does: its own
in-flight branches and worktrees, what local work is still unpushed, and
which issues/PRs are in play across its conversations. This module builds
that facet for the wake-time communication snapshot.

It is deliberately **network-free**, like :mod:`brr.forges`: it reads local
git worktree state and parses conversation keys into clickable forge
cross-references, but never calls a forge API. Live PR/issue status
(open/closed/merged, behind-base, CI) needs a token-bearing request on the
hot wake path and is the input to *forge grooming* (issue #117), not this
snapshot facet — see ``kb/design-co-maintainer.md`` §5.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import forges, worktree


def parse_forge_thread(key: str) -> tuple[str, int] | None:
    """Parse a conversation key into ``(repo_path, number)``, or ``None``.

    Two key shapes carry a GitHub issue/PR thread:

    - native gate:  ``github:<owner>/<repo>:<number>``
    - cloud relay:  ``cloud:github:<owner>/<repo>#<number>:<topic>``

    ``repo_path`` is ``owner/repo``; ``number`` is the issue or PR number.
    Returns ``None`` for any other key (Telegram, Slack, malformed).
    """
    if not key:
        return None
    if key.startswith("github:"):
        # github:<owner>/<repo>:<number>
        rest = key[len("github:"):]
        repo, sep, num = rest.rpartition(":")
        if not sep or "/" not in repo:
            return None
        try:
            return repo, int(num)
        except ValueError:
            return None
    if key.startswith("cloud:github:"):
        # cloud:github:<owner>/<repo>#<number>:<topic>
        rest = key[len("cloud:github:"):]
        chat = rest.split(":", 1)[0]  # drop the topic suffix
        repo, sep, num = chat.partition("#")
        if not sep or "/" not in repo:
            return None
        try:
            return repo, int(num)
        except ValueError:
            return None
    return None


def _resolve_remote(repo_root: Path) -> tuple[str | None, dict[str, str | None]]:
    """Return ``(origin_remote_url, forge_overrides)`` tolerantly.

    Any failure (no remote, unreadable config) collapses to ``(None, {})``
    so the caller can still emit the worktree half of the facet.
    """
    from . import config as conf
    from . import gitops

    try:
        remote = gitops.default_remote(repo_root) or "origin"
        url = gitops.remote_url(repo_root, remote)
        cfg = conf.load_config(repo_root)
        overrides = {
            "override_kind": cfg.get("forge.kind") or None,
            "override_url_base": cfg.get("forge.url_base") or None,
        }
        return url, overrides
    except Exception:
        return None, {}


def _worktrees_facet(
    repo_root: Path,
    *,
    current_task_id: str,
    remote_url: str | None,
    overrides: dict[str, Any],
) -> list[dict[str, Any]]:
    """Enumerate brr worktrees with branch, unpushed, and dirty state."""
    try:
        infos = worktree.list_worktrees(repo_root)
    except Exception:
        return []

    out: list[dict[str, Any]] = []
    for info in infos:
        entry: dict[str, Any] = {
            "task_id": info.task_id,
            "branch": info.branch,
            "current": bool(current_task_id) and info.task_id == current_task_id,
        }
        try:
            entry["unpushed"] = worktree.unpushed_commit_count(info.path)
        except Exception:
            entry["unpushed"] = 0
        try:
            entry["dirty"] = worktree.has_uncommitted_changes(info.path)
        except Exception:
            entry["dirty"] = False
        if remote_url and info.branch:
            url = forges.view_branch_url(remote_url, info.branch, **overrides)
            if url:
                entry["branch_url"] = url
        out.append(entry)
    return out


def _threads_facet(
    related_threads: list[dict[str, Any]] | None,
    *,
    current_thread: str,
    remote_url: str | None,
    overrides: dict[str, Any],
    current_event_meta: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Cross-reference forge threads in play into ``repo``/``number``/``url``.

    Only threads whose conversation key resolves to a GitHub issue/PR are
    included. The thread that woke this run is enriched with the live event
    metadata (issue-vs-PR kind, the branch it targets, the exact comment
    URL) that PR #106 threads through, when present.
    """
    keys: list[str] = []
    if current_thread:
        keys.append(current_thread)
    for thread in related_threads or []:
        if not isinstance(thread, dict):
            continue
        key = str(thread.get("conversation_key") or "").strip()
        if key and key not in keys:
            keys.append(key)

    out: list[dict[str, Any]] = []
    for key in keys:
        parsed = parse_forge_thread(key)
        if parsed is None:
            continue
        repo, number = parsed
        entry: dict[str, Any] = {
            "conversation_key": key,
            "repo": repo,
            "number": number,
            "current": key == current_thread,
        }
        if remote_url:
            url = forges.thread_url(remote_url, repo, number, **overrides)
            if url:
                entry["url"] = url
        if key == current_thread and current_event_meta:
            _enrich_current(entry, current_event_meta)
        out.append(entry)
    return out


def _enrich_current(entry: dict[str, Any], meta: dict[str, Any]) -> None:
    """Fold live event metadata onto the current thread's forge entry."""
    kind = str(meta.get("github_kind") or "").strip()
    if kind:
        entry["kind"] = kind
    branch_target = str(meta.get("branch_target") or "").strip()
    if branch_target:
        entry["branch_target"] = branch_target
    pr_number = str(meta.get("github_pr_number") or "").strip()
    if pr_number:
        entry["pr_number"] = pr_number
    # Prefer the exact comment/issue URL the forge handed us over the
    # template-derived one — it deep-links to the actual comment.
    html_url = str(meta.get("github_html_url") or "").strip()
    if html_url:
        entry["url"] = html_url


def _unpushed_commits(worktree_entry: dict[str, Any]) -> int:
    unpushed = worktree_entry.get("unpushed", 0)
    if isinstance(unpushed, int) and unpushed > 0:
        return unpushed
    return 0


def summarize_worktrees(worktrees: Any) -> dict[str, Any]:
    """Summarize worktree facet entries for compact wake rendering.

    The wake prompt should keep attention on branches that need action:
    this run, uncommitted work, or unpushed commits. Clean pushed branches
    still matter as inventory, but listing every one makes the forge facet
    a firehose.
    """
    if not isinstance(worktrees, list):
        return {
            "total": 0,
            "dirty_branches": 0,
            "unpushed_branches": 0,
            "unpushed_commits": 0,
            "current_branches": 0,
            "attention": [],
            "omitted": 0,
        }

    entries = [wt for wt in worktrees if isinstance(wt, dict)]
    attention = [
        wt for wt in entries
        if wt.get("current") or wt.get("dirty") or _unpushed_commits(wt) > 0
    ]
    unpushed_commits = sum(_unpushed_commits(wt) for wt in entries)
    return {
        "total": len(entries),
        "dirty_branches": sum(1 for wt in entries if wt.get("dirty")),
        "unpushed_branches": sum(1 for wt in entries if _unpushed_commits(wt) > 0),
        "unpushed_commits": unpushed_commits,
        "current_branches": sum(1 for wt in entries if wt.get("current")),
        "attention": attention,
        "omitted": len(entries) - len(attention),
    }


def build_forge_state(
    repo_root: Path,
    *,
    related_threads: list[dict[str, Any]] | None = None,
    current_thread: str = "",
    current_task_id: str = "",
    current_event_meta: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build the forge-state facet, or ``None`` when there is nothing to show.

    Combines two network-free views:

    - ``worktrees`` — the resident's brr worktrees, each with its branch,
      unpushed-commit count, dirty flag, and a forge branch URL.
    - ``threads`` — the GitHub issues/PRs in play across the current and
      sibling conversation threads, as clickable cross-references.

    Returns ``None`` when both are empty so the snapshot can omit the
    section entirely rather than render a hollow header.
    """
    remote_url, overrides = _resolve_remote(repo_root)
    worktrees = _worktrees_facet(
        repo_root,
        current_task_id=current_task_id,
        remote_url=remote_url,
        overrides=overrides,
    )
    threads = _threads_facet(
        related_threads,
        current_thread=current_thread,
        remote_url=remote_url,
        overrides=overrides,
        current_event_meta=current_event_meta,
    )
    if not worktrees and not threads:
        return None
    facet: dict[str, Any] = {}
    if worktrees:
        facet["worktrees"] = worktrees
    if threads:
        facet["threads"] = threads
    return facet

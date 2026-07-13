"""Forge-state facet for the wake snapshot (co-maintainer §5, issue #113).

A co-maintainer should see the project the way a human peer does: its own
in-flight branches and worktrees, what local work is still unpushed, and
which issues/PRs are in play across its conversations. This module builds
that facet for the wake-time communication snapshot.

It is deliberately **network-free**: it reads local git worktree state, parses
conversation keys into clickable forge cross-references, and *reads* the local
PR-state cache (:mod:`brr.forge_pr_cache`, refreshed on the daemon's own tick) —
but it never calls a forge API itself. That constraint is load-bearing: this
facet is built on the hot wake path, and the block it renders promises
"network-free" in its own name.

PR state rides along because a boot context that lists branches with *no* PR
state cannot contradict a resident's remembered claim ("#373 still awaits the
maintainer") — and on 2026-07-13 that memory was wrong twice, once 3.5h after
the PR had merged. Perception beats a warning: render the state beside the
branch and the stale claim dies at read time.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import forge_pr_cache, forges, gitops, worktree
from .run import Run

# A PR merged/closed within this window is still worth a line even on an
# otherwise-quiet branch: it is exactly the PR a live conversation may still be
# claiming as open. Older resolutions have aged out of the conversation and
# would only bloat a block that rides in every wake.
PR_RESOLVED_WINDOW_SECONDS = 24 * 3600


def _run_has_new_commit(repo_root: Path, run_id: str) -> bool:
    """Read the finalized commit verdict cached on a run manifest."""
    task = Run.from_file(
        gitops.shared_brr_dir(repo_root) / "runs" / run_id / "run.md"
    )
    return bool(task and task.meta.get("has_new_commit") is True)


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
    current_run_id: str,
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
            "run_id": info.run_id,
            "branch": info.branch,
            "current": bool(current_run_id) and info.run_id == current_run_id,
        }
        try:
            entry["unpushed"] = worktree.unpushed_commit_count(info.path)
        except Exception:
            entry["unpushed"] = 0
        try:
            entry["dirty"] = worktree.has_uncommitted_changes(info.path)
        except Exception:
            entry["dirty"] = False
        if remote_url and info.branch and _run_has_new_commit(repo_root, info.run_id):
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


def _pr_state_facet(
    repo_root: Path,
    worktrees: list[dict[str, Any]],
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """Read the PR-state cache and fold it onto the worktree entries.

    Pure read — no subprocess, no network (see the module docstring). Returns
    the cache's own verdict (``status`` / ``age_seconds`` / ``error``) plus
    ``standalone``: the PRs worth a line that have **no** local worktree to
    hang off. That list carries the feature on this host: a spawned child's
    worktree is pruned when its run finalizes, so the branch behind a just-
    merged PR (#382 → ``brr/boot-score-slice1``) is typically gone from the
    local worktree list while the conversation about it is still live. Open PRs
    *and* recently-resolved ones both belong there — a merged PR nobody has
    noticed yet is exactly the claim this block exists to kill.
    """
    state = forge_pr_cache.read_state(repo_root, now=now)
    prs = state.get("prs")
    if not isinstance(prs, list):
        # absent / error: unknown, and unknown is not "no PRs".
        return {
            "status": state.get("status") or "absent",
            "fetched_at": state.get("fetched_at"),
            "age_seconds": state.get("age_seconds"),
            "error": state.get("error"),
            "has_rows": False,
            "standalone": [],
        }

    by_branch: dict[str, dict[str, Any]] = {}
    for pr in prs:
        branch = str(pr.get("branch") or "").strip()
        if not branch:
            continue
        current = by_branch.get(branch)
        # Newest PR (highest number) wins when a branch has been re-used.
        if current is None or int(pr.get("number") or 0) > int(current.get("number") or 0):
            by_branch[branch] = pr

    local_branches = set()
    for entry in worktrees:
        branch = str(entry.get("branch") or "").strip()
        if not branch:
            continue
        local_branches.add(branch)
        pr = by_branch.get(branch)
        if pr:
            entry["pr"] = pr

    standalone = [
        pr
        for branch, pr in by_branch.items()
        if branch not in local_branches and _pr_worth_a_line(pr, now=now)
    ]
    standalone.sort(key=lambda pr: int(pr.get("number") or 0), reverse=True)
    return {
        "status": state.get("status") or "fresh",
        "fetched_at": state.get("fetched_at"),
        "age_seconds": state.get("age_seconds"),
        "error": state.get("error"),
        # Whether *any* row is being shown (standalone, or folded onto a
        # worktree entry).  A failed refresh that is still displaying kept rows
        # must say so with their age — see :func:`pr_state_note`.
        "has_rows": bool(by_branch),
        "standalone": standalone,
    }


def _pr_worth_a_line(pr: dict[str, Any], *, now: float | None = None) -> bool:
    """Open, or resolved recently enough that a live claim could still be wrong."""
    if str(pr.get("state") or "").upper() == "OPEN":
        return True
    return _resolved_recently(pr, now=now)


def _resolved_recently(pr: dict[str, Any], *, now: float | None = None) -> bool:
    """Did this PR merge/close inside :data:`PR_RESOLVED_WINDOW_SECONDS`?"""
    stamp = pr.get("merged_at") or pr.get("closed_at")
    epoch = forge_pr_cache.parse_iso(stamp)
    if epoch is None:
        return False
    age = (time.time() if now is None else now) - epoch
    return 0 <= age < PR_RESOLVED_WINDOW_SECONDS


def pr_needs_attention(worktree_entry: dict[str, Any], *, now: float | None = None) -> bool:
    """Does this branch's PR state itself earn a line in the wake block?

    Open PRs always (they are the live queue). Merged/closed ones only while
    recent — that is the window where a resident's memory can still be wrong
    about them, which is the whole reason this state is rendered.
    """
    pr = worktree_entry.get("pr")
    if not isinstance(pr, dict):
        return False
    return _pr_worth_a_line(pr, now=now)


def format_pr(pr: dict[str, Any], *, now: float | None = None) -> str:
    """``#382 MERGED 3h ago`` / ``#390 OPEN (draft)`` — the compact PR marker.

    The resolution age is the load-bearing half for a merged/closed PR: "merged
    3h ago" is what makes a remembered "still awaiting review" visibly wrong.
    """
    if not isinstance(pr, dict):
        return ""
    number = pr.get("number")
    if number is None:
        return ""
    state = str(pr.get("state") or "UNKNOWN").upper()
    text = f"#{number} {state}"
    if pr.get("draft") and state == "OPEN":
        text += " (draft)"
    resolved = forge_pr_cache.parse_iso(pr.get("merged_at") or pr.get("closed_at"))
    if resolved is not None and state in ("MERGED", "CLOSED"):
        age = (time.time() if now is None else now) - resolved
        if age >= 0:
            text += f" {format_age(age)} ago"
    return text


def format_age(seconds: float | None) -> str:
    """``14m`` / ``3h`` / ``2d`` — coarse age, enough to judge staleness by."""
    if seconds is None or seconds < 0:
        return "unknown age"
    if seconds < 90:
        return f"{int(seconds)}s"
    if seconds < 5400:
        return f"{int(seconds // 60)}m"
    if seconds < 172800:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


# This repo merges a dozen PRs on a busy day, and every one inside the 24h
# window is a line in a block that rides in *every* wake — so the tail is
# capped. Not tightly, though: the claim that went wrong on 2026-07-13 was
# about a PR merged 3.5h earlier, which a stingy cap would have pushed into
# the "omitted" line and left invisible. Ten lines (~150 tokens worst case)
# buys a full busy day of resolutions; the open queue is never capped.
STANDALONE_RESOLVED_LIMIT = 10


def standalone_prs(pr_state: Any, *, now: float | None = None) -> tuple[list[dict[str, Any]], int]:
    """``(prs to render, resolved-lines omitted)`` for the PR list.

    Open PRs first (the queue), then the newest resolutions (the antidote),
    capped. Shared by both renderers so the prompt and the context file cannot
    drift into two different truths.
    """
    if not isinstance(pr_state, dict):
        return [], 0
    rows = pr_state.get("standalone")
    if not isinstance(rows, list):
        return [], 0
    prs = [pr for pr in rows if isinstance(pr, dict)]
    open_prs = [pr for pr in prs if str(pr.get("state") or "").upper() == "OPEN"]
    resolved = [pr for pr in prs if str(pr.get("state") or "").upper() != "OPEN"]
    resolved.sort(
        key=lambda pr: forge_pr_cache.parse_iso(
            pr.get("merged_at") or pr.get("closed_at")
        ) or 0.0,
        reverse=True,
    )
    shown = resolved[:STANDALONE_RESOLVED_LIMIT]
    return open_prs + shown, len(resolved) - len(shown)


def pr_state_note(pr_state: Any) -> str:
    """One line on the cache's own trustworthiness, or ``""`` when it is fresh.

    ``absent ≠ unknown ≠ none``: no cache and a failed refresh both say
    *unknown* out loud, so nothing reads a silent block as "there are no PRs".
    """
    if not isinstance(pr_state, dict):
        return "PR state: unknown (no local cache)"
    status = str(pr_state.get("status") or "absent")
    if status == "fresh":
        return ""
    if status == "absent":
        return "PR state: unknown (no local cache yet — the daemon refreshes it on its tick)"
    if status == "error":
        error = str(pr_state.get("error") or "").strip() or "refresh failed"
        # A failed refresh may still be showing rows kept from an earlier good
        # fetch.  Then the honest line is not "unknown" — it is *these rows, and
        # this is how old they are, and the refresh that would have corrected
        # them failed*.  Naming the age is what lets a reader judge whether to
        # trust a row; hiding it is how stale data passes for current.
        if pr_state.get("has_rows"):
            age = format_age(pr_state.get("age_seconds"))
            return (
                f"PR state from {age} ago — the refresh since then FAILED "
                f"({error}); treat these rows as possibly out of date"
            )
        return f"PR state: unknown (last refresh failed: {error})"
    age = format_age(pr_state.get("age_seconds"))
    return f"PR state as of {age} ago (stale)"


def _unpushed_commits(worktree_entry: dict[str, Any]) -> int:
    unpushed = worktree_entry.get("unpushed", 0)
    if isinstance(unpushed, int) and unpushed > 0:
        return unpushed
    return 0


def summarize_worktrees(worktrees: Any) -> dict[str, Any]:
    """Summarize worktree facet entries for compact wake rendering.

    The wake prompt should keep attention on branches that need action:
    this run, uncommitted work, unpushed commits — or a PR state worth
    knowing (an open PR, or one merged/closed recently enough that a live
    conversation could still be wrong about it). Clean pushed branches with
    nothing in flight still matter as inventory, but listing every one makes
    the forge facet a firehose.
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
        if wt.get("current")
        or wt.get("dirty")
        or _unpushed_commits(wt) > 0
        or pr_needs_attention(wt)
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
    current_run_id: str = "",
    current_event_meta: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build the forge-state facet, or ``None`` when there is nothing to show.

    Combines three network-free views:

    - ``worktrees`` — the resident's brr worktrees, each with its branch,
      unpushed-commit count, dirty flag, a forge branch URL, and (from the
      cache) the ``pr`` open on that branch.
    - ``threads`` — the GitHub issues/PRs in play across the current and
      sibling conversation threads, as clickable cross-references.
    - ``pr_state`` — the PR-state cache's own verdict (fresh / stale /
      absent / error) plus open PRs whose branch has no local worktree.

    Every one of them is a **read**: the ``gh`` call that fills the PR cache
    belongs to the daemon tick (:mod:`brr.forge_pr_cache`), never to this
    prompt-build path.

    Returns ``None`` when all are empty so the snapshot can omit the
    section entirely rather than render a hollow header.
    """
    remote_url, overrides = _resolve_remote(repo_root)
    worktrees = _worktrees_facet(
        repo_root,
        current_run_id=current_run_id,
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
    pr_state = _pr_state_facet(repo_root, worktrees)
    if not worktrees and not threads:
        return None
    facet: dict[str, Any] = {}
    if worktrees:
        facet["worktrees"] = worktrees
    if threads:
        facet["threads"] = threads
    facet["pr_state"] = pr_state
    return facet

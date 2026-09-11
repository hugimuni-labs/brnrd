"""Network-free detection of brr branches whose owning run has ended."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from . import forge_pr_cache, gitops
from .run import list_runs

TERMINAL_RUN_STATUSES = frozenset({"done", "error", "conflict", "stopped"})
_WARNED: set[str] = set()

#: How long one :func:`warn_new` sweep's verdict stands before the walk runs
#: again. The walk is one ``git cherry`` per local ``brr/*`` branch —
#: **15.3s measured** over 346 branches on this account's own checkout
#: (2026-09-11) — and it runs on the daemon's *main loop thread*, immediately
#: in front of the dispatch scan that decides whether a waiting chat message
#: becomes a run. Every neighbour in that same tick
#: (``forge_pr_cache``, ``lane_liveness``, ``forge_workflow_cache``,
#: ``forge_issue_cache``, ``release_availability``) is already TTL-gated and
#: threaded off the loop for exactly this reason; this one was not, so the
#: loop paid the full walk per tick.
#:
#: The cost bought nothing after the first pass: the *output* is already
#: deduped for the process's lifetime (``_WARNED``), so sweep two onward spent
#: 15s of CPU-bound git to print nothing. A TTL is what makes the spend match
#: the value — a branch that parks is now announced within this window instead
#: of within one tick, which is the right trade for a note nobody acts on in
#: the same minute.
_SWEEP_TTL_SECONDS = 300.0

#: Monotonic stamp of the last completed :func:`warn_new` sweep, or ``None``
#: when this process has never swept. Deliberately monotonic, not wall-clock:
#: a TTL that reads ``time.time()`` skips or repeats a sweep when the host
#: clock steps (a suspend/resume, an NTP correction), and this daemon runs
#: across laptop sleeps every day.
_last_sweep_at: float | None = None


@dataclass(frozen=True)
class ParkedBranch:
    """A branch holding work ``main`` does not have, with nobody left on it.

    ``commits`` counts **unmerged** commits (patch-id, via
    :func:`gitops.unmerged_commit_count`), not commits reachable from the
    branch and not from the default. The two come apart on every rebase-merge,
    squash, and cherry-pick, and the reachability form never converges back —
    which is why the surface's first real day listed four branches that held
    nothing (#1544).
    """

    name: str
    commits: int
    updated_at: float | None


def _live_branches(repo_root: Path) -> set[str]:
    runs_dir = gitops.shared_brr_dir(repo_root) / "runs"
    if not runs_dir.is_dir():
        return set()
    branches: set[str] = set()
    for run in list_runs(runs_dir):
        if run.status in TERMINAL_RUN_STATUSES:
            continue
        branch = str(
            run.meta.get("branch_name") or run.meta.get("publish_branch") or ""
        ).strip()
        if branch:
            branches.add(branch)
    return branches


def detect(repo_root: Path) -> list[ParkedBranch]:
    """Return ahead, PR-less, unowned local ``brr/*`` branches.

    PR state comes from the daemon-warmed forge cache. Unknown PR state is
    deliberately fail-closed: absence of evidence must not become a false
    claim that a branch has no PR.

    "Ahead" is measured by patch id, not by reachability (#1544). A branch
    whose every commit already has an equivalent on the default branch is not
    parked work — it is a leftover ref — and listing it costs the reader the
    only expensive thing here: reading a diff to find out it was already
    merged.
    """
    default = gitops.default_branch(repo_root)
    if not default:
        return []
    state = forge_pr_cache.read_state(repo_root)
    prs = state.get("prs")
    if not isinstance(prs, list):
        return []
    open_heads = {
        str(row.get("branch") or "").strip()
        for row in prs
        if isinstance(row, dict) and str(row.get("state") or "").upper() == "OPEN"
    }
    live = _live_branches(repo_root)
    parked: list[ParkedBranch] = []
    for name, updated_at in gitops.branches_with_commit_times(repo_root, "brr"):
        if not name or name in live or name in open_heads:
            continue
        commits = gitops.unmerged_commit_count(repo_root, default, name)
        if commits is None or commits <= 0:
            continue
        parked.append(ParkedBranch(name, commits, updated_at))
    return sorted(parked, key=lambda item: item.name)


def _age(timestamp: float | None, *, now: float | None = None) -> str:
    if timestamp is None:
        return "age unknown"
    seconds = max(0, int((time.time() if now is None else now) - timestamp))
    if seconds < 3600:
        return f"{max(1, seconds // 60)}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def render(items: list[ParkedBranch], *, now: float | None = None) -> str | None:
    if not items:
        return None
    rows = []
    for item in items:
        noun = "commit" if item.commits == 1 else "commits"
        rows.append(
            f"{item.name} ({item.commits} unmerged {noun}, "
            f"pushed {_age(item.updated_at, now=now)})"
        )
    return "parked branches: " + " · ".join(rows)


def warn_new(
    repo_root: Path,
    *,
    ttl: float = _SWEEP_TTL_SECONDS,
    now: float | None = None,
) -> None:
    """Emit the daemon's ergo warning once per branch per process lifetime.

    Rate-limited to one sweep per *ttl* seconds (:data:`_SWEEP_TTL_SECONDS`),
    because the sweep itself is the expensive part and the daemon calls this
    every main-loop tick, right before dispatch. *now* is a monotonic reading,
    injectable for tests; ``None`` reads :func:`time.monotonic`.
    """
    global _last_sweep_at
    stamp = time.monotonic() if now is None else now
    if _last_sweep_at is not None and stamp - _last_sweep_at < ttl:
        return
    # Stamped before the walk, not after: a sweep that raises (a git failure,
    # a cache read error) must not become a retry-every-tick loop — the caller
    # in `daemon.py` swallows the exception and would otherwise arrive back
    # here, unthrottled, in a few seconds.
    _last_sweep_at = stamp
    for item in detect(repo_root):
        if item.name in _WARNED:
            continue
        _WARNED.add(item.name)
        print(
            f"[brnrd:ergo] warn parked_branch [daemon] — "
            f"{item.name} holds {item.commits} unmerged commit(s) with no "
            "open PR and no live run"
        )

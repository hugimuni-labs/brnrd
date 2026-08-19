"""Network-free detection of brr branches whose owning run has ended."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from . import forge_pr_cache, gitops
from .run import list_runs

TERMINAL_RUN_STATUSES = frozenset({"done", "error", "conflict", "stopped"})
_WARNED: set[str] = set()


@dataclass(frozen=True)
class ParkedBranch:
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
        commits = gitops.ahead_count(repo_root, default, name)
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
            f"{item.name} ({item.commits} {noun}, "
            f"pushed {_age(item.updated_at, now=now)})"
        )
    return "parked branches: " + " · ".join(rows)


def warn_new(repo_root: Path) -> None:
    """Emit the daemon's ergo warning once per branch per process lifetime."""
    for item in detect(repo_root):
        if item.name in _WARNED:
            continue
        _WARNED.add(item.name)
        print(
            f"[brnrd:ergo] warn parked_branch [daemon] — "
            f"{item.name} is {item.commits} commit(s) ahead with no open PR "
            "and no live run"
        )

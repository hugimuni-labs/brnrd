"""Network-free detection of brr branches whose owning run has ended."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import forge_pr_cache, gitops
from .run import TERMINAL_STATUSES as RUN_TERMINAL_STATUSES, list_runs

TERMINAL_RUN_STATUSES = RUN_TERMINAL_STATUSES
_WARNED: set[str] = set()

#: How long one sweep's verdict stands before the walk runs again. The walk
#: is one ``git cherry`` per local ``brr/*`` branch — **15.3s measured** over
#: 346 branches on this account's own checkout (2026-09-11), **130.9s
#: measured over 453 branches** on the same checkout (2026-09-22): branch
#: count grew 1.3x, walk time grew 8.5x, because ``gitops
#: .unmerged_commit_count``'s per-branch patch-id diff does not scale
#: linearly against a repo whose history keeps growing underneath it.
#:
#: The cost bought nothing after the first pass: the *output* is already
#: deduped for the process's lifetime (``_WARNED``), so sweep two onward spent
#: the whole walk to print nothing. A TTL is what makes the spend match
#: the value — a branch that parks is now announced within this window instead
#: of within one tick, which is the right trade for a note nobody acts on in
#: the same minute.
_SWEEP_TTL_SECONDS = 300.0

#: Monotonic stamp of the last completed sweep (successful or not — see
#: :func:`refresh_if_stale_async`), or ``None`` when this process has never
#: swept. Deliberately monotonic, not wall-clock: a TTL that reads
#: ``time.time()`` skips or repeats a sweep when the host clock steps (a
#: suspend/resume, an NTP correction), and this daemon runs across laptop
#: sleeps every day.
_cached_at: float | None = None

#: The last completed walk's result. Empty until the first sweep lands —
#: a cold cache renders no ``parked branches:`` line and warns of nothing for
#: up to one TTL window after a fresh daemon boot, which is the honest cost
#: of never blocking a caller on the walk (see :func:`read_cached`).
_cached_items: list["ParkedBranch"] = []

#: Guards ``_cached_at`` / ``_cached_items`` and the "already refreshing"
#: check in :func:`refresh_if_stale_async` — the same shape as
#: ``forge_pr_cache._refresh_lock`` / ``lane_liveness``'s own, so a reader of
#: one of those modules already knows how to read this one.
_cache_lock = threading.Lock()
_refreshing = False


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


def refresh_if_stale_async(
    repo_root: Path,
    *,
    ttl: float = _SWEEP_TTL_SECONDS,
    now: float | None = None,
) -> bool:
    """Refresh the cached parked-branch list on a daemon thread if it's stale.

    Never blocks the caller. The walk this refreshes (:func:`detect`) measured
    **130.9s over 453 branches** on this account's own checkout (2026-09-22)
    — up from 15.3s over 346 branches on 2026-09-11 (see the module-level
    docstring above :data:`_SWEEP_TTL_SECONDS`). Two callers used to run that
    walk inline on their own thread: the daemon's main-loop tick (via
    :func:`warn_new`) and, with **no TTL at all**, every dispatched run's own
    boot-prompt assembly (``prompts.py``'s ``Run Context Bundle``, via
    :func:`read_cached`) — the second one is what actually reproduces "the
    daemon's tick fell to minutes": every strand spawned during a busy window
    paid the full walk before its wake prompt was even finished. Both now only
    ever read the cache; this function is the only thing that still calls
    :func:`detect`, same shape as ``forge_pr_cache.refresh_if_stale_async`` /
    ``lane_liveness``'s own.

    Returns whether a refresh thread was actually started — ``False`` when one
    is already in flight, or the cache is still within *ttl*.
    """
    global _refreshing, _cached_at
    stamp = time.monotonic() if now is None else now
    with _cache_lock:
        if _refreshing:
            return False
        if _cached_at is not None and stamp - _cached_at < ttl:
            return False
        # Stamped before the walk, not after: a sweep that raises (a git
        # failure, a cache read error) must not become a retry-every-tick
        # loop — mirrors the pre-async gate's own reasoning, now guarding a
        # background thread instead of the caller.
        _cached_at = stamp
        _refreshing = True

    def _work() -> None:
        global _refreshing, _cached_items
        try:
            items = detect(repo_root)
            with _cache_lock:
                _cached_items = items
        except Exception as exc:  # noqa: BLE001 - a background sweep must never surface here
            print(f"[brnrd] parked-branch sweep failed (ignored): {exc}")
        finally:
            with _cache_lock:
                _refreshing = False

    threading.Thread(target=_work, name="parked-branches", daemon=True).start()
    return True


def read_cached(
    repo_root: Path,
    *,
    ttl: float = _SWEEP_TTL_SECONDS,
    now: float | None = None,
) -> list[ParkedBranch]:
    """The last-computed parked-branch list — never walks branches here.

    Triggers :func:`refresh_if_stale_async` as a side effect (so the cache
    keeps itself warm across callers) and returns whatever is cached *right
    now*: empty on a cold cache (a fresh daemon process, or the first *ttl*
    window of one), up to *ttl* old otherwise. This is the call every hot
    path wants — ``prompts.py``'s boot-time render and :func:`warn_new`'s own
    sweep both read this instead of calling :func:`detect` directly.
    """
    refresh_if_stale_async(repo_root, ttl=ttl, now=now)
    with _cache_lock:
        return list(_cached_items)


def warn_new(
    repo_root: Path,
    *,
    ttl: float = _SWEEP_TTL_SECONDS,
    now: float | None = None,
) -> None:
    """Emit the daemon's ergo warning once per branch per process lifetime.

    Reads :func:`read_cached` — never walks branches on the caller's own
    thread. A cold or stale cache simply has nothing new to warn about on
    this tick; the background refresh it triggers catches up within *ttl*.
    """
    for item in read_cached(repo_root, ttl=ttl, now=now):
        if item.name in _WARNED:
            continue
        _WARNED.add(item.name)
        print(
            f"[brnrd:ergo] warn parked_branch [daemon] — "
            f"{item.name} holds {item.commits} unmerged commit(s) with no "
            "open PR and no live run"
        )

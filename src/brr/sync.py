"""Daemon freshness — fetch and best-effort fast-forward target branches.

Called by the daemon just before resolving a branch plan for a new task,
so the task seeds from a current view of the world instead of whatever
the host last pulled. Without this hook, every brr-produced branch
merged on the remote leaves the daemon's local default branch stale,
and subsequent tasks silently start from old code.

The contract is deliberately small:

- One ``git fetch <remote>`` per call when a remote is configured.
- For each named target branch, attempt ``fast_forward_branch`` against
  ``<remote>/<branch>``. ff-only is safe (refuses non-fast-forward and
  dirty-checkout cases), so failures are recorded and the daemon
  proceeds against current local refs.
- After the targeted ff's, sweep every other local branch that has a
  matching ``<remote>/<branch>`` tracking ref and try the same ff
  best-effort. This keeps branches the agent learns about from free
  text (Telegram, Slack) safe to ``git switch`` into without the agent
  having to remember to use ``origin/<branch>`` explicitly. Failures on
  sweep-discovered branches are **silent** (not recorded in
  ``SyncResult.skipped``) — abandoned branches that diverged ages ago
  should not fill the progress card.
- Never raises. Any unexpected exception is captured in
  ``SyncResult.error`` so a fetch failure cannot block task execution.

Three opt-out config knobs in ``.brr/config``:

- ``sync.fetch_before_task=false`` — skip the network entirely.
- ``sync.fast_forward_default=false`` — fetch but do not advance local
  refs (for users sharing the daemon's checkout with active dev work).
- ``sync.fast_forward_all=false`` — fetch and ff the explicit targets,
  but skip the sweep over other local branches.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import gitops


# ── Result ───────────────────────────────────────────────────────────


@dataclass
class SyncResult:
    """Outcome of a single ``refresh_before_task`` call.

    *fetched* tells whether a network fetch was attempted (false when
    no remote is configured or the operator disabled the knob).
    *ff_branches* maps each branch advanced by this call to its new
    OID. *skipped* maps branches we tried to advance but couldn't to
    a short human-readable reason (dirty tree, diverged history,
    missing remote ref, opt-out, etc.). *error* is set only when
    something unexpected blew up — the daemon treats that as a
    soft failure and continues.
    """

    fetched: bool = False
    ff_branches: dict[str, str] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    error: str | None = None

    def is_noop(self) -> bool:
        """True when nothing meaningful happened (no ff, no skips, no error)."""
        return not self.ff_branches and not self.skipped and self.error is None


# ── Config ───────────────────────────────────────────────────────────


def _bool(cfg: dict[str, Any], key: str, default: bool) -> bool:
    """Read a bool from *cfg*, accepting bool / int / str shapes."""
    if key not in cfg:
        return default
    raw = cfg[key]
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int):
        return raw != 0
    if isinstance(raw, str):
        return raw.strip().lower() not in {"", "false", "0", "no", "off"}
    return default


def fetch_enabled(cfg: dict[str, Any]) -> bool:
    """Whether ``refresh_before_task`` should perform a network fetch."""
    return _bool(cfg, "sync.fetch_before_task", True)


def fast_forward_enabled(cfg: dict[str, Any]) -> bool:
    """Whether ``refresh_before_task`` may advance local target branches."""
    return _bool(cfg, "sync.fast_forward_default", True)


def fast_forward_all_enabled(cfg: dict[str, Any]) -> bool:
    """Whether the sweep over non-target local branches is allowed.

    When False, only the explicit ``target_branches`` are considered for
    fast-forward; behaviour matches pre-sweep brr. Gated separately from
    ``fast_forward_default`` so an operator can keep targeted ff's on
    while opting out of the broader sweep (and vice versa).
    """
    return _bool(cfg, "sync.fast_forward_all", True)


# ── Public entry point ───────────────────────────────────────────────


def refresh_before_task(
    repo_root: Path,
    *,
    target_branches: list[str],
    cfg: dict[str, Any] | None = None,
) -> SyncResult:
    """Fetch the default remote and best-effort fast-forward target branches.

    *target_branches* is the list of local branch names the daemon
    intends to seed from. Duplicate names and empty entries are
    filtered out; branches that don't exist locally yet are recorded
    as skipped (the daemon doesn't try to invent them here).

    Returns a populated ``SyncResult``. Never raises; any exception is
    captured in ``SyncResult.error`` so the caller can carry on.
    """
    cfg = cfg or {}
    result = SyncResult()
    branches = _dedupe(target_branches)

    try:
        remote = gitops.default_remote(repo_root)
        if not remote:
            for branch in branches:
                result.skipped[branch] = "no remote configured"
            return result

        if fetch_enabled(cfg):
            result.fetched = _fetch(repo_root, remote, result)
        else:
            for branch in branches:
                result.skipped[branch] = "fetch disabled (sync.fetch_before_task=false)"
            return result

        if not fast_forward_enabled(cfg):
            for branch in branches:
                result.skipped[branch] = "ff disabled (sync.fast_forward_default=false)"
            return result

        explicit = set(branches)
        for branch in branches:
            _try_fast_forward(repo_root, remote, branch, result)

        if fast_forward_all_enabled(cfg):
            for branch in _sweep_candidates(repo_root, remote, explicit):
                _try_fast_forward(
                    repo_root, remote, branch, result, silent_on_skip=True,
                )
    except Exception as exc:  # pragma: no cover - defensive
        result.error = f"{type(exc).__name__}: {exc}"

    return result


# ── Internals ────────────────────────────────────────────────────────


def _dedupe(branches: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for branch in branches:
        if not branch:
            continue
        name = branch.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def _fetch(repo_root: Path, remote: str, result: SyncResult) -> bool:
    """Run ``git fetch <remote>``. Returns True on success."""
    try:
        proc = subprocess.run(
            ["git", "fetch", "--quiet", remote],
            cwd=repo_root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        result.error = f"git fetch {remote}: timed out"
        return False
    if proc.returncode != 0:
        # Don't surface as a hard error: a fetch failure is normal in
        # offline / network-flaky environments. Skip every target with
        # the underlying reason so the operator can debug if needed.
        reason = (proc.stderr.strip() or proc.stdout.strip() or "fetch failed").splitlines()[0]
        result.error = f"git fetch {remote}: {reason}"
        return False
    return True


def _try_fast_forward(
    repo_root: Path,
    remote: str,
    branch: str,
    result: SyncResult,
    *,
    silent_on_skip: bool = False,
) -> None:
    """Best-effort ff of *branch* to ``<remote>/<branch>``. Records outcome.

    With ``silent_on_skip=True``, failures (missing local, missing remote
    ref, refused ff) don't populate ``result.skipped``. Used by the sweep
    over non-target branches: abandoned branches that have diverged
    forever should not pollute the progress card. Successful ff's are
    still recorded regardless — advancing a branch is the useful signal.
    """
    if not gitops.branch_exists(repo_root, branch):
        if not silent_on_skip:
            result.skipped[branch] = "branch does not exist locally"
        return

    remote_ref = f"{remote}/{branch}"
    if gitops.rev_parse(repo_root, remote_ref) is None:
        if not silent_on_skip:
            result.skipped[branch] = f"no remote ref {remote_ref}"
        return

    old_oid = gitops.branch_head(repo_root, branch)
    update = gitops.fast_forward_branch(repo_root, branch, remote_ref)
    if not update.success:
        if not silent_on_skip:
            result.skipped[branch] = update.detail or "fast-forward refused"
        return

    new_oid = update.commit or gitops.branch_head(repo_root, branch) or ""
    if not new_oid or new_oid == old_oid:
        # Already up to date — not worth surfacing.
        return
    result.ff_branches[branch] = new_oid


def _sweep_candidates(
    repo_root: Path,
    remote: str,
    explicit: set[str],
) -> list[str]:
    """Local branches eligible for the sweep ff pass.

    Eligible = exists locally, has a matching ``<remote>/<branch>``
    tracking ref, and wasn't already covered as an explicit target.
    Filtering by tracking-ref existence here cheaply prunes the noise
    cases (purely local branches, fork branches against a different
    remote name) so ``_try_fast_forward`` doesn't have to deal with
    them.
    """
    out: list[str] = []
    for branch in gitops.list_local_branches(repo_root):
        if branch in explicit:
            continue
        if gitops.rev_parse(repo_root, f"{remote}/{branch}") is None:
            continue
        out.append(branch)
    return out


# ── Rendering ────────────────────────────────────────────────────────


def render_summary(result: SyncResult) -> str:
    """Short one-line summary suitable for progress packets / console.

    Returns an empty string when nothing meaningful happened, so callers
    can suppress "synced: " noise on the common no-op path.
    """
    if result.is_noop():
        return ""
    bits: list[str] = []
    for branch, oid in sorted(result.ff_branches.items()):
        bits.append(f"ff {branch} -> {oid[:7]}")
    for branch, reason in sorted(result.skipped.items()):
        bits.append(f"skipped {branch} ({reason})")
    if result.error:
        bits.append(f"error: {result.error}")
    return ", ".join(bits)

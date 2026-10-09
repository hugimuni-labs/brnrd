"""Organ adapter: worktree placement for strand runs.

A strand gets its own isolated git clone so its branch and HEAD are decoupled
from the host checkout. This module wraps the git/worktree organs behind a
simple allocate/release/land interface, keeping the caller free of the organ
signatures. If an organ's signature forces an old shape, note it here.

Organ wrapping notes
--------------------
- ``worktree.create_clone`` takes *(repo_root, run_id, base_ref="HEAD")* and
  returns *(path, branch_name)*. The run_id determines both the worktree path
  and the branch name (``brr/<run_id>``). That coupling is the organ's own
  convention; we surface it through the allocation record.
- ``worktree.remove_clone`` removes the worktree directory; the organ handles
  the git bookkeeping.
- ``worktree.land_clone_branch`` merges the clone's branch back after the
  strand finishes. Currently wraps as-is; see ``BranchUpdateResult`` from
  ``gitops`` for the return type.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .. import gitops, worktree


@dataclass(frozen=True)
class Allocation:
    """A worktree clone created for one strand run."""
    path: Path
    branch: str
    run_id: str
    #: The commit the clone sprouted from. ``publish`` compares the branch
    #: tip against it so a strand with no commits of its own publishes
    #: nothing (#2236).
    seed_oid: str = ""

    def env(self) -> dict[str, str]:
        """Environment variables that pin git commands to this worktree."""
        git_dir = self.path / ".git"
        return {
            "GIT_DIR": str(git_dir),
            "GIT_WORK_TREE": str(self.path),
        }


class PlacementError(RuntimeError):
    pass


@dataclass(frozen=True)
class Publication:
    branch: str
    landed: bool
    pushed: bool
    released: bool
    detail: str = ""
    #: True when the branch carried no commits past the seed, so nothing was
    #: landed or pushed on purpose (not a failure to retain the clone for).
    empty: bool = False


def strand_seed(repo_root: Path) -> str:
    """The ref a strand sprouts from: ``<remote>/<default>``, freshly fetched.

    Never the host checkout's ``HEAD`` or local default branch (#2236): the
    host is a checkout the operator also works in, and its local ``main`` may
    carry commits he has not pushed. A strand seeded there publishes them on
    its own run branch. Falls back to ``HEAD`` only when there is no remote
    tracking ref at all, in which case nothing leaves the machine anyway.
    """
    remote = gitops.default_remote(repo_root)
    default = gitops.default_branch(repo_root)
    if remote and default and default != "HEAD":
        from ..runner import clean_runner_environ
        try:
            env = clean_runner_environ()
        except Exception:  # noqa: BLE001 — a token refresh must not block a seed
            env = None
        gitops.fetch_branch(repo_root, remote, default, env=env)
        remote_ref = f"{remote}/{default}"
        if gitops.rev_parse(repo_root, remote_ref):
            return remote_ref
    return "HEAD"


def allocate(repo_root: Path, run_id: str, *, base_ref: str | None = None) -> Allocation:
    """Create a fresh clone worktree for *run_id* branched from *base_ref*.

    *base_ref* unset ⇒ :func:`strand_seed` (the fetched remote default).

    Raises ``PlacementError`` if the underlying organ fails (no commit at
    *base_ref*, clone directory conflict, git failure).
    """
    if base_ref is None:
        base_ref = strand_seed(repo_root)
    seed_oid = gitops.rev_parse(repo_root, base_ref) or ""
    try:
        path, branch = worktree.create_clone(repo_root, run_id, base_ref=base_ref)
    except Exception as exc:
        raise PlacementError(f"failed to allocate worktree for {run_id}: {exc}") from exc
    _pin_clone_default(repo_root, path, base_ref, seed_oid)
    return Allocation(path=path, branch=branch, run_id=run_id, seed_oid=seed_oid)


def release(allocation: Allocation) -> None:
    """Remove a strand worktree after its run finishes or is abandoned.

    Idempotent: if the path no longer exists the call is a no-op.
    """
    if not allocation.path.exists():
        return
    try:
        worktree.remove_clone(allocation.path)
    except Exception as exc:
        raise PlacementError(
            f"failed to release worktree {allocation.path}: {exc}") from exc


def land(repo_root: Path, allocation: Allocation, *,
         branch: str | None = None) -> gitops.BranchUpdateResult:
    """Merge the strand's branch back into the host checkout.

    Returns the organ's own ``BranchUpdateResult``; callers interpret it
    (fast-forward vs conflict vs already-merged).
    """
    try:
        return worktree.land_clone_branch(repo_root, allocation.path,
                                          branch or allocation.branch)
    except Exception as exc:
        raise PlacementError(
            f"failed to land branch {allocation.branch}: {exc}") from exc


def publish(repo_root: Path, allocation: Allocation) -> Publication:
    """Land the clone's actual HEAD branch before any destructive cleanup.

    A dirty clone or failed land/push stays on disk for salvage. A successful
    local land is sufficient when the checkout has no remote.
    """
    branch = worktree.current_branch(allocation.path)
    if not branch:
        return Publication("", False, False, False, "clone has no branch")
    dirty = worktree.has_uncommitted_changes(allocation.path)
    if not dirty and _no_commits_past_seed(allocation):
        release(allocation)
        return Publication(branch, False, False, True,
                           "no commits past the seed; nothing published", empty=True)
    result = land(repo_root, allocation, branch=branch)
    if not result.success:
        return Publication(branch, False, False, False, result.detail)
    remote = gitops.remote_url(repo_root, "origin")
    pushed = False
    if remote:
        push = gitops.push_branch(repo_root, "origin", branch)
        if not push:
            return Publication(branch, True, False, False, str(push.detail))
        pushed = True
    if dirty:
        return Publication(branch, True, pushed, False,
                           "clone has uncommitted changes")
    release(allocation)
    return Publication(branch, True, pushed, True)


def _pin_clone_default(repo_root: Path, clone: Path, base_ref: str, seed_oid: str) -> None:
    """Point the clone's ``origin/<default>`` at the seed, not the host's local.

    ``git clone <host>`` turns the host's *local* branches into the clone's
    ``refs/remotes/origin/*``, so until the strand fetches, ``origin/main`` in
    the clone is the operator's local ``main``, unpushed commits included
    (#2236). When the seed is the real remote default, overwrite that one
    tracking ref with it. Best-effort: a strand that fetches corrects it anyway.
    """
    remote = gitops.default_remote(repo_root)
    if not remote or not seed_oid or not base_ref.startswith(f"{remote}/"):
        return
    default = base_ref.split("/", 1)[1]
    subprocess.run(
        ["git", "-C", str(clone), "update-ref", f"refs/remotes/origin/{default}", seed_oid],
        env=_clone_git_env(), capture_output=True, check=False,
    )


def _no_commits_past_seed(allocation: Allocation) -> bool:
    """True when the clone's HEAD adds nothing to the seed it sprouted from."""
    if not allocation.seed_oid:
        return False
    head = gitops.rev_parse(allocation.path, "HEAD")
    if not head:
        return False
    if head == allocation.seed_oid:
        return True
    probe = subprocess.run(
        ["git", "-C", str(allocation.path), "merge-base", "--is-ancestor",
         "HEAD", allocation.seed_oid],
        env=_clone_git_env(), capture_output=True, check=False,
    )
    return probe.returncode == 0


def _clone_git_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items()
            if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}

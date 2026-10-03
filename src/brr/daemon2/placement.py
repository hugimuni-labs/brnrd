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

from dataclasses import dataclass
from pathlib import Path

from .. import gitops, worktree


@dataclass(frozen=True)
class Allocation:
    """A worktree clone created for one strand run."""
    path: Path
    branch: str
    run_id: str

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


def allocate(repo_root: Path, run_id: str, *, base_ref: str = "HEAD") -> Allocation:
    """Create a fresh clone worktree for *run_id* branched from *base_ref*.

    Raises ``PlacementError`` if the underlying organ fails (no commit at
    *base_ref*, clone directory conflict, git failure).
    """
    try:
        path, branch = worktree.create_clone(repo_root, run_id, base_ref=base_ref)
    except Exception as exc:
        raise PlacementError(f"failed to allocate worktree for {run_id}: {exc}") from exc
    return Allocation(path=path, branch=branch, run_id=run_id)


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

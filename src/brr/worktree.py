"""Git worktree helpers for run-isolated execution.

Each run gets a fresh worktree at ``.brr/worktrees/<run-id>/`` on a
dedicated ``brr/<run-id>`` branch sprouted from the resolved seed ref. The
agent runs inside that sandbox and decides how its work should land:

- Leaving commits on ``brr/<run-id>`` follows the daemon's branch
  plan: finalization fast-forwards a resolved auto-land target, or
  preserves the run branch when no safe target exists.
- Switching to a different branch (``git switch -c feat/foo`` or
  ``git switch existing``) records a runtime branch choice, and the
  branch is preserved as-is on cleanup.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


def _git(repo_root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command in *repo_root*."""
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


@dataclass(frozen=True)
class WorktreeInfo:
    """A brr-managed worktree entry."""

    path: Path
    run_id: str
    branch: str


class BranchCheckedOutError(RuntimeError):
    """Raised when a branch is already checked out in another worktree."""

    def __init__(self, branch: str, checkout_path: Path):
        self.branch = branch
        self.checkout_path = checkout_path
        super().__init__(f"{branch} is checked out at {checkout_path}")


def run_branch_name(run_id: str) -> str:
    """Return the standard run branch name brr creates for a worktree."""
    return f"brr/{run_id}"


def list_worktrees(repo_root: Path) -> list[WorktreeInfo]:
    """List brr-managed worktrees under ``.brr/worktrees/``.

    Parses ``git worktree list --porcelain`` and filters to worktrees
    whose path starts with the brr worktrees directory.
    """
    from . import gitops

    worktrees_dir = gitops.shared_brr_dir(repo_root) / "worktrees"
    result = _git(repo_root, "worktree", "list", "--porcelain", check=False)
    if result.returncode != 0:
        return []

    entries: list[WorktreeInfo] = []
    current_path: Path | None = None
    current_branch: str = ""

    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            current_path = Path(line.split(" ", 1)[1])
            current_branch = ""
        elif line.startswith("branch "):
            ref = line.split(" ", 1)[1]
            current_branch = ref.removeprefix("refs/heads/")
        elif line == "" and current_path is not None:
            try:
                current_path.relative_to(worktrees_dir)
            except ValueError:
                pass
            else:
                run_id = current_path.name
                entries.append(WorktreeInfo(
                    path=current_path,
                    run_id=run_id,
                    branch=current_branch,
                ))
            current_path = None
            current_branch = ""

    if current_path is not None:
        try:
            current_path.relative_to(worktrees_dir)
        except ValueError:
            pass
        else:
            entries.append(WorktreeInfo(
                path=current_path,
                run_id=current_path.name,
                branch=current_branch,
            ))

    return entries


def path_for(repo_root: Path, run_id: str) -> Path:
    """Return the worktree path for *run_id*."""
    from . import gitops

    return gitops.shared_brr_dir(repo_root) / "worktrees" / run_id


def create(repo_root: Path, run_id: str, *, base_ref: str = "HEAD") -> tuple[Path, str]:
    """Create a fresh run worktree on a new ``brr/<run_id>`` branch.

    Always sprouts a new branch from *base_ref* so worktree creation
    never collides with a branch that's checked out elsewhere. Returns
    ``(worktree_path, branch_name)``.
    """
    worktree_path = path_for(repo_root, run_id)
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    if worktree_path.exists():
        raise RuntimeError(f"worktree already exists: {worktree_path}")

    branch = run_branch_name(run_id)
    args = ["worktree", "add", "-b", branch, str(worktree_path), base_ref]
    result = _git(repo_root, *args, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(detail or f"failed to create worktree {worktree_path}")
    return worktree_path, branch


def switch_to(worktree_path: Path, branch: str) -> None:
    """Switch a worktree's HEAD to *branch*, creating it if it doesn't exist.

    Uses ``git switch <branch>`` when the branch already exists locally,
    otherwise ``git switch -c <branch>`` to create it at the current HEAD.
    Called by ``WorktreeEnv.prepare`` to move the agent's starting point
    from the throwaway ``brr/<run-id>`` placeholder to the event's named
    target branch before the agent runs.

    Raises ``BranchCheckedOutError`` before invoking git when the branch is
    already checked out in another worktree. Git refuses that checkout anyway;
    the typed error lets callers keep the unique run branch instead.
    """
    from . import gitops

    checkout_path = gitops.branch_checkout_path(worktree_path, branch)
    if (
        checkout_path is not None
        and checkout_path.resolve() != worktree_path.resolve()
    ):
        raise BranchCheckedOutError(branch, checkout_path)

    result = subprocess.run(
        ["git", "switch", branch],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return
    result = subprocess.run(
        ["git", "switch", "-c", branch],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        checkout_path = gitops.branch_checkout_path(worktree_path, branch)
        if (
            checkout_path is not None
            and checkout_path.resolve() != worktree_path.resolve()
        ):
            raise BranchCheckedOutError(branch, checkout_path)
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(
            detail or f"failed to switch worktree to branch {branch!r}"
        )


def current_branch(worktree_path: Path) -> str | None:
    """Return the branch HEAD points at inside *worktree_path*, or None.

    Returns ``None`` for a detached HEAD (rare — only happens if the
    agent explicitly detaches inside the worktree).
    """
    result = subprocess.run(
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    name = result.stdout.strip()
    return name or None


def has_commits_beyond(worktree_path: Path, base_ref: str) -> bool:
    """Return True if the worktree HEAD has commits not reachable from *base_ref*."""
    result = subprocess.run(
        ["git", "rev-list", "--count", f"{base_ref}..HEAD"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return False
    try:
        return int(result.stdout.strip() or "0") > 0
    except ValueError:
        return False


def unpushed_commit_count(worktree_path: Path) -> int:
    """Return the number of HEAD commits not present on any remote-tracking ref.

    ``git rev-list --count HEAD --not --remotes`` counts commits reachable
    from HEAD but from no ``refs/remotes/*`` ref — i.e. local work not yet
    pushed anywhere. It needs no configured upstream, so a fresh run
    branch (which has none) still reports honestly. Any git failure
    (detached/empty repo, command error) yields ``0`` rather than raising:
    the forge facet this feeds is observational and must never fail a run.
    """
    result = subprocess.run(
        ["git", "rev-list", "--count", "HEAD", "--not", "--remotes"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return 0
    try:
        return int(result.stdout.strip() or "0")
    except ValueError:
        return 0


def has_uncommitted_changes(worktree_path: Path) -> bool:
    """Return True when the worktree has untracked, unstaged, or staged changes.

    Used by finalization to decide whether the worktree directory can be
    discarded safely. If the agent created files but didn't commit them,
    those files are only present here — tearing the worktree down would
    silently drop them, so we keep it for forensic inspection instead.
    """
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        # Conservatively assume there is something worth keeping when we
        # can't read the status — better to leak a worktree than to drop
        # uncommitted work.
        return True
    return bool(result.stdout.strip())


def remove(
    repo_root: Path,
    run_id: str,
    *,
    branch: str | None = None,
    delete_branch: bool = False,
    force: bool = False,
) -> None:
    """Remove a run worktree and optionally delete its branch."""
    worktree_path = path_for(repo_root, run_id)
    if worktree_path.exists():
        args = ["worktree", "remove", str(worktree_path)]
        if force:
            args.insert(2, "--force")
        result = _git(repo_root, *args, check=False)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(detail or f"failed to remove worktree {worktree_path}")

    if delete_branch and branch:
        result = _git(repo_root, "branch", "-D", branch, check=False)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(detail or f"failed to delete branch {branch}")

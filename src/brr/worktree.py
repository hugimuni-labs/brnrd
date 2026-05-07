"""Git worktree helpers for task-isolated execution.

Each task gets a fresh worktree at ``.brr/worktrees/<task-id>/`` on a
dedicated ``brr/<task-id>`` branch sprouted from the current HEAD. The
agent runs inside that sandbox and decides how its work should land:

- Leaving commits on ``brr/<task-id>`` opts into the auto-merge
  contract — :func:`finalize` fast-forwards the branch back into the
  base branch and deletes both the worktree and the temporary branch
  on success.
- Switching to a different branch (``git switch -c feat/foo`` or
  ``git switch existing``) opts out of auto-merge — the branch is
  preserved as-is on cleanup.
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
    task_id: str
    branch: str


def task_branch_name(task_id: str) -> str:
    """Return the standard task branch name brr creates for a worktree."""
    return f"brr/{task_id}"


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
                task_id = current_path.name
                entries.append(WorktreeInfo(
                    path=current_path,
                    task_id=task_id,
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
                task_id=current_path.name,
                branch=current_branch,
            ))

    return entries


def path_for(repo_root: Path, task_id: str) -> Path:
    """Return the worktree path for *task_id*."""
    from . import gitops

    return gitops.shared_brr_dir(repo_root) / "worktrees" / task_id


def create(repo_root: Path, task_id: str) -> tuple[Path, str]:
    """Create a fresh task worktree on a new ``brr/<task_id>`` branch.

    Always sprouts a new branch from the current HEAD so worktree
    creation never collides with a branch that's checked out
    elsewhere. Returns ``(worktree_path, branch_name)``.
    """
    worktree_path = path_for(repo_root, task_id)
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    if worktree_path.exists():
        raise RuntimeError(f"worktree already exists: {worktree_path}")

    branch = task_branch_name(task_id)
    args = ["worktree", "add", "-b", branch, str(worktree_path), "HEAD"]
    result = _git(repo_root, *args, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(detail or f"failed to create worktree {worktree_path}")
    return worktree_path, branch


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


def remove(
    repo_root: Path,
    task_id: str,
    *,
    branch: str | None = None,
    delete_branch: bool = False,
    force: bool = False,
) -> None:
    """Remove a task worktree and optionally delete its branch."""
    worktree_path = path_for(repo_root, task_id)
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

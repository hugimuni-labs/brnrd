"""Git worktree helpers for task-isolated execution."""

from __future__ import annotations

import subprocess
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


def path_for(repo_root: Path, task_id: str) -> Path:
    """Return the worktree path for *task_id*."""
    return repo_root / ".brr" / "worktrees" / task_id


def create(repo_root: Path, task_id: str, branch: str, create_branch: bool = True) -> Path:
    """Create a task worktree and return its path."""
    worktree_path = path_for(repo_root, task_id)
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    if worktree_path.exists():
        raise RuntimeError(f"worktree already exists: {worktree_path}")

    args = ["worktree", "add", str(worktree_path)]
    if create_branch:
        args.extend(["-b", branch, "HEAD"])
    else:
        args.append(branch)

    result = _git(repo_root, *args, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(detail or f"failed to create worktree for {branch}")
    return worktree_path


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
        result = _git(repo_root, "branch", "-d", branch, check=False)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(detail or f"failed to delete branch {branch}")

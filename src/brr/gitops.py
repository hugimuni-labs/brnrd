"""Git helpers — repo detection, branching, and file tracking."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class MergeResult:
    """Result of merging a branch back into the current branch."""

    success: bool
    branch: str
    commit: str = ""
    conflicts: list[str] | None = None
    detail: str = ""


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


def ensure_git_repo() -> Path:
    """Return the repository root, or raise RuntimeError."""
    try:
        result = _git(Path.cwd(), "rev-parse", "--show-toplevel")
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("Not a Git repository; run `git init` first.") from exc
    return Path(result.stdout.strip())


def current_branch(repo_root: Path) -> str:
    """Return the current branch name, or ``HEAD`` when detached."""
    result = _git(repo_root, "rev-parse", "--abbrev-ref", "HEAD", check=False)
    if result.returncode != 0:
        return "HEAD"
    return result.stdout.strip() or "HEAD"


def shared_brr_dir(repo_root: Path) -> Path:
    """Return the shared ``.brr`` dir for a repo or worktree checkout.

    In a normal checkout this is ``repo_root/.brr``. In a git worktree,
    runtime state lives beside the common git dir in the main checkout.
    """
    local = repo_root / ".brr"
    if local.exists():
        return local

    result = _git(repo_root, "rev-parse", "--git-common-dir", check=False)
    if result.returncode != 0:
        return local

    common_dir = Path(result.stdout.strip())
    if not common_dir.is_absolute():
        common_dir = (repo_root / common_dir).resolve()
    return common_dir.parent / ".brr"


def is_tracked(path: Path) -> bool:
    """Return True if *path* is tracked by Git."""
    try:
        _git(Path.cwd(), "ls-files", "--error-unmatch", str(path))
        return True
    except subprocess.CalledProcessError:
        return False


def branch_exists(repo_root: Path, branch: str) -> bool:
    """Return True if *branch* exists locally."""
    result = _git(repo_root, "show-ref", "--verify", f"refs/heads/{branch}", check=False)
    return result.returncode == 0


def merge_branch(repo_root: Path, branch: str, message: str | None = None) -> MergeResult:
    """Merge *branch* into the currently checked-out branch."""
    merge_args = ["merge", branch, "--no-ff"]
    if message:
        merge_args.extend(["-m", message])

    result = _git(repo_root, *merge_args, check=False)
    if result.returncode == 0:
        head = _git(repo_root, "rev-parse", "HEAD").stdout.strip()
        return MergeResult(success=True, branch=branch, commit=head)

    conflicts = _git(
        repo_root, "diff", "--name-only", "--diff-filter=U", check=False,
    ).stdout.splitlines()
    _git(repo_root, "merge", "--abort", check=False)
    detail = result.stderr.strip() or result.stdout.strip()
    return MergeResult(
        success=False,
        branch=branch,
        conflicts=conflicts,
        detail=detail,
    )

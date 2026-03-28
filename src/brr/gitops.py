"""Git operations and safety helpers.

This module provides helpers for interacting with the Git repository that
contains the current working directory.  It is deliberately small; most
operations are deferred to external tools such as `git` itself.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def ensure_git_repo() -> Path:
    """Ensure that the current directory is inside a Git repository.

    Returns the path to the repository root.  Raises a RuntimeError if
    there is no repository.
    """
    cwd = Path.cwd()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("Not a Git repository; run `git init` first.") from exc
    repo_root = Path(result.stdout.strip())
    return repo_root


def is_tracked(path: Path) -> bool:
    """Return True if the given path is tracked by Git.

    This uses `git ls-files` to check tracking status.  It returns False for
    untracked and ignored files.
    """
    try:
        subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(path)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def commit_files(paths: list[Path], message: str) -> None:
    """Commit the specified files to Git with the given commit message.

    This function stages the files and runs `git commit`.  It raises an
    exception if the commit fails.
    """
    if not paths:
        return
    subprocess.run(["git", "add"] + [str(p) for p in paths], check=True)
    subprocess.run(["git", "commit", "-m", message], check=True)
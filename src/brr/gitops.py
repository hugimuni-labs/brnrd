"""Git helpers — repo detection and file tracking."""

from __future__ import annotations

import subprocess
from pathlib import Path


def ensure_git_repo() -> Path:
    """Return the repository root, or raise RuntimeError."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("Not a Git repository; run `git init` first.") from exc
    return Path(result.stdout.strip())


def is_tracked(path: Path) -> bool:
    """Return True if *path* is tracked by Git."""
    try:
        subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(path)],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        return True
    except subprocess.CalledProcessError:
        return False

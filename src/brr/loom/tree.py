"""``GET /loom/tree.json`` — the whole tree a ground can be laid out from.

``state.json`` carries only the places a run *visited* (``tree.repo`` /
``tree.home.places``). A map laid out from those moves every time the fog
lifts; the dungeon's law — the layout is a pure function of the reading and
rooms do not move — wants the ground drawn from the whole tree, with light
from the visits. This is that tree: ``git ls-files`` of the repo, of the
account home's own trees (``dominion`` · ``surface`` · ``hearth`` ·
``account``), and of the knowledge repo mounted as ``knowledge/``.
``.gitignore`` decides what is not source — an ignored path is simply not a
room. A checkout that is not a git repo yields an empty file list, never an
error: the ground is then only what the visits say.
"""
from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HOME_TREE_DIRS = ("dominion", "surface", "hearth", "account")
TREE_BEAT_MS = 30_000  # the tree changes when a file is born; a beat of 30 s is plenty


def ls_files(root: Path) -> list[str]:
    """Tracked paths under *root*, forward-slashed, or ``[]`` when it is not a git checkout."""
    if not (root / ".git").exists():
        return []
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True, check=True, timeout=20,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]


def repo_name(root: Path) -> str:
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return root.name
    url = r.stdout.strip().rstrip("/")
    if r.returncode == 0 and url:
        tail = url.rsplit("/", 1)[-1]
        return tail[:-4] if tail.endswith(".git") else tail
    return root.name


def build(repo_root: Path | str, account_home: Path | str | None, *, now: datetime | None = None) -> dict[str, Any]:
    repo = Path(repo_root)
    files = sorted(ls_files(repo))
    home_files: list[str] = []
    if account_home:
        home = Path(account_home)
        home_files = [p for p in ls_files(home) if p.split("/", 1)[0] in HOME_TREE_DIRS]
        knowledge = home / "knowledge"
        if (knowledge / ".git").exists():
            home_files += ["knowledge/" + p for p in ls_files(knowledge)]
        home_files.sort()
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "captured_at": stamp,
        "repo": {"name": repo_name(repo), "root": os.fspath(repo), "files": files},
        "home": {"files": home_files},
    }

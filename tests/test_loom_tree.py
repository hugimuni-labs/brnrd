"""``brr.loom.tree`` — the whole tracked tree a ground is laid out from."""
from __future__ import annotations

import subprocess
from pathlib import Path

from brr.loom import tree


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _repo(root: Path, files: dict[str, str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed")


def test_build_lays_out_tracked_files_only_and_mounts_the_knowledge_repo(tmp_path: Path):
    repo = tmp_path / "widgets"
    _repo(repo, {"src/a.py": "a", "src/b/c.py": "c", ".gitignore": "build/\n"})
    (repo / "build").mkdir()
    (repo / "build" / "out.bin").write_bytes(b"\0")  # ignored: not a room
    (repo / "scratch.txt").write_text("untracked: not a room either", encoding="utf-8")
    home = tmp_path / "home"
    _repo(home, {"dominion/notebook.md": "n", "surface/warp/w-1.md": "w", "runs/r1/body.md": "not a home tree", "hearth/him.md": "h"})
    _repo(home / "knowledge", {"repos/acme__widgets/log.md": "log"})

    t = tree.build(repo, home)

    assert t["repo"]["files"] == [".gitignore", "src/a.py", "src/b/c.py"]
    assert t["repo"]["name"] == "widgets"  # no origin remote ⇒ the checkout's own name
    assert t["home"]["files"] == [
        "dominion/notebook.md",
        "hearth/him.md",
        "knowledge/repos/acme__widgets/log.md",
        "surface/warp/w-1.md",
    ]
    assert "runs/r1/body.md" not in t["home"]["files"]


def test_build_on_a_bare_directory_is_empty_not_an_error(tmp_path: Path):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "loose.py").write_text("x", encoding="utf-8")
    t = tree.build(plain, None)
    assert t["repo"]["files"] == [] and t["home"]["files"] == []
    assert t["repo"]["name"] == "plain"

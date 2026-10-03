"""Placement adapter: allocate/release/land wrapping worktree.create_clone."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from brr.daemon2.placement import Allocation, PlacementError, allocate, release


def _git_env() -> dict[str, str]:
    """Stripped env without GIT_DIR/GIT_WORK_TREE so test ops stay in tmp."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
    env.update({"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@t.com",
                "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@t.com"})
    return env


def _make_git_repo(path: Path) -> Path:
    """Init a minimal git repo at *path* with one commit."""
    env = _git_env()
    subprocess.run(["git", "init", "-b", "main", str(path)],
                   env=env, capture_output=True, check=True)
    (path / "AGENTS.md").write_text("# test\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "."],
                   env=env, capture_output=True, check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "init"],
                   env=env, capture_output=True, check=True)
    return path


def test_allocate_creates_clone_and_release_removes_it(tmp_path: Path) -> None:
    repo = _make_git_repo(tmp_path / "repo")
    alloc = allocate(repo, "run-test-0001")
    assert isinstance(alloc, Allocation)
    assert alloc.path.exists()
    assert alloc.branch.startswith("brr/")
    assert alloc.run_id == "run-test-0001"
    git_dir = alloc.path / ".git"
    assert git_dir.is_dir(), "clone must have its own .git directory"
    env = alloc.env()
    assert env["GIT_DIR"] == str(git_dir)
    assert env["GIT_WORK_TREE"] == str(alloc.path)
    release(alloc)
    assert not alloc.path.exists()


def test_release_is_idempotent(tmp_path: Path) -> None:
    repo = _make_git_repo(tmp_path / "repo")
    alloc = allocate(repo, "run-test-0002")
    release(alloc)
    release(alloc)  # second call must not raise


def test_allocate_fails_on_non_git_directory(tmp_path: Path) -> None:
    plain = tmp_path / "notgit"
    plain.mkdir()
    (plain / "AGENTS.md").write_text("# x\n")
    with pytest.raises(PlacementError, match="failed to allocate"):
        allocate(plain, "run-test-0003")


def test_git_env_pins_to_clone_not_host(tmp_path: Path) -> None:
    """Operations using alloc.env() target the clone, not the host checkout."""
    repo = _make_git_repo(tmp_path / "repo")
    alloc = allocate(repo, "run-test-0004")
    try:
        env = {**_git_env(), **alloc.env()}
        # Write and commit a file inside the clone.
        (alloc.path / "strand-work.txt").write_text("done\n", encoding="utf-8")
        subprocess.run(["git", "add", "strand-work.txt"],
                       env=env, cwd=str(alloc.path), capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "strand work"],
                       env=env, cwd=str(alloc.path), capture_output=True, check=True)
        # The host repo must not have the strand commit.
        host_log = subprocess.run(
            ["git", "-C", str(repo), "log", "--oneline"],
            env=_git_env(), capture_output=True, text=True, check=True)
        assert "strand work" not in host_log.stdout
        # The clone has it.
        clone_log = subprocess.run(
            ["git", "log", "--oneline"],
            env=env, cwd=str(alloc.path), capture_output=True, text=True, check=True)
        assert "strand work" in clone_log.stdout
    finally:
        release(alloc)

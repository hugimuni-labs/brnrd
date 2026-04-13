"""Tests for gitops module."""

import subprocess
from pathlib import Path

from brr.gitops import is_tracked, merge_branch


def _init_repo(repo: Path) -> str:
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=repo, check=True, stdout=subprocess.PIPE,
    )
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    return "main"


def test_is_tracked(tmp_path, monkeypatch):
    # Setup a temporary git repo
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "file.txt").write_text("data")
    # Initialise git
    _init_repo(repo)
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    # Change directory to repo
    monkeypatch.chdir(repo)
    assert is_tracked(Path("file.txt")) is True
    assert is_tracked(Path("nonexistent.txt")) is False


def test_merge_branch_merges_feature_commit(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    main_branch = _init_repo(repo)
    (repo / "file.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)

    subprocess.run(["git", "checkout", "-b", "feature/worktree"], cwd=repo, check=True, stdout=subprocess.PIPE)
    (repo / "file.txt").write_text("base\nfeature\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-am", "feature"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "checkout", main_branch], cwd=repo, check=True, stdout=subprocess.PIPE)

    result = merge_branch(repo, "feature/worktree", "merge feature/worktree")

    assert result.success is True
    assert result.branch == "feature/worktree"
    assert result.commit
    assert "feature" in (repo / "file.txt").read_text(encoding="utf-8")


def test_merge_branch_reports_conflicts(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    main_branch = _init_repo(repo)
    (repo / "file.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)

    subprocess.run(["git", "checkout", "-b", "feature/conflict"], cwd=repo, check=True, stdout=subprocess.PIPE)
    (repo / "file.txt").write_text("feature change\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-am", "feature"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "checkout", main_branch], cwd=repo, check=True, stdout=subprocess.PIPE)

    (repo / "file.txt").write_text("main change\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-am", "main"], cwd=repo, check=True, stdout=subprocess.PIPE)

    result = merge_branch(repo, "feature/conflict", "merge feature/conflict")

    assert result.success is False
    assert result.conflicts == ["file.txt"]

"""Tests for gitops module."""

import subprocess
from pathlib import Path

from brr.gitops import current_branch, is_tracked, merge_branch, shared_brr_dir
from brr.worktree import list_worktrees, create, remove


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


def test_list_worktrees_empty(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "file.txt").write_text("init\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=repo, check=True,
        stdout=subprocess.PIPE,
    )

    assert list_worktrees(repo) == []


def test_list_worktrees_finds_brr_worktree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "file.txt").write_text("init\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=repo, check=True,
        stdout=subprocess.PIPE,
    )

    wt_path = create(repo, "task-42", "brr/task-42", create_branch=True)
    assert wt_path.exists()

    wts = list_worktrees(repo)
    assert len(wts) == 1
    assert wts[0].task_id == "task-42"
    assert wts[0].branch == "brr/task-42"
    assert wts[0].path == wt_path

    remove(repo, "task-42", branch="brr/task-42", delete_branch=True, force=True)
    assert list_worktrees(repo) == []


def test_shared_brr_dir_uses_main_checkout_for_worktree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "file.txt").write_text("init\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=repo, check=True,
        stdout=subprocess.PIPE,
    )

    (repo / ".brr").mkdir()
    wt_path = create(repo, "task-42", "brr/task-42", create_branch=True)
    assert shared_brr_dir(repo) == repo / ".brr"
    assert shared_brr_dir(wt_path) == repo / ".brr"
    assert current_branch(wt_path) == "brr/task-42"

    remove(repo, "task-42", branch="brr/task-42", delete_branch=True, force=True)


def test_worktree_branch_is_created_from_current_branch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "file.txt").write_text("main\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "checkout", "-b", "feature/base"], cwd=repo, check=True, stdout=subprocess.PIPE)
    (repo / "feature.txt").write_text("feature\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "feature base"], cwd=repo, check=True, stdout=subprocess.PIPE)

    wt_path = create(repo, "task-43", "brr/task-43", create_branch=True)
    try:
        merge_base = subprocess.run(
            ["git", "merge-base", "feature/base", "brr/task-43"],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        feature_head = subprocess.run(
            ["git", "rev-parse", "feature/base"],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        assert merge_base == feature_head
        assert (wt_path / "feature.txt").exists()
    finally:
        remove(repo, "task-43", branch="brr/task-43", delete_branch=True, force=True)

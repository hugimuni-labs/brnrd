"""Tests for gitops module."""

import subprocess
from pathlib import Path

from brr.gitops import (
    current_branch,
    fast_forward_branch,
    is_tracked,
    shared_brr_dir,
)
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


def test_fast_forward_branch_updates_checked_out_target(tmp_path):
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

    result = fast_forward_branch(repo, main_branch, "feature/worktree")

    assert result.success is True
    assert result.branch == main_branch
    assert result.commit
    assert "feature" in (repo / "file.txt").read_text(encoding="utf-8")


def test_fast_forward_branch_refuses_diverged_target(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    main_branch = _init_repo(repo)
    (repo / "file.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)

    subprocess.run(["git", "checkout", "-b", "feature/diverge"], cwd=repo, check=True, stdout=subprocess.PIPE)
    (repo / "feature.txt").write_text("feature\n", encoding="utf-8")
    subprocess.run(["git", "add", "feature.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "feature"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "checkout", main_branch], cwd=repo, check=True, stdout=subprocess.PIPE)

    (repo / "main.txt").write_text("main\n", encoding="utf-8")
    subprocess.run(["git", "add", "main.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "main"], cwd=repo, check=True, stdout=subprocess.PIPE)

    result = fast_forward_branch(repo, main_branch, "feature/diverge")

    assert result.success is False
    assert result.detail


def test_fast_forward_branch_updates_unchecked_out_branch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    main_branch = _init_repo(repo)
    (repo / "file.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)

    subprocess.run(["git", "checkout", "-b", "target"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "checkout", main_branch], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "checkout", "-b", "source"], cwd=repo, check=True, stdout=subprocess.PIPE)
    (repo / "source.txt").write_text("source\n", encoding="utf-8")
    subprocess.run(["git", "add", "source.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "source"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "checkout", main_branch], cwd=repo, check=True, stdout=subprocess.PIPE)

    result = fast_forward_branch(repo, "target", "source")

    assert result.success is True
    target_head = subprocess.run(
        ["git", "rev-parse", "target"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    source_head = subprocess.run(
        ["git", "rev-parse", "source"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    assert target_head == source_head


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

    wt_path, branch = create(repo, "task-42")
    assert wt_path.exists()
    assert branch == "brr/task-42"

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
    wt_path, _branch = create(repo, "task-42")
    assert shared_brr_dir(repo) == repo / ".brr"
    assert shared_brr_dir(wt_path) == repo / ".brr"
    assert current_branch(wt_path) == "brr/task-42"

    remove(repo, "task-42", branch="brr/task-42", delete_branch=True, force=True)


def test_worktree_branch_defaults_to_current_head(tmp_path):
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

    wt_path, _branch = create(repo, "task-43")
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


def test_worktree_branch_can_be_created_from_explicit_base_ref(tmp_path):
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
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, stdout=subprocess.PIPE)

    wt_path, _branch = create(repo, "task-44", base_ref="feature/base")
    try:
        assert (wt_path / "feature.txt").exists()
    finally:
        remove(repo, "task-44", branch="brr/task-44", delete_branch=True, force=True)

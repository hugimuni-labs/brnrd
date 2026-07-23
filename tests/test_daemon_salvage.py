"""Tests for ``daemon._capture_worktree`` — the failed-run salvage net.

When a run is killed/timed-out/quota-exhausted, the agent usually never
reached its own commit+push and ``WorktreeEnv.finalize`` skips publish for a
non-``done`` run. ``_capture_worktree`` commits any in-flight edits and arms
``publish_branch`` so the publish() tail carries the work to the remote (the
2026-06-22 quota incident).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from brr import daemon
from brr.run import Run

from _helpers import commit_files, init_git_repo


@dataclass
class _Ctx:
    cwd: Path


@dataclass
class _Plan:
    seed_ref: str


def _seed_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "wt"
    init_git_repo(repo)
    seed = commit_files(repo, {"README.md": "seed\n"}, message="seed")
    return repo, seed


def _branch(repo: Path, name: str) -> None:
    subprocess.run(["git", "checkout", "-b", name], cwd=repo, check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _run(tmp_path: Path) -> tuple[Run, Path]:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    return Run(id="task-x", event_id="evt-x", body="b", status="error"), runs_dir


def test_commits_inflight_edits_and_arms_publish(tmp_path):
    repo, seed = _seed_repo(tmp_path)
    _branch(repo, "brr/work")
    (repo / "new.py").write_text("print('half done')\n", encoding="utf-8")
    task, runs_dir = _run(tmp_path)

    daemon._capture_worktree(task, _Ctx(repo), _Plan(seed), {}, runs_dir)

    # Uncommitted work is now a salvage commit on the branch...
    porcelain = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True,
    ).stdout.strip()
    assert porcelain == ""
    log = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"], cwd=repo, capture_output=True, text=True,
    ).stdout.strip()
    assert "salvage" in log and task.id in log
    # ...and publish_branch is armed for the publish() tail.
    assert task.meta["publish_branch"] == "brr/work"
    # #575: the salvage commit is a project-repo commit path the daemon
    # owns — it must carry the same Brnrd-Run-Id trailer as the automated
    # capture commit, so relics.collection_scope's shared-window fallback
    # (a *different* call site — a host, not worktree, run) can filter on
    # it consistently.
    trailer = subprocess.run(
        ["git", "log", "-1", "--format=%(trailers:key=Brnrd-Run-Id,valueonly)"],
        cwd=repo, capture_output=True, text=True,
    ).stdout.strip()
    assert trailer == task.id


def test_already_committed_clean_tree_still_arms_publish(tmp_path):
    """The incident's other half: commits exist but were never pushed."""
    repo, seed = _seed_repo(tmp_path)
    _branch(repo, "brr/work")
    commit_files(repo, {"feat.py": "x\n"}, message="real work")
    task, runs_dir = _run(tmp_path)

    daemon._capture_worktree(task, _Ctx(repo), _Plan(seed), {}, runs_dir)

    assert task.meta["publish_branch"] == "brr/work"
    # No salvage commit added — the tree was already clean.
    log = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"], cwd=repo, capture_output=True, text=True,
    ).stdout.strip()
    assert "salvage" not in log


def test_no_commits_beyond_seed_stays_silent(tmp_path):
    """A run that failed before producing anything must not publish."""
    repo, seed = _seed_repo(tmp_path)
    _branch(repo, "brr/work")
    task, runs_dir = _run(tmp_path)

    daemon._capture_worktree(task, _Ctx(repo), _Plan(seed), {}, runs_dir)

    assert "publish_branch" not in task.meta


def test_disabled_via_config_is_noop(tmp_path):
    repo, seed = _seed_repo(tmp_path)
    _branch(repo, "brr/work")
    (repo / "new.py").write_text("x\n", encoding="utf-8")
    task, runs_dir = _run(tmp_path)

    daemon._capture_worktree(
        task, _Ctx(repo), _Plan(seed), {"salvage.enabled": False}, runs_dir,
    )

    assert "publish_branch" not in task.meta
    # The uncommitted file is left untouched.
    porcelain = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True,
    ).stdout.strip()
    assert "new.py" in porcelain


def test_detached_head_is_skipped(tmp_path):
    repo, seed = _seed_repo(tmp_path)
    subprocess.run(["git", "checkout", "--detach"], cwd=repo, check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (repo / "new.py").write_text("x\n", encoding="utf-8")
    task, runs_dir = _run(tmp_path)

    daemon._capture_worktree(task, _Ctx(repo), _Plan(seed), {}, runs_dir)

    assert "publish_branch" not in task.meta

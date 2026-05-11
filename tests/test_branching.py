"""Tests for daemon branch intent resolution."""

import subprocess
from pathlib import Path

from brr import branching


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    (repo / "file.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)


def test_default_fallback_preserves_task_branch_from_default_seed(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    subprocess.run(["git", "checkout", "-b", "feature/host"], cwd=repo, check=True, stdout=subprocess.PIPE)

    plan = branching.resolve_branch_plan(repo, {}, {})

    assert plan.seed_ref == "main"
    assert plan.auto_land_branch is None
    assert plan.authority == "fallback:preserve"
    assert plan.host_context_branch == "feature/host"


def test_structured_event_branch_is_auto_land_target(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    subprocess.run(["git", "checkout", "-b", "feature/task"], cwd=repo, check=True, stdout=subprocess.PIPE)

    plan = branching.resolve_branch_plan(
        repo,
        {"target_branch": "feature/task"},
        {},
    )

    assert plan.seed_ref == "feature/task"
    assert plan.auto_land_branch == "feature/task"
    assert plan.authority == "event:target_branch"
    assert plan.expected_old_oid


def test_conversation_branch_context_is_used_when_unambiguous(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    plan = branching.resolve_branch_plan(
        repo,
        {},
        {},
        conversation_records=[
            {
                "kind": "update",
                "type": "done",
                "task_id": "task-old",
                "preserved_branch": "brr/task-old",
            },
        ],
    )

    assert plan.seed_ref == "main"
    assert plan.auto_land_branch == "brr/task-old"
    assert plan.authority == "conversation"


def test_ambiguous_conversation_branches_fall_back_to_preserve(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    plan = branching.resolve_branch_plan(
        repo,
        {},
        {},
        conversation_records=[
            {"kind": "update", "landed_branch": "feature/one"},
            {"kind": "update", "landed_branch": "feature/two"},
        ],
    )

    assert plan.auto_land_branch is None
    assert plan.authority == "fallback:preserve"

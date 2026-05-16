"""Tests for daemon branch plan resolution."""

import subprocess
from pathlib import Path

from brr import branching

from _helpers import commit_files, init_git_repo


def _init_repo(repo: Path) -> None:
    init_git_repo(repo)
    commit_files(repo, {"file.txt": "base\n"})


def test_default_fallback_preserves_task_branch_from_default_seed(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    subprocess.run(
        ["git", "checkout", "-b", "feature/host"],
        cwd=repo, check=True, stdout=subprocess.PIPE,
    )

    plan = branching.resolve_branch_plan(repo, {}, {})

    assert plan.seed_ref == "main"
    assert plan.auto_land_branch is None
    assert plan.source == "fallback:preserve"
    assert plan.host_context_branch == "feature/host"


def test_structured_event_branch_is_auto_land_target(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    subprocess.run(
        ["git", "checkout", "-b", "feature/task"],
        cwd=repo, check=True, stdout=subprocess.PIPE,
    )

    plan = branching.resolve_branch_plan(
        repo,
        {"target_branch": "feature/task"},
        {},
    )

    assert plan.seed_ref == "feature/task"
    assert plan.auto_land_branch == "feature/task"
    assert plan.source == "event:target_branch"
    assert plan.expected_old_oid


def test_conversation_branch_is_not_auto_landed(tmp_path):
    """Conversation history is no longer mined for auto-land authority.

    The agent reads recent records from the prompt and can switch
    branches at runtime; pre-decoding them as durable branch authority
    silently routed unrelated tasks onto stale sibling branches.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    plan = branching.resolve_branch_plan(repo, {}, {})

    assert plan.auto_land_branch is None
    assert plan.source == "fallback:preserve"
    assert plan.seed_ref == "main"


def test_fallback_current_mode_uses_host_branch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    subprocess.run(
        ["git", "checkout", "-b", "feature/host"],
        cwd=repo, check=True, stdout=subprocess.PIPE,
    )

    plan = branching.resolve_branch_plan(
        repo, {}, {"branch.fallback": "current"},
    )

    assert plan.auto_land_branch == "feature/host"
    assert plan.source == "fallback:current"


def test_unknown_fallback_mode_falls_back_to_preserve(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    plan = branching.resolve_branch_plan(
        repo, {}, {"branch.fallback": "inbox"},
    )

    assert plan.auto_land_branch is None
    assert plan.source == "fallback:preserve"


def test_legacy_branch_field_special_values_skipped(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    plan = branching.resolve_branch_plan(repo, {"branch": "auto"}, {})

    assert plan.auto_land_branch is None
    assert plan.source == "fallback:preserve"


def test_legacy_branch_field_current_resolves_to_host_branch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    subprocess.run(
        ["git", "checkout", "-b", "feature/host"],
        cwd=repo, check=True, stdout=subprocess.PIPE,
    )

    plan = branching.resolve_branch_plan(repo, {"branch": "current"}, {})

    assert plan.auto_land_branch == "feature/host"
    assert plan.source == "event:branch"

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


def _init_repo_with_origin(tmp_path: Path) -> Path:
    """Create a working repo cloned from a bare ``origin`` next to it.

    Returns the working tree path. The bare repo lives at
    ``tmp_path / "origin.git"`` so callers can reach it for separate
    pushes that simulate a remote diverging from the local checkout.
    """
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    seed = tmp_path / "seed"
    init_git_repo(seed)
    commit_files(seed, {"file.txt": "base\n"})
    subprocess.run(
        ["git", "remote", "add", "origin", str(origin)],
        cwd=seed, check=True,
    )
    subprocess.run(
        ["git", "push", "-u", "origin", "main"],
        cwd=seed, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    repo = tmp_path / "repo"
    subprocess.run(
        ["git", "clone", str(origin), str(repo)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True,
    )
    return repo


def test_event_branch_seeds_from_remote_when_local_diverged(tmp_path):
    """When the gate names a branch whose local copy has diverged from
    the remote, the plan must seed from ``origin/<branch>`` so the
    worker sprouts from the forge-visible state. Without this, the
    daemon's pre-task ff is refused and the worker would build on a
    stale local branch, producing a divergent, unpushable history."""
    repo = _init_repo_with_origin(tmp_path)
    bare = tmp_path / "origin.git"

    # Branch published on origin, then advanced via a sibling clone so
    # the worker repo's tracking ref outruns its local branch copy.
    subprocess.run(
        ["git", "checkout", "-b", "feature/x"], cwd=repo, check=True,
        stdout=subprocess.PIPE,
    )
    commit_files(repo, {"x.txt": "local\n"}, message="local commit")
    subprocess.run(
        ["git", "push", "-u", "origin", "feature/x"],
        cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )

    sibling = tmp_path / "sibling"
    subprocess.run(
        ["git", "clone", str(bare), str(sibling)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"], cwd=sibling, check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=sibling, check=True,
    )
    subprocess.run(
        ["git", "checkout", "feature/x"],
        cwd=sibling, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    commit_files(sibling, {"x.txt": "remote\n"}, message="remote ahead")
    # Force-push so the remote ref intentionally diverges from `repo`'s
    # local feature/x — this is exactly the rebase-from-agent scenario.
    subprocess.run(
        ["git", "push", "--force", "origin", "feature/x"],
        cwd=sibling, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    subprocess.run(
        ["git", "fetch", "origin"],
        cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )

    plan = branching.resolve_branch_plan(
        repo, {"branch_target": "feature/x"}, {},
    )

    remote_oid = subprocess.run(
        ["git", "rev-parse", "origin/feature/x"],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()

    assert plan.seed_ref == "origin/feature/x"
    assert plan.expected_old_oid == remote_oid
    assert plan.auto_land_branch == "feature/x"
    assert plan.source == "event:branch_target"


def test_event_branch_falls_back_to_local_when_no_remote_ref(tmp_path):
    """No origin/<target> means the branch exists only locally (or the
    remote ref hasn't been fetched yet). Seed from the local copy so
    behaviour is unchanged for repos without a remote."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    subprocess.run(
        ["git", "checkout", "-b", "feature/local-only"],
        cwd=repo, check=True, stdout=subprocess.PIPE,
    )

    plan = branching.resolve_branch_plan(
        repo, {"branch_target": "feature/local-only"}, {},
    )

    assert plan.seed_ref == "feature/local-only"
    assert plan.source == "event:branch_target"


def test_fallback_current_mode_does_not_prefer_remote(tmp_path):
    """``branch.fallback=current`` is the self-development knob: the
    daemon is sharing the host's checkout and the host is the source of
    truth. The remote preference is event-branch-only, so the seed must
    stay on the local current branch even when origin/<branch> exists."""
    repo = _init_repo_with_origin(tmp_path)
    subprocess.run(
        ["git", "checkout", "-b", "feature/host"],
        cwd=repo, check=True, stdout=subprocess.PIPE,
    )
    commit_files(repo, {"h.txt": "host\n"}, message="host commit")
    subprocess.run(
        ["git", "push", "-u", "origin", "feature/host"],
        cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )

    plan = branching.resolve_branch_plan(
        repo, {}, {"branch.fallback": "current"},
    )

    assert plan.seed_ref == "feature/host"
    assert plan.source == "fallback:current"

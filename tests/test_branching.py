"""Tests for daemon publish plan resolution."""

import subprocess
from pathlib import Path

from brr import branching

from _helpers import commit_files, init_git_repo


def _init_repo(repo: Path) -> None:
    init_git_repo(repo)
    commit_files(repo, {"file.txt": "base\n"})


def test_default_fallback_preserves_run_branch_from_default_seed(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    subprocess.run(
        ["git", "checkout", "-b", "feature/host"],
        cwd=repo, check=True, stdout=subprocess.PIPE,
    )

    plan = branching.resolve_publish_plan(repo, {}, {})

    assert plan.seed_ref == "main"
    assert plan.target_branch is None
    assert plan.source == "fallback:preserve"
    assert plan.host_context_branch == "feature/host"


def test_structured_event_branch_names_target_branch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    subprocess.run(
        ["git", "checkout", "-b", "feature/task"],
        cwd=repo, check=True, stdout=subprocess.PIPE,
    )

    plan = branching.resolve_publish_plan(
        repo,
        {"target_branch": "feature/task"},
        {},
    )

    assert plan.seed_ref == "feature/task"
    assert plan.target_branch == "feature/task"
    assert plan.source == "event:target_branch"
    # No remote in this repo, so the lease anchor stays empty.
    assert plan.expected_remote_oid is None


def test_conversation_branch_is_not_inferred(tmp_path):
    """Conversation history is no longer mined for publish authority.

    The agent reads recent records from the prompt and can switch
    branches at runtime; pre-decoding them as durable branch authority
    silently routed unrelated tasks onto stale sibling branches.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    plan = branching.resolve_publish_plan(repo, {}, {})

    assert plan.target_branch is None
    assert plan.source == "fallback:preserve"
    assert plan.seed_ref == "main"


def test_legacy_fallback_modes_downgrade_to_preserve(tmp_path, capsys):
    """The publish kernel only knows ``preserve``; legacy values
    (``current``, ``inbox``, ``default``) downgrade silently apart from
    a one-time warning so operators inheriting an old config see the
    change."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    subprocess.run(
        ["git", "checkout", "-b", "feature/host"],
        cwd=repo, check=True, stdout=subprocess.PIPE,
    )

    branching._LEGACY_FALLBACK_WARNED = False  # reset for clean capture
    plan = branching.resolve_publish_plan(
        repo, {}, {"branch.fallback": "current"},
    )

    assert plan.target_branch is None
    assert plan.source == "fallback:preserve"
    captured = capsys.readouterr()
    assert "branch.fallback" in captured.out
    assert "no longer supported" in captured.out


def test_legacy_branch_field_sentinels_skipped(tmp_path):
    """All legacy ``branch=`` sentinel values (``auto``, ``current``,
    ``none``) are no-ops now. ``task`` is just a normal branch name after
    the run rename."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    subprocess.run(
        ["git", "checkout", "-b", "feature/host"],
        cwd=repo, check=True, stdout=subprocess.PIPE,
    )

    for sentinel in ("auto", "current", "none"):
        plan = branching.resolve_publish_plan(repo, {"branch": sentinel}, {})
        assert plan.target_branch is None, sentinel
        assert plan.source == "fallback:preserve", sentinel

    task_plan = branching.resolve_publish_plan(repo, {"branch": "task"}, {})
    assert task_plan.target_branch == "task"
    assert task_plan.source == "event:branch"


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

    plan = branching.resolve_publish_plan(
        repo, {"branch_target": "feature/x"}, {},
    )

    remote_oid = subprocess.run(
        ["git", "rev-parse", "origin/feature/x"],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()

    assert plan.seed_ref == "origin/feature/x"
    assert plan.expected_remote_oid == remote_oid
    assert plan.target_branch == "feature/x"
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

    plan = branching.resolve_publish_plan(
        repo, {"branch_target": "feature/local-only"}, {},
    )

    assert plan.seed_ref == "feature/local-only"
    assert plan.source == "event:branch_target"
    assert plan.expected_remote_oid is None


def test_cross_task_freshness_sync_then_seed_from_remote(tmp_path):
    """Integration: after task A's refspec push advances ``feature/x`` on
    origin, task B's publish plan seeds from ``origin/feature/x`` so it
    sees the freshly published state — not the stale local tip.

    This locks in the freshness guarantee the operator flagged when we
    discussed dropping local-land: the daemon's pre-run sync + the
    resolver's ``prefer_remote`` together cover the case where the
    operator's local copy of a target branch lags the remote.
    """
    from brr import sync

    repo = _init_repo_with_origin(tmp_path)

    # Run A: agent stayed on brr/task-a; daemon does refspec push to
    # feature/x. Simulate that by pushing a brand-new commit via a
    # sibling clone so origin/feature/x exists with content the repo's
    # local feature/x does not have.
    sibling = tmp_path / "sibling"
    subprocess.run(
        ["git", "clone", str(tmp_path / "origin.git"), str(sibling)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=sibling, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=sibling, check=True,
    )
    subprocess.run(
        ["git", "checkout", "-b", "feature/x", "main"],
        cwd=sibling, check=True, stdout=subprocess.PIPE,
    )
    commit_files(sibling, {"taska.txt": "from task A\n"}, message="task A delivery")
    subprocess.run(
        ["git", "push", "-u", "origin", "feature/x"],
        cwd=sibling, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )

    # Local repo has no idea feature/x even exists yet.
    assert not (repo / "taska.txt").exists()

    # Pre-run sync (B): fetches origin so origin/feature/x becomes
    # visible. The local feature/x branch doesn't exist, so the ff
    # pass leaves no skip noise — the resolver does the real work.
    sync.refresh_before_run(repo, target_branches=["feature/x"], cfg={})

    plan = branching.resolve_publish_plan(
        repo, {"branch_target": "feature/x"}, {},
    )

    expected_oid = subprocess.run(
        ["git", "rev-parse", "origin/feature/x"],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert plan.seed_ref == "origin/feature/x"
    assert plan.expected_remote_oid == expected_oid
    # Sanity: a worktree sprouted from this seed sees task A's commit.
    show = subprocess.run(
        ["git", "show", f"{plan.seed_ref}:taska.txt"],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout
    assert show == "from task A\n"

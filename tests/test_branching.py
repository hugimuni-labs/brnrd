"""Tests for daemon publish plan resolution."""

from pathlib import Path

import pytest

from brr import branching, gitops

from _helpers import commit_files, init_git_repo


def _init_repo(repo: Path) -> None:
    init_git_repo(repo)
    commit_files(repo, {"file.txt": "base\n"})


def test_resolve_publish_plan_propagates_an_unresolvable_current_branch(
    tmp_path, monkeypatch,
):
    """#1340: this is a deliberate design choice, not an oversight — see
    the propagation comment on ``branching.resolve_publish_plan``'s own
    ``gitops.current_branch`` call. This runs from inside
    ``daemon._run_worker`` before any run work has started; that
    function's own outer crash handler turns an uncaught exception here
    into a cleanly deferred/errored run, which beats silently seeding a
    run off a host branch git couldn't actually name. Locked in here so a
    future "helpfully" added local try/except doesn't quietly change the
    failure mode back to a silent mis-seed.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    def _raise(_repo_root):
        raise gitops.CurrentBranchUnresolvable("simulated")

    monkeypatch.setattr(gitops, "current_branch", _raise)

    with pytest.raises(gitops.CurrentBranchUnresolvable):
        branching.resolve_publish_plan(repo, {}, {})



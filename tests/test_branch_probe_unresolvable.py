"""``worktree.current_branch`` refuses to report a measurement failure as a
detached HEAD (#1302, the git-probe audit follow-up to #1298).

Driven against real checkouts, not a mocked ``subprocess`` — the whole
defect is a *git* fact (does ``symbolic-ref`` fail because HEAD is
detached, or because git could not answer at all), and a mock agrees with
both the pre-fix and post-fix code equally happily. The two "does a
caller preserve rather than guess" tests do monkeypatch the now-raising
function directly — that half is about call-site behaviour, not the git
fact, and #1298's own suite (``test_strand_work_survives.py``) sets the
same precedent for its ``BaseUnresolvable`` callers.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from brr import branching, daemon, envs, worktree
from brr.run import Run

from _helpers import commit_files, init_git_repo


def _git(cwd, *args, check=True):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=check, capture_output=True, text=True,
    )


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_files(repo, {"README.md": "base\n"}, message="base")
    return repo


def _strand(run_id="run-1302-child", *, status="done"):
    return Run(
        id=run_id, event_id=f"evt-{run_id}", body="spec",
        env="worktree", status=status, meta={"strand": True},
    )


def _prepare(host_repo, task, plan):
    return envs.get_env("worktree").prepare(
        task, host_repo, {},
        branch_plan=plan,
        response_path=host_repo / ".brr" / "responses" / "evt.md",
    )


# ── the git facts the whole defect rests on ──────────────────────────


def test_a_true_detached_head_still_resolves_head_itself(repo):
    _git(repo, "checkout", "--detach", "-q")
    probe = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    assert probe.returncode != 0
    verify = _git(repo, "rev-parse", "--verify", "HEAD", check=False)
    assert verify.returncode == 0


def test_an_unreadable_tree_resolves_neither(tmp_path):
    ghost = tmp_path / "not-a-repo"
    ghost.mkdir()
    probe = _git(ghost, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    assert probe.returncode != 0
    verify = _git(ghost, "rev-parse", "--verify", "HEAD", check=False)
    assert verify.returncode != 0


# ── half 1: the probe distinguishes detached from unresolvable ───────


def test_current_branch_answers_a_normal_branch(repo):
    assert worktree.current_branch(repo) == "main"


def test_current_branch_reports_none_for_a_true_detached_head(repo):
    _git(repo, "checkout", "--detach", "-q")
    assert worktree.current_branch(repo) is None


def test_current_branch_raises_rather_than_reporting_detached(tmp_path):
    ghost = tmp_path / "not-a-repo"
    ghost.mkdir()
    with pytest.raises(worktree.BranchUnresolvable):
        worktree.current_branch(ghost)


def test_current_branch_still_answers_an_unborn_branch(tmp_path):
    """A fresh ``git init`` with no commits: ``symbolic-ref`` succeeds (the
    ref file names ``main`` even though it doesn't resolve to a commit yet)
    — this must not be mistaken for either detached or unresolvable.
    """
    fresh = tmp_path / "fresh"
    init_git_repo(fresh)
    assert worktree.current_branch(fresh) == "main"


# ── half 2: both callers preserve rather than guess ───────────────────


def test_finalize_keeps_the_worktree_when_the_branch_is_unresolvable(
    repo, tmp_path, monkeypatch,
):
    """The neuter: pre-#1302, an unresolvable branch and a real detached
    HEAD were the same ``None`` to ``_resolve_outcome``, so "cannot tell"
    only happened to render safely by coincidence (both readings keep the
    worktree, ``keep_reason="detached HEAD"``). This drives the actual
    exception through the real call site and checks the outcome is still
    the safe one, distinguishably labelled.
    """
    plan = branching.resolve_publish_plan(repo, {}, {})
    task = _strand()
    ctx = _prepare(repo, task, plan)
    clone = Path(ctx.env_state["worktree_path"])

    def _raise(_path):
        raise worktree.BranchUnresolvable("simulated: git would not answer")

    monkeypatch.setattr(worktree, "current_branch", _raise)

    envs.get_env("worktree").finalize(ctx, task, tmp_path / "runs")

    assert task.meta["publish_status"] == "detached"
    assert "publish_branch" not in task.meta
    assert clone.exists(), "a run git could not read must never be deleted"


def test_salvage_declines_loudly_rather_than_silently_when_unresolvable(
    tmp_path, monkeypatch, capsys,
):
    """The daemon salvage path (#1302) — the sibling of the #1298 fix in the
    loss-prevention family. Before this fix, an unresolvable branch and a
    genuine detached HEAD both hit the same silent ``return`` behind a
    comment claiming "detached HEAD" — a lie for the unresolvable case.
    Outcome is unchanged (still no branch to publish); the operator now
    sees why.
    """
    run_root = tmp_path / "run"
    run_root.mkdir()

    def _raise(_path):
        raise worktree.BranchUnresolvable("simulated: git would not answer")

    monkeypatch.setattr(worktree, "current_branch", _raise)

    class _Ctx:
        cwd = run_root

    task = _strand(status="failed")
    daemon._capture_worktree(
        task, _Ctx(), branch_plan=None, cfg={}, runs_dir=tmp_path / "runs",
    )
    captured = capsys.readouterr()
    assert "branch unresolvable" in captured.out
    assert task.meta.get("publish_branch") is None


def test_salvage_still_declines_silently_correct_for_a_true_detached_head(
    repo, tmp_path, capsys,
):
    """Same call site, the other arm: a real detached HEAD still declines
    without the new diagnostic line — it was never the ambiguous case.
    """
    _git(repo, "checkout", "--detach", "-q")

    task = _strand(status="failed")

    class _Ctx:
        cwd = repo

    daemon._capture_worktree(
        task, _Ctx(), branch_plan=None, cfg={}, runs_dir=tmp_path / "runs",
    )
    captured = capsys.readouterr()
    assert "branch unresolvable" not in captured.out
    assert task.meta.get("publish_branch") is None

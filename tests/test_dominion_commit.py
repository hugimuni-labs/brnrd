"""Tests for serialized dominion capture (slice 5a).

The persistence half of the agent-as-memory model: the resident writes
into ``.brr/dominion/`` during a thought and brr captures those edits at
sleep, with the commit step serialized across processes so an overlapping
thought and an ad-hoc session never race the shared worktree's git index.
See ``kb/design-agent-dominion.md`` §4.
"""

from __future__ import annotations

import subprocess

from brr import daemon, dominion, gitops
from brr.run import Run

from _helpers import (
    commit_files,
    init_git_repo,
)


def _repo(tmp_path, name="repo"):
    repo = tmp_path / name
    init_git_repo(repo)
    commit_files(repo, {"README.md": "main\n"}, message="init main")
    (repo / ".brr").mkdir()
    return repo


def _clone(remote, dest, *, name):
    subprocess.run(
        ["git", "clone", str(remote), str(dest)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    subprocess.run(["git", "-C", str(dest), "config", "user.name", name], check=True)
    subprocess.run(
        ["git", "-C", str(dest), "config", "user.email", f"{name}@e.com"], check=True,
    )
    (dest / ".brr").mkdir()
    return dest


def _bare_remote(tmp_path):
    seed = tmp_path / "seed"
    init_git_repo(seed)
    commit_files(seed, {"README.md": "main\n"}, message="init")
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "clone", "--bare", str(seed), str(remote)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return remote


def test_commit_noop_on_clean_dominion(tmp_path):
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)
    head_before = gitops.rev_parse(path, "HEAD")
    # Most thoughts never touch the dominion — a clean worktree is a
    # silent no-op, not an empty commit.
    assert dominion.commit(path, "nothing to capture") is False
    assert gitops.rev_parse(path, "HEAD") == head_before


def test_commit_captures_dirty_dominion(tmp_path):
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)
    head_before = gitops.rev_parse(path, "HEAD")
    (path / "pain.md").write_text("slow rebuild keeps biting\n", encoding="utf-8")

    assert dominion.commit(path, "capture pain") is True
    assert gitops.rev_parse(path, "HEAD") != head_before
    # The write is committed: tree is clean afterward.
    assert not gitops.worktree_dirty(path)


def test_capture_dominion_helper_commits_when_dirty(tmp_path):
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)
    (path / "focus.md").write_text("current focus\n", encoding="utf-8")
    head_before = gitops.rev_parse(path, "HEAD")

    task = Run(id="t1", event_id="e1", body="b", source="telegram")
    daemon._capture_dominion(repo, {"dominion.push_on_capture": False}, task)

    assert gitops.rev_parse(path, "HEAD") != head_before
    assert not gitops.worktree_dirty(path)


def test_capture_dominion_helper_survives_branch_probe_failure(
    tmp_path, monkeypatch,
):
    """#1340: daemon._capture_dominion has no local try/except of its own
    around the ``gitops.current_branch`` call — a raise there, uncaught,
    would abort every remaining dominion candidate's capture in the same
    loop (and, worse, would very likely surface as an uncaught
    "worker_crash" for what is purely post-run housekeeping). The commit
    itself must still happen even when the branch name can't be measured.
    """
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)
    (path / "focus.md").write_text("current focus\n", encoding="utf-8")
    head_before = gitops.rev_parse(path, "HEAD")

    def _raise(_repo_root):
        raise gitops.CurrentBranchUnresolvable("simulated")

    monkeypatch.setattr(gitops, "current_branch", _raise)

    task = Run(id="t1", event_id="e1", body="b", source="telegram")
    daemon._capture_dominion(repo, {"dominion.push_on_capture": False}, task)

    assert gitops.rev_parse(path, "HEAD") != head_before
    assert not gitops.worktree_dirty(path)


def test_capture_dominion_helper_respects_disabled(tmp_path):
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)
    (path / "x.md").write_text("y\n", encoding="utf-8")
    head_before = gitops.rev_parse(path, "HEAD")

    task = Run(id="t1", event_id="e1", body="b", source="telegram")
    daemon._capture_dominion(repo, {"dominion.enabled": False}, task)

    # Disabled → left untouched for the operator to manage by hand.
    assert gitops.rev_parse(path, "HEAD") == head_before
    assert gitops.worktree_dirty(path)


# ── Agent-owned sync: the needs-sync marker (slice 7b) ───────────────


def test_commit_clears_marker_on_successful_push(tmp_path):
    remote = _bare_remote(tmp_path)
    clone = _clone(remote, tmp_path / "a", name="A")
    path = dominion.ensure_dominion(clone, push=True)
    brr_dir = path.parent
    dominion.mark_needs_sync(brr_dir, "stale flag from a past failure")

    (path / "note.md").write_text("fresh\n", encoding="utf-8")
    assert dominion.commit(
        path, "capture", remote=gitops.default_remote(clone),
        branch="brr-home", push=True,
    ) is True
    # A successful push means we're in sync — the stale marker is gone.
    assert dominion.needs_sync(brr_dir) is None


def test_commit_marks_needs_sync_on_rejected_push(tmp_path):
    remote = _bare_remote(tmp_path)
    # A publishes the dominion.
    clone_a = _clone(remote, tmp_path / "a", name="A")
    path_a = dominion.ensure_dominion(clone_a, push=True)
    # B reconstitutes it and advances brr-home on the shared remote.
    clone_b = _clone(remote, tmp_path / "b", name="B")
    path_b = dominion.ensure_dominion(clone_b, push=False)
    (path_b / "from-b.md").write_text("b was here\n", encoding="utf-8")
    assert dominion.commit(
        path_b, "B writes", remote=gitops.default_remote(clone_b),
        branch="brr-home", push=True,
    ) is True

    # A now commits + pushes from a stale base — the push is rejected.
    (path_a / "from-a.md").write_text("a was here\n", encoding="utf-8")
    committed = dominion.commit(
        path_a, "A writes", remote=gitops.default_remote(clone_a),
        branch="brr-home", push=True,
    )
    assert committed is True  # the local commit (durability floor) still happens
    reason = dominion.needs_sync(path_a.parent)
    assert reason is not None
    assert "diverged" in reason
    assert "fetch / merge / push" in reason


# ── Never-linked: no remote at all, not a push failure (#1423) ───────


def test_commit_clears_never_linked_once_a_remote_is_wired(tmp_path):
    remote = _bare_remote(tmp_path)
    clone = _clone(remote, tmp_path / "a", name="A")
    path = dominion.ensure_dominion(clone, push=True)
    brr_dir = path.parent
    # Simulate a prior wake that found no remote at all.
    dominion.mark_never_linked(brr_dir)
    (path / "note.md").write_text("fresh\n", encoding="utf-8")

    assert dominion.commit(
        path, "capture", remote=gitops.default_remote(clone),
        branch="brr-home", push=True,
    ) is True

    assert dominion.never_linked(brr_dir, path) is None


# ── #1340: a current_branch probe failure must not mask a real commit ──


def test_commit_reports_the_real_commit_even_when_branch_probe_fails(
    tmp_path, monkeypatch,
):
    """``branch=None`` + ``push=True`` makes ``commit`` resolve the push
    target itself via ``gitops.current_branch`` (dominion.py, the ``target
    = branch or gitops.current_branch(...)`` line). Before #1340, a git
    failure there answered "HEAD" and the ``target != "HEAD"`` guard just
    skipped the push, leaving ``committed``'s real value to reach the
    return. A unit test on ``current_branch`` alone can't show this site
    specifically — only calling through ``dominion.commit`` with the probe
    actually raising can.
    """
    remote = _bare_remote(tmp_path)
    clone = _clone(remote, tmp_path / "a", name="A")
    path = dominion.ensure_dominion(clone, push=True)
    (path / "note.md").write_text("fresh\n", encoding="utf-8")

    def _raise(_repo_root):
        raise gitops.CurrentBranchUnresolvable("simulated")

    monkeypatch.setattr(gitops, "current_branch", _raise)

    # branch=None (not supplied) forces the internal current_branch() call.
    committed = dominion.commit(
        path, "capture", remote=gitops.default_remote(clone), push=True,
    )
    assert committed is True  # the local commit is real and must be reported
    assert not gitops.worktree_dirty(path)  # the commit actually landed

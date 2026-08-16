"""Tests for serialized dominion capture (slice 5a).

The persistence half of the agent-as-memory model: the resident writes
into ``.brr/dominion/`` during a thought and brr captures those edits at
sleep, with the commit step serialized across processes so an overlapping
thought and an ad-hoc session never race the shared worktree's git index.
See ``kb/design-agent-dominion.md`` §4.
"""

from __future__ import annotations

import fcntl
import os
import subprocess

from brr import daemon, dominion, gitops
from brr.run import Run

from _helpers import (
    commit_files,
    init_git_repo,
    rejecting_git_http_remote,
    unreachable_git_http_remote,
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


def test_commit_serializes_on_held_lock(tmp_path):
    # A second committer that can't take the lock within its timeout skips
    # rather than racing the index — the pending write stays for a later
    # pass (no corruption, no loss).
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)
    (path / "note.md").write_text("pending\n", encoding="utf-8")

    lock_path = path.parent / dominion.COMMIT_LOCK_FILE
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert dominion.commit(path, "blocked", lock_timeout=0.2) is False
        assert gitops.worktree_dirty(path)  # write still pending
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    # Lock released — the write is captured on the next attempt.
    assert dominion.commit(path, "after release") is True
    assert not gitops.worktree_dirty(path)


def test_commit_missing_dominion_is_false(tmp_path):
    repo = _repo(tmp_path)
    assert dominion.commit(repo / ".brr" / "dominion", "no dominion") is False


def test_capture_dominion_helper_commits_when_dirty(tmp_path):
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)
    (path / "focus.md").write_text("current focus\n", encoding="utf-8")
    head_before = gitops.rev_parse(path, "HEAD")

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


def test_sync_marker_round_trip(tmp_path):
    brr_dir = tmp_path / ".brr"
    brr_dir.mkdir()
    assert dominion.needs_sync(brr_dir) is None
    dominion.mark_needs_sync(brr_dir, "remote diverged")
    assert dominion.needs_sync(brr_dir) == "remote diverged"
    dominion.clear_needs_sync(brr_dir)
    assert dominion.needs_sync(brr_dir) is None


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


def test_commit_marks_auth_failure_without_inventing_divergence(
    tmp_path, monkeypatch,
):
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    with rejecting_git_http_remote() as url:
        subprocess.run(["git", "remote", "add", "private", url], cwd=repo, check=True)
        (path / "note.md").write_text("capture me\n", encoding="utf-8")
        assert dominion.commit(
            path, "capture", remote="private", branch="brr-home", push=True,
        ) is True

    reason = dominion.needs_sync(path.parent) or ""
    assert f"failed authentication against {url} over HTTP" in reason
    assert str(path) in reason
    assert "diverged" not in reason
    assert "merge" not in reason


def test_commit_marks_unreachable_without_prescribing_a_merge(tmp_path):
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)
    url = unreachable_git_http_remote()
    subprocess.run(["git", "remote", "add", "offline", url], cwd=repo, check=True)
    (path / "note.md").write_text("capture me\n", encoding="utf-8")

    assert dominion.commit(
        path, "capture", remote="offline", branch="brr-home", push=True,
    ) is True

    reason = dominion.needs_sync(path.parent) or ""
    assert f"could not reach {url} over HTTP" in reason
    assert str(path) in reason
    assert "diverged" not in reason
    assert "merge" not in reason


def test_commit_marks_unclassified_push_failure_as_unclassified(tmp_path):
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)
    missing = tmp_path / "missing.git"
    subprocess.run(
        ["git", "remote", "add", "broken", str(missing)], cwd=repo, check=True,
    )
    (path / "note.md").write_text("capture me\n", encoding="utf-8")

    assert dominion.commit(
        path, "capture", remote="broken", branch="brr-home", push=True,
    ) is True

    reason = dominion.needs_sync(path.parent) or ""
    assert f"unclassified reason against {missing} over local" in reason
    assert str(path) in reason
    assert "diverged" not in reason


# ── Never-linked: no remote at all, not a push failure (#1423) ───────


def test_never_linked_marker_round_trip(tmp_path):
    brr_dir = tmp_path / ".brr"
    brr_dir.mkdir()
    assert dominion.never_linked(brr_dir, tmp_path) is None
    dominion.mark_never_linked(brr_dir)
    # The raw marker is set, but the getter is gated on the repo carrying
    # real content — an unrelated empty tmp_path has 0 commits.
    assert dominion.never_linked(brr_dir, tmp_path) is None
    dominion.clear_never_linked(brr_dir)


def test_ensure_dominion_marks_never_linked_but_getter_stays_quiet_at_birth(
    tmp_path,
):
    """A freshly-seeded dominion has no remote — the marker is written
    honestly at birth, but the getter withholds it until there is real
    memory to warn about. (The legacy repo-local dominion seeds *two*
    commits at birth — an orphan root plus a seed commit — so this asserts
    the getter's own behaviour, not a specific founding count.)"""
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)

    # This first look is also what anchors the baseline every later look
    # compares against (`gitops.read_or_seed_baseline`) — production reads
    # it the same way, via the very next wake's prompt build, before that
    # wake's own capture has committed anything new.
    assert dominion.never_linked(path.parent, path) is None


def test_commit_marks_never_linked_once_real_content_follows_birth(tmp_path):
    """The ongoing capture path (dominion.commit, called after every
    thought) is where the marker becomes visible: once the dominion holds
    a real commit *beyond* the one the birth-anchored baseline saw, and
    still has no remote, the getter reports it."""
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)
    # Anchor the baseline at the pristine, just-born state (see the birth
    # test above) before any real content exists.
    assert dominion.never_linked(path.parent, path) is None

    (path / "pain.md").write_text("slow rebuild keeps biting\n", encoding="utf-8")
    assert dominion.commit(path, "capture pain", remote=None, push=False) is True

    reason = dominion.never_linked(path.parent, path)
    assert reason is not None
    assert "brnrd home link" in reason
    assert "no git remote is configured" in reason


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


def test_commit_marks_never_linked_even_when_push_is_off(tmp_path):
    """The daemon's real caller ANDs ``push`` with ``bool(remote)`` before
    calling in — so a no-remote capture always arrives with ``push=False``.
    The marking logic must not be gated on ``push``, or it never fires in
    production (#1423's fix shape names this exact site)."""
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)
    assert dominion.never_linked(path.parent, path) is None  # anchors baseline

    (path / "note.md").write_text("more\n", encoding="utf-8")
    dominion.commit(path, "capture one", remote=None, push=False)
    (path / "note2.md").write_text("even more\n", encoding="utf-8")

    assert dominion.commit(path, "capture two", remote=None, push=False) is True

    assert dominion.never_linked(path.parent, path) is not None

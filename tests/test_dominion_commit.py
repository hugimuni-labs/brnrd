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

from brr import daemon, dominion, gitops
from brr.task import Task

from _helpers import commit_files, init_git_repo


def _repo(tmp_path, name="repo"):
    repo = tmp_path / name
    init_git_repo(repo)
    commit_files(repo, {"README.md": "main\n"}, message="init main")
    (repo / ".brr").mkdir()
    return repo


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

    task = Task(id="t1", event_id="e1", body="b", source="telegram")
    daemon._capture_dominion(repo, {"dominion.push_on_capture": False}, task)

    assert gitops.rev_parse(path, "HEAD") != head_before
    assert not gitops.worktree_dirty(path)


def test_capture_dominion_helper_respects_disabled(tmp_path):
    repo = _repo(tmp_path)
    path = dominion.ensure_dominion(repo, push=False)
    (path / "x.md").write_text("y\n", encoding="utf-8")
    head_before = gitops.rev_parse(path, "HEAD")

    task = Task(id="t1", event_id="e1", body="b", source="telegram")
    daemon._capture_dominion(repo, {"dominion.enabled": False}, task)

    # Disabled → left untouched for the operator to manage by hand.
    assert gitops.rev_parse(path, "HEAD") == head_before
    assert gitops.worktree_dirty(path)

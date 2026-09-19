"""The screen says which code it is serving — and when that code is stale.

A long-running loom server holds the modules it imported at start. Editing a
source file changes nothing it serves until it restarts. The maintainer hit
this on 2026-09-19 — *"where do we serve the updated ui? field6.html serves
the old version apparently"* — and so did the seat, on a server running out
of a strand worktree git had already unregistered.

The repair is not hot-reload. A daemon that swaps its own code mid-beat is a
worse problem than a stale one. The repair is **saying so**.
"""

from __future__ import annotations

import time

import pytest

from brr.loom import state


@pytest.fixture()
def fresh(monkeypatch):
    """A server that started just now: nothing on disk can be newer."""
    monkeypatch.setattr(state, "_PROCESS_STARTED", time.time() + 5)


@pytest.fixture()
def elderly(monkeypatch):
    """A server that started an hour ago: the tree has moved under it."""
    monkeypatch.setattr(state, "_PROCESS_STARTED", time.time() - 3600)


def test_a_fresh_process_is_not_stale(tmp_path, fresh):
    assert state.read_build(tmp_path)["stale"] is False


def test_a_process_older_than_its_source_is_stale(tmp_path, elderly):
    """The defect, stated as a measurement rather than a guess.

    `stale` is exactly *a watched source file is newer than the moment this
    process imported its code* — which is precisely the condition under
    which what you are reading is not what you wrote.
    """
    assert state.read_build(tmp_path)["stale"] is True


def test_the_commit_is_read_through_a_symbolic_head(tmp_path, fresh):
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git / "refs" / "heads" / "main").write_text("a" * 40 + "\n", encoding="utf-8")
    assert state.read_build(tmp_path)["commit"] == "a" * 12


def test_a_detached_head_reads_too(tmp_path, fresh):
    """A worktree on a detached HEAD is exactly how field7 was served."""
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text("b" * 40 + "\n", encoding="utf-8")
    assert state.read_build(tmp_path)["commit"] == "b" * 12


def test_no_git_is_none_not_a_guess(tmp_path, fresh):
    assert state.read_build(tmp_path)["commit"] is None


def test_a_dangling_ref_does_not_raise(tmp_path, fresh):
    """HEAD points at a ref file that does not exist — read, don't crash."""
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text("ref: refs/heads/gone\n", encoding="utf-8")
    assert state.read_build(tmp_path)["commit"] is None


def test_the_facet_is_in_the_feed_contract(tmp_path):
    assert "build" in state.KEYS
    built = state.build(tmp_path, None)
    assert tuple(built.keys()) == state.KEYS
    assert "stale" in built["build"]


def test_the_feed_never_dies_on_an_unreadable_tree(tmp_path, monkeypatch):
    """The build facet is a convenience; it must not be able to blank a screen."""
    monkeypatch.setattr(state, "_newest_source_mtime", lambda _: (_ for _ in ()).throw(OSError("nope")))
    assert state.build(tmp_path, None)["build"]["stale"] is False

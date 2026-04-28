"""Tests for stream-aware status and brr streams/stream show output."""

from __future__ import annotations

import subprocess

import pytest

from brr import stream as stream_mod, status as status_mod
from brr.task import Task


def _init_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=repo, check=True)
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    return repo


def _seed_stream(brr_dir, **kwargs):
    defaults = dict(
        id="stream-test-1",
        title="Refactor auth",
        status="active",
        intent="Make login testable",
        summary="Found coupling",
        gate_context={"source": "telegram", "telegram_chat_id": 7},
        reply_route={
            "preferred": "input_gate",
            "selected": "input_gate",
            "allowed": ["input_gate", "git_pr"],
        },
    )
    defaults.update(kwargs)
    manifest = stream_mod.StreamManifest(**defaults)
    stream_mod.save_manifest(brr_dir, manifest)
    return manifest


def test_list_streams_renders_manifest_summary(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    brr_dir = repo / ".brr"
    _seed_stream(brr_dir, id="stream-a", title="Stream A")
    _seed_stream(brr_dir, id="stream-b", title="Stream B", status="paused")

    out = status_mod.list_streams()
    assert "stream-a" in out
    assert "stream-b" in out
    assert "Stream A" in out
    assert "[active]" in out
    assert "[paused]" in out


def test_list_streams_when_empty(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    out = status_mod.list_streams()
    assert "No streams" in out


def test_show_stream_renders_full_view(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    brr_dir = repo / ".brr"
    manifest = _seed_stream(
        brr_dir,
        id="stream-show-1",
        open_questions="Keep cookie fallback?",
    )
    stream_mod.append_task(
        brr_dir, manifest.id,
        task_id="task-1", event_id="evt-1",
        branch="auto", env="worktree", status="done",
        base_branch="main", branch_name="brr/task-1",
    )
    stream_mod.append_artifact(
        brr_dir, manifest.id,
        kind="response", path="/tmp/out.md",
        task_id="task-1", label="response:evt-1",
    )

    out = status_mod.show_stream("stream-show-1")
    assert "stream-show-1" in out
    assert "Refactor auth" in out
    assert "Make login testable" in out
    assert "Keep cookie fallback" in out
    assert "preferred=input_gate" in out
    assert "telegram_chat_id=7" in out
    assert "Tasks (1)" in out
    assert "task-1" in out
    assert "Artifacts (1)" in out
    assert "response:evt-1" in out


def test_show_stream_uses_current_task_status(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    brr_dir = repo / ".brr"
    manifest = _seed_stream(brr_dir, id="stream-stale-task")
    stream_mod.append_task(
        brr_dir, manifest.id,
        task_id="task-1", event_id="evt-1",
        branch="auto", env="worktree", status="running",
        base_branch="main", branch_name="brr/task-1",
    )
    Task(
        id="task-1",
        event_id="evt-1",
        body="do the work",
        branch="auto",
        env="worktree",
        status="done",
        stream_id=manifest.id,
    ).save(brr_dir / "tasks")

    out = status_mod.show_stream("stream-stale-task")
    assert "task-1 [done] auto/worktree" in out
    assert "task-1 [running]" not in out


def test_show_stream_partial_match(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    brr_dir = repo / ".brr"
    _seed_stream(brr_dir, id="stream-uniq-9999")

    out = status_mod.show_stream("9999")
    assert "stream-uniq-9999" in out


def test_show_stream_not_found(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    out = status_mod.show_stream("nope")
    assert "No stream matching" in out


def test_get_status_lists_active_streams(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    brr_dir = repo / ".brr"
    _seed_stream(brr_dir, id="stream-active-1", title="Live work")
    _seed_stream(brr_dir, id="stream-archived", status="archived")

    out = status_mod.get_status()
    assert "streams: 1 active" in out
    assert "stream-active-1" in out
    assert "Live work" in out
    assert "stream-archived" not in out

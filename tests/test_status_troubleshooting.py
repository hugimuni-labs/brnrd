"""Tests for the shrunken, troubleshooting-focused status module."""

from __future__ import annotations

import subprocess

from brr import conversations, status as status_mod, updates
from brr.task import Task


def _init_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True,
                   stdout=subprocess.PIPE)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=repo, check=True)
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True,
                   stdout=subprocess.PIPE)
    return repo


def test_get_status_shows_active_run_progress(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    brr_dir = repo / ".brr"
    key = "telegram:7:"
    conversations.append_task(
        brr_dir, key,
        task_id="task-active", event_id="evt-active",
        branch="auto", env="docker", status="running",
        base_branch="main", branch_name="brr/task-active",
    )
    updates.emit(brr_dir, updates.UpdatePacket(
        type="task_created", conversation_key=key,
        payload={"task_id": "task-active", "branch": "auto", "env": "docker"},
    ))
    updates.emit(brr_dir, updates.UpdatePacket(
        type="run_started", conversation_key=key,
        payload={"task_id": "task-active"},
    ))

    out = status_mod.get_status()
    assert "active task:" in out
    assert "task-active" in out
    assert "phase: running" in out


def test_get_status_omits_active_block_when_terminal(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(repo)
    brr_dir = repo / ".brr"
    key = "telegram:8:"
    conversations.append_task(
        brr_dir, key,
        task_id="task-done", event_id="evt-done",
        branch="current", env="host", status="done",
    )
    updates.emit(brr_dir, updates.UpdatePacket(
        type="task_created", conversation_key=key,
        payload={"task_id": "task-done", "branch": "current", "env": "host"},
    ))
    updates.emit(brr_dir, updates.UpdatePacket(
        type="done", conversation_key=key,
        payload={"task_id": "task-done"},
    ))

    out = status_mod.get_status()
    assert "active task:" not in out


def test_inspect_task_lists_preserved_docker_containers(tmp_path):
    brr_dir = tmp_path / ".brr"
    tasks_dir = brr_dir / "tasks"
    tasks_dir.mkdir(parents=True)
    (brr_dir / "responses").mkdir(parents=True)
    (brr_dir / "inbox").mkdir(parents=True)

    task = Task(
        id="task-docker-keep",
        event_id="evt-keep",
        body="kept",
        status="error",
        meta={"docker_containers": "brr-task-docker-keep-attempt-1"},
    )
    task.save(tasks_dir)

    out = status_mod.inspect_task("task-docker-keep", tmp_path)
    assert "Docker containers (preserved):" in out
    assert "brr-task-docker-keep-attempt-1" in out

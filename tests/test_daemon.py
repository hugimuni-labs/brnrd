"""Tests for daemon task and event status handling."""

from pathlib import Path

import pytest

from brr import daemon
from brr.task import Task


def test_start_preserves_needs_context_event_status(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    event = {"id": "evt-1", "status": "pending", "_path": tmp_path / ".brr" / "inbox" / "evt-1.md"}
    event["_path"].write_text("---\nid: evt-1\nstatus: pending\n---\nhelp\n", encoding="utf-8")
    statuses = []

    monkeypatch.setattr(daemon, "read_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_write_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_clear_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_start_gates", lambda *_args: [])
    monkeypatch.setattr(daemon.conf, "load_config", lambda _repo_root: {})
    monkeypatch.setattr(
        daemon.protocol,
        "list_pending",
        lambda _inbox_dir: [event] if not statuses else [],
    )
    monkeypatch.setattr(daemon.protocol, "set_status", lambda _event, status: statuses.append(status))
    monkeypatch.setattr(
        daemon,
        "_run_worker",
        lambda *_args: Task(id="task-1", event_id="evt-1", body="help", status="needs_context"),
    )
    monkeypatch.setattr(daemon, "_push_if_needed", _stop_after_first_push)
    monkeypatch.setattr(daemon.signal, "signal", lambda *_args: None)

    with pytest.raises(StopIteration):
        daemon.start(tmp_path)

    assert statuses == ["processing", "needs_context"]


def test_start_preserves_error_event_status(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    event = {"id": "evt-2", "status": "pending", "_path": tmp_path / ".brr" / "inbox" / "evt-2.md"}
    event["_path"].write_text("---\nid: evt-2\nstatus: pending\n---\nhelp\n", encoding="utf-8")
    statuses = []

    monkeypatch.setattr(daemon, "read_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_write_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_clear_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_start_gates", lambda *_args: [])
    monkeypatch.setattr(daemon.conf, "load_config", lambda _repo_root: {})
    monkeypatch.setattr(
        daemon.protocol,
        "list_pending",
        lambda _inbox_dir: [event] if not statuses else [],
    )
    monkeypatch.setattr(daemon.protocol, "set_status", lambda _event, status: statuses.append(status))
    monkeypatch.setattr(
        daemon,
        "_run_worker",
        lambda *_args: Task(id="task-2", event_id="evt-2", body="help", status="error"),
    )
    monkeypatch.setattr(daemon, "_push_if_needed", _stop_after_first_push)
    monkeypatch.setattr(daemon.signal, "signal", lambda *_args: None)

    with pytest.raises(StopIteration):
        daemon.start(tmp_path)

    assert statuses == ["processing", "error"]


def test_run_worker_uses_triage_output_for_task(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    event = {
        "id": "evt-3",
        "status": "pending",
        "body": "raw event body",
        "source": "telegram",
        "_path": tmp_path / ".brr" / "inbox" / "evt-3.md",
    }
    event["_path"].write_text(
        "---\nid: evt-3\nstatus: pending\nsource: telegram\n---\nraw event body\n",
        encoding="utf-8",
    )

    calls = []

    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _repo_root: "codex")
    monkeypatch.setattr(
        daemon.runner,
        "build_triage_prompt",
        lambda body, event_id, _repo_root: f"TRIAGE {event_id}: {body}",
    )
    monkeypatch.setattr(
        daemon.runner,
        "build_daemon_prompt",
        lambda task, event_id, response_path, _repo_root: (
            f"RUN {event_id}: {task} -> {response_path}"
        ),
    )
    monkeypatch.setattr(daemon.gitops, "branch_exists", lambda *_args: False)
    monkeypatch.setattr(
        daemon.worktree,
        "create",
        lambda *_args, **_kwargs: tmp_path / ".brr" / "worktrees" / "task-worktree",
    )
    monkeypatch.setattr(
        daemon.gitops,
        "merge_branch",
        lambda _repo_root, branch, message=None: daemon.gitops.MergeResult(
            success=True, branch=branch, commit="abc123",
        ),
    )
    monkeypatch.setattr(daemon.worktree, "remove", lambda *_args, **_kwargs: None)

    def fake_run_executor(runner_name, prompt, cwd=None, cfg=None, response_path=None):
        calls.append((runner_name, prompt, response_path))
        if prompt.startswith("TRIAGE"):
            return "---\nbranch: auto\nenv: worktree\n---\nrefined task body\n"
        Path(response_path).write_text("---\n---\nall done\n", encoding="utf-8")
        return "ok"

    monkeypatch.setattr(daemon.runner, "run_executor", fake_run_executor)

    task = daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    assert task.status == "done"
    assert task.body == "refined task body"
    assert task.branch == "auto"
    assert task.env == "worktree"
    assert calls[0][1] == "TRIAGE evt-3: raw event body"
    assert "refined task body" in calls[1][1]

    persisted = Task.from_file(tmp_path / ".brr" / "tasks" / f"{task.id}.md")
    assert persisted is not None
    assert persisted.branch == "auto"
    assert persisted.env == "worktree"
    assert persisted.status == "done"


def test_run_worker_executes_worktree_tasks_in_worktree_and_merges(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    event = {
        "id": "evt-5",
        "status": "pending",
        "body": "raw event body",
        "source": "telegram",
        "_path": tmp_path / ".brr" / "inbox" / "evt-5.md",
    }
    event["_path"].write_text(
        "---\nid: evt-5\nstatus: pending\nsource: telegram\n---\nraw event body\n",
        encoding="utf-8",
    )

    worktree_path = tmp_path / ".brr" / "worktrees" / "task-1"
    calls = []
    merges = []
    removals = []

    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _repo_root: "codex")
    monkeypatch.setattr(
        daemon.runner,
        "build_triage_prompt",
        lambda body, event_id, _repo_root: f"TRIAGE {event_id}: {body}",
    )
    monkeypatch.setattr(
        daemon.runner,
        "build_daemon_prompt",
        lambda task, event_id, response_path, prompt_root: (
            f"RUN {event_id}: {task} @ {prompt_root} -> {response_path}"
        ),
    )
    monkeypatch.setattr(daemon.gitops, "branch_exists", lambda *_args: False)
    monkeypatch.setattr(
        daemon.worktree,
        "create",
        lambda *_args, **_kwargs: worktree_path,
    )
    monkeypatch.setattr(
        daemon.gitops,
        "merge_branch",
        lambda _repo_root, branch, message=None: merges.append((branch, message)) or
        daemon.gitops.MergeResult(success=True, branch=branch, commit="abc123"),
    )
    monkeypatch.setattr(
        daemon.worktree,
        "remove",
        lambda _repo_root, task_id, **kwargs: removals.append((task_id, kwargs)),
    )

    def fake_run_executor(runner_name, prompt, cwd=None, cfg=None, response_path=None):
        calls.append((runner_name, prompt, cwd, response_path))
        if prompt.startswith("TRIAGE"):
            return "---\nid: ignored\nbranch: auto\nenv: worktree\n---\nrefined task body\n"
        Path(response_path).write_text("---\n---\nall done\n", encoding="utf-8")
        return "ok"

    monkeypatch.setattr(daemon.runner, "run_executor", fake_run_executor)

    task = daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    assert task.status == "done"
    assert calls[1][2] == worktree_path
    assert f"@ {worktree_path} ->" in calls[1][1]
    assert len(merges) == 1
    assert merges[0][0] == task.resolve_branch_name()
    assert merges[0][1] == f"merge {task.resolve_branch_name()} for {task.id}"
    assert removals == [(task.id, {"branch": task.resolve_branch_name(), "delete_branch": True})]


def test_run_worker_marks_error_on_invalid_triage_output(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    event = {
        "id": "evt-4",
        "status": "pending",
        "body": "raw event body",
        "source": "telegram",
        "_path": tmp_path / ".brr" / "inbox" / "evt-4.md",
    }
    event["_path"].write_text(
        "---\nid: evt-4\nstatus: pending\nsource: telegram\n---\nraw event body\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _repo_root: "codex")
    monkeypatch.setattr(
        daemon.runner,
        "build_triage_prompt",
        lambda body, event_id, _repo_root: f"TRIAGE {event_id}: {body}",
    )
    monkeypatch.setattr(
        daemon.runner,
        "run_executor",
        lambda *args, **kwargs: "not a task file",
    )

    task = daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    assert task.status == "error"
    assert task.body == "raw event body"
    persisted = Task.from_file(tmp_path / ".brr" / "tasks" / f"{task.id}.md")
    assert persisted is not None
    assert persisted.status == "error"


def test_run_worker_preserves_named_branch_without_merge(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    event = {
        "id": "evt-6",
        "status": "pending",
        "body": "raw event body",
        "source": "telegram",
        "_path": tmp_path / ".brr" / "inbox" / "evt-6.md",
    }
    event["_path"].write_text(
        "---\nid: evt-6\nstatus: pending\nsource: telegram\n---\nraw event body\n",
        encoding="utf-8",
    )

    worktree_path = tmp_path / ".brr" / "worktrees" / "task-2"
    merges = []
    removals = []

    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _repo_root: "codex")
    monkeypatch.setattr(
        daemon.runner,
        "build_triage_prompt",
        lambda body, event_id, _repo_root: f"TRIAGE {event_id}: {body}",
    )
    monkeypatch.setattr(
        daemon.runner,
        "build_daemon_prompt",
        lambda task, event_id, response_path, prompt_root: (
            f"RUN {event_id}: {task} @ {prompt_root} -> {response_path}"
        ),
    )
    monkeypatch.setattr(daemon.gitops, "branch_exists", lambda *_args: True)
    monkeypatch.setattr(
        daemon.worktree,
        "create",
        lambda *_args, **_kwargs: worktree_path,
    )
    monkeypatch.setattr(
        daemon.gitops,
        "merge_branch",
        lambda *_args, **_kwargs: merges.append("merge"),
    )
    monkeypatch.setattr(
        daemon.worktree,
        "remove",
        lambda _repo_root, task_id, **kwargs: removals.append((task_id, kwargs)),
    )
    monkeypatch.setattr(
        daemon.runner,
        "run_executor",
        lambda _runner_name, prompt, cwd=None, cfg=None, response_path=None: (
            "---\nbranch: feature/review-fixes\nenv: worktree\n---\nrefined task body\n"
            if prompt.startswith("TRIAGE")
            else Path(response_path).write_text("---\n---\nall done\n", encoding="utf-8") or "ok"
        ),
    )

    task = daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    assert task.status == "done"
    assert merges == []
    assert removals == [(task.id, {"branch": "feature/review-fixes"})]


def _write_repo_scaffold(repo_root: Path) -> None:
    (repo_root / "AGENTS.md").write_text("# Project\n", encoding="utf-8")
    (repo_root / ".brr" / "inbox").mkdir(parents=True)
    (repo_root / ".brr" / "responses").mkdir(parents=True)


def _stop_after_first_push(_repo_root: Path) -> None:
    raise StopIteration

"""Tests for daemon task and event status handling."""

from pathlib import Path

import pytest

from brr import daemon
from brr.task import Task
from brr.runner import RunnerResult


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
        lambda *_args, **_kw: Task(id="task-1", event_id="evt-1", body="help", status="needs_context"),
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
        lambda *_args, **_kw: Task(id="task-2", event_id="evt-2", body="help", status="error"),
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
        lambda body, event_id, _repo_root, **_kwargs: f"TRIAGE {event_id}: {body}",
    )
    monkeypatch.setattr(
        daemon.runner,
        "build_daemon_prompt",
        lambda task, event_id, response_path, _repo_root, **kwargs: (
            f"RUN {event_id}: {kwargs.get('task_id')} {kwargs.get('branch_name')} "
            f"{kwargs.get('runtime_dir')} :: {task} -> {response_path}"
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

    def fake_invoke_runner(runner_name, invocation, cfg=None, *, trace=False):
        calls.append((runner_name, invocation.prompt, invocation.response_path))
        if invocation.prompt.startswith("TRIAGE"):
            return RunnerResult(
                invocation=invocation,
                runner_name=runner_name,
                command=["mock"],
                stdout="---\nbranch: auto\nenv: worktree\n---\nrefined task body\n",
                stderr="",
                returncode=0,
                trace_dir=None,
                artifacts=[],
            )
        Path(invocation.response_path).write_text("---\n---\nall done\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation,
            runner_name=runner_name,
            command=["mock"],
            stdout="ok",
            stderr="",
            returncode=0,
            trace_dir=None,
            artifacts=[
                daemon.runner.RunnerArtifactRecord(
                    path=Path(invocation.response_path),
                    label=f"response:{event['id']}",
                    exists=True,
                    trace_copy=None,
                )
            ],
        )

    monkeypatch.setattr(daemon.runner, "invoke_runner", fake_invoke_runner)

    task = daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    assert task.status == "done"
    assert task.body == "refined task body"
    assert task.branch == "auto"
    assert task.env == "worktree"
    assert calls[0][1] == "TRIAGE evt-3: raw event body"
    assert "refined task body" in calls[1][1]
    assert task.id in calls[1][1]
    assert task.resolve_branch_name() in calls[1][1]
    assert str(tmp_path / ".brr") in calls[1][1]

    persisted = Task.from_file(tmp_path / ".brr" / "tasks" / f"{task.id}.md")
    assert persisted is not None
    assert persisted.branch == "auto"
    assert persisted.env == "worktree"
    assert persisted.status == "done"
    assert "response_path" in persisted.meta
    assert "branch_name" in persisted.meta
    assert persisted.meta["branch_name"] == task.resolve_branch_name()


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
        lambda body, event_id, _repo_root, **_kwargs: f"TRIAGE {event_id}: {body}",
    )
    monkeypatch.setattr(
        daemon.runner,
        "build_daemon_prompt",
        lambda task, event_id, response_path, prompt_root, **kwargs: (
            f"RUN {event_id}: {kwargs.get('task_id')} {kwargs.get('branch_name')} "
            f"{kwargs.get('runtime_dir')} :: {task} @ {prompt_root} -> {response_path}"
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

    def fake_invoke_runner(runner_name, invocation, cfg=None, *, trace=False):
        calls.append((runner_name, invocation.prompt, invocation.cwd, invocation.response_path))
        if invocation.prompt.startswith("TRIAGE"):
            return RunnerResult(
                invocation=invocation,
                runner_name=runner_name,
                command=["mock"],
                stdout="---\nid: ignored\nbranch: auto\nenv: worktree\n---\nrefined task body\n",
                stderr="",
                returncode=0,
                trace_dir=None,
                artifacts=[],
            )
        Path(invocation.response_path).write_text("---\n---\nall done\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation,
            runner_name=runner_name,
            command=["mock"],
            stdout="ok",
            stderr="",
            returncode=0,
            trace_dir=None,
            artifacts=[
                daemon.runner.RunnerArtifactRecord(
                    path=Path(invocation.response_path),
                    label=f"response:{event['id']}",
                    exists=True,
                    trace_copy=None,
                )
            ],
        )

    monkeypatch.setattr(daemon.runner, "invoke_runner", fake_invoke_runner)

    task = daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    assert task.status == "done"
    assert calls[1][2] == worktree_path
    assert f"@ {worktree_path} ->" in calls[1][1]
    assert task.id in calls[1][1]
    assert task.resolve_branch_name() in calls[1][1]
    assert str(tmp_path / ".brr") in calls[1][1]
    assert len(merges) == 1
    assert merges[0][0] == task.resolve_branch_name()
    assert merges[0][1] == f"merge {task.resolve_branch_name()} for {task.id}"
    assert removals == [(task.id, {"branch": task.resolve_branch_name(), "delete_branch": True, "force": True})]


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
        lambda body, event_id, _repo_root, **_kwargs: f"TRIAGE {event_id}: {body}",
    )
    monkeypatch.setattr(
        daemon.runner,
        "invoke_runner",
        lambda runner_name, invocation, cfg=None, *, trace=False: RunnerResult(
            invocation=invocation,
            runner_name=runner_name,
            command=["mock"],
            stdout="not a task file",
            stderr="",
            returncode=0,
            trace_dir=None,
            artifacts=[],
        ),
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
        lambda body, event_id, _repo_root, **_kwargs: f"TRIAGE {event_id}: {body}",
    )
    monkeypatch.setattr(
        daemon.runner,
        "build_daemon_prompt",
        lambda task, event_id, response_path, prompt_root, **kwargs: (
            f"RUN {event_id}: {kwargs.get('task_id')} {kwargs.get('branch_name')} "
            f"{kwargs.get('runtime_dir')} :: {task} @ {prompt_root} -> {response_path}"
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
    def _named_branch_invoke(_runner_name, invocation, cfg=None, *, trace=False):
        if invocation.prompt.startswith("TRIAGE"):
            return RunnerResult(
                invocation=invocation,
                runner_name=_runner_name,
                command=["mock"],
                stdout="---\nbranch: feature/review-fixes\nenv: worktree\n---\nrefined task body\n",
                stderr="",
                returncode=0,
                trace_dir=None,
                artifacts=[],
            )
        Path(invocation.response_path).write_text("---\n---\nall done\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation,
            runner_name=_runner_name,
            command=["mock"],
            stdout="ok",
            stderr="",
            returncode=0,
            trace_dir=None,
            artifacts=[
                daemon.runner.RunnerArtifactRecord(
                    path=Path(invocation.response_path),
                    label=f"response:{event['id']}",
                    exists=True,
                    trace_copy=None,
                )
            ],
        )

    monkeypatch.setattr(daemon.runner, "invoke_runner", _named_branch_invoke)

    task = daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    assert task.status == "done"
    assert merges == []
    assert removals == [(task.id, {"branch": "feature/review-fixes", "force": True})]


def test_run_worker_retries_from_missing_required_output(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    event = {
        "id": "evt-7",
        "status": "pending",
        "body": "raw event body",
        "source": "telegram",
        "_path": tmp_path / ".brr" / "inbox" / "evt-7.md",
    }
    event["_path"].write_text(
        "---\nid: evt-7\nstatus: pending\nsource: telegram\n---\nraw event body\n",
        encoding="utf-8",
    )

    attempts = []

    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _repo_root: "codex")
    monkeypatch.setattr(
        daemon.runner,
        "build_triage_prompt",
        lambda body, event_id, _repo_root, **_kwargs: f"TRIAGE {event_id}: {body}",
    )
    monkeypatch.setattr(
        daemon.runner,
        "build_daemon_prompt",
        lambda task, event_id, response_path, _repo_root, **kwargs: (
            f"RUN {event_id}: {kwargs.get('task_id')} {kwargs.get('branch_name')} "
            f"{kwargs.get('runtime_dir')} :: {task} -> {response_path}"
        ),
    )

    def fake_invoke_runner(runner_name, invocation, cfg=None, *, trace=False):
        attempts.append(invocation.label)
        if invocation.prompt.startswith("TRIAGE"):
            return RunnerResult(
                invocation=invocation,
                runner_name=runner_name,
                command=["mock"],
                stdout="---\nbranch: current\nenv: local\n---\nrefined task body\n",
                stderr="",
                returncode=0,
                trace_dir=None,
                artifacts=[],
            )
        response_path = Path(invocation.response_path)
        if invocation.label.endswith("attempt-1"):
            return RunnerResult(
                invocation=invocation,
                runner_name=runner_name,
                command=["mock"],
                stdout="first try",
                stderr="",
                returncode=0,
                trace_dir=None,
                artifacts=[
                    daemon.runner.RunnerArtifactRecord(
                        path=response_path,
                        label="response:evt-7",
                        exists=False,
                        trace_copy=None,
                    )
                ],
            )
        response_path.write_text("---\n---\nall done\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation,
            runner_name=runner_name,
            command=["mock"],
            stdout="second try",
            stderr="",
            returncode=0,
            trace_dir=None,
            artifacts=[
                daemon.runner.RunnerArtifactRecord(
                    path=response_path,
                    label="response:evt-7",
                    exists=True,
                    trace_copy=None,
                )
            ],
        )

    monkeypatch.setattr(daemon.runner, "invoke_runner", fake_invoke_runner)

    task = daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 1)

    assert task.status == "done"
    assert attempts == ["evt-7", "evt-7-attempt-1", "evt-7-attempt-2"]


def test_debug_mode_keeps_worktree_after_merge(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    event = {
        "id": "evt-8",
        "status": "pending",
        "body": "raw event body",
        "source": "telegram",
        "_path": tmp_path / ".brr" / "inbox" / "evt-8.md",
    }
    event["_path"].write_text(
        "---\nid: evt-8\nstatus: pending\nsource: telegram\n---\nraw event body\n",
        encoding="utf-8",
    )

    worktree_path = tmp_path / ".brr" / "worktrees" / "task-1"
    removals = []
    trace_flags = []

    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _repo_root: "codex")
    monkeypatch.setattr(
        daemon.runner,
        "build_triage_prompt",
        lambda body, event_id, _repo_root, **_kwargs: f"TRIAGE {event_id}: {body}",
    )
    monkeypatch.setattr(
        daemon.runner,
        "build_daemon_prompt",
        lambda task, event_id, response_path, prompt_root, **kwargs: (
            f"RUN {event_id}: {kwargs.get('task_id')} {kwargs.get('branch_name')} "
            f"{kwargs.get('runtime_dir')} :: {task} @ {prompt_root} -> {response_path}"
        ),
    )
    monkeypatch.setattr(daemon.gitops, "branch_exists", lambda *_args: False)
    monkeypatch.setattr(
        daemon.worktree, "create", lambda *_args, **_kwargs: worktree_path,
    )
    monkeypatch.setattr(
        daemon.gitops,
        "merge_branch",
        lambda _repo_root, branch, message=None:
        daemon.gitops.MergeResult(success=True, branch=branch, commit="abc123"),
    )
    monkeypatch.setattr(
        daemon.worktree,
        "remove",
        lambda _repo_root, task_id, **kwargs: removals.append((task_id, kwargs)),
    )

    def fake_invoke_runner(runner_name, invocation, cfg=None, *, trace=False):
        trace_flags.append(trace)
        if invocation.prompt.startswith("TRIAGE"):
            return RunnerResult(
                invocation=invocation,
                runner_name=runner_name,
                command=["mock"],
                stdout="---\nbranch: task\nenv: worktree\n---\nrefined body\n",
                stderr="",
                returncode=0,
                trace_dir=None,
                artifacts=[],
            )
        Path(invocation.response_path).write_text("---\n---\nall done\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation,
            runner_name=runner_name,
            command=["mock"],
            stdout="ok",
            stderr="",
            returncode=0,
            trace_dir=None,
            artifacts=[
                daemon.runner.RunnerArtifactRecord(
                    path=Path(invocation.response_path),
                    label=f"response:{event['id']}",
                    exists=True,
                    trace_copy=None,
                )
            ],
        )

    monkeypatch.setattr(daemon.runner, "invoke_runner", fake_invoke_runner)

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0, debug=True,
    )

    assert task.status == "done"
    assert removals == [], "worktree should NOT be removed in debug mode"
    assert all(trace_flags), "trace should be True for all invocations in debug mode"


def test_debug_mode_from_config(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    event = {"id": "evt-9", "status": "pending", "_path": tmp_path / ".brr" / "inbox" / "evt-9.md"}
    event["_path"].write_text("---\nid: evt-9\nstatus: pending\n---\nhelp\n", encoding="utf-8")
    statuses = []
    worker_debug_flags = []

    monkeypatch.setattr(daemon, "read_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_write_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_clear_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_start_gates", lambda *_args: [])
    monkeypatch.setattr(daemon.conf, "load_config", lambda _repo_root: {"debug": True})
    monkeypatch.setattr(
        daemon.protocol,
        "list_pending",
        lambda _inbox_dir: [event] if not statuses else [],
    )
    monkeypatch.setattr(daemon.protocol, "set_status", lambda _event, status: statuses.append(status))

    original_run_worker = daemon._run_worker

    def capturing_run_worker(*args, **kwargs):
        worker_debug_flags.append(kwargs.get("debug", False))
        return Task(id="task-9", event_id="evt-9", body="help", status="done")

    monkeypatch.setattr(daemon, "_run_worker", capturing_run_worker)
    monkeypatch.setattr(daemon, "_push_if_needed", _stop_after_first_push)
    monkeypatch.setattr(daemon.signal, "signal", lambda *_args: None)

    with pytest.raises(StopIteration):
        daemon.start(tmp_path)

    assert worker_debug_flags == [True], "debug=True from config should propagate to worker"


def test_no_debug_removes_worktree(tmp_path, monkeypatch):
    """Verify that without debug, worktrees are removed as before."""
    _write_repo_scaffold(tmp_path)
    event = {
        "id": "evt-10",
        "status": "pending",
        "body": "raw event body",
        "source": "telegram",
        "_path": tmp_path / ".brr" / "inbox" / "evt-10.md",
    }
    event["_path"].write_text(
        "---\nid: evt-10\nstatus: pending\nsource: telegram\n---\nraw event body\n",
        encoding="utf-8",
    )

    worktree_path = tmp_path / ".brr" / "worktrees" / "task-1"
    removals = []

    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _repo_root: "codex")
    monkeypatch.setattr(
        daemon.runner, "build_triage_prompt",
        lambda body, event_id, _repo_root, **_kwargs: f"TRIAGE {event_id}: {body}",
    )
    monkeypatch.setattr(
        daemon.runner, "build_daemon_prompt",
        lambda task, event_id, response_path, prompt_root, **kwargs: (
            f"RUN {event_id}: {kwargs.get('task_id')} {kwargs.get('branch_name')} "
            f"{kwargs.get('runtime_dir')} :: {task} @ {prompt_root} -> {response_path}"
        ),
    )
    monkeypatch.setattr(daemon.gitops, "branch_exists", lambda *_args: False)
    monkeypatch.setattr(daemon.worktree, "create", lambda *_args, **_kwargs: worktree_path)
    monkeypatch.setattr(
        daemon.gitops, "merge_branch",
        lambda _repo_root, branch, message=None:
        daemon.gitops.MergeResult(success=True, branch=branch, commit="abc123"),
    )
    monkeypatch.setattr(
        daemon.worktree, "remove",
        lambda _repo_root, task_id, **kwargs: removals.append((task_id, kwargs)),
    )

    def fake_invoke_runner(runner_name, invocation, cfg=None, *, trace=False):
        if invocation.prompt.startswith("TRIAGE"):
            return RunnerResult(
                invocation=invocation, runner_name=runner_name, command=["mock"],
                stdout="---\nbranch: task\nenv: worktree\n---\nbody\n",
                stderr="", returncode=0, trace_dir=None, artifacts=[],
            )
        Path(invocation.response_path).write_text("---\n---\ndone\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="ok", stderr="", returncode=0, trace_dir=None,
            artifacts=[
                daemon.runner.RunnerArtifactRecord(
                    path=Path(invocation.response_path),
                    label=f"response:{event['id']}", exists=True, trace_copy=None,
                )
            ],
        )

    monkeypatch.setattr(daemon.runner, "invoke_runner", fake_invoke_runner)

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0, debug=False,
    )

    assert task.status == "done"
    assert len(removals) == 1, "worktree should be removed when not in debug mode"


def test_kb_maintenance_runs_when_kb_changed(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    event = {
        "id": "evt-kb",
        "status": "pending",
        "body": "update docs",
        "source": "telegram",
        "_path": tmp_path / ".brr" / "inbox" / "evt-kb.md",
    }
    event["_path"].write_text(
        "---\nid: evt-kb\nstatus: pending\nsource: telegram\n---\nupdate docs\n",
        encoding="utf-8",
    )

    maintenance_calls = []

    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _: "codex")
    monkeypatch.setattr(
        daemon.runner, "build_triage_prompt",
        lambda body, eid, _, **_kwargs: f"TRIAGE {eid}: {body}",
    )
    monkeypatch.setattr(
        daemon.runner, "build_daemon_prompt",
        lambda task, eid, rp, rr, **kw: f"RUN {eid}: {task} -> {rp}",
    )
    monkeypatch.setattr(
        daemon.runner, "build_kb_maintenance_prompt",
        lambda _: "KB MAINTENANCE",
    )
    monkeypatch.setattr(daemon, "_kb_changed", lambda _: True)

    def fake_invoke(runner_name, invocation, cfg=None, *, trace=False):
        if invocation.prompt.startswith("TRIAGE"):
            return RunnerResult(
                invocation=invocation, runner_name=runner_name,
                command=["mock"],
                stdout="---\nbranch: current\nenv: local\n---\nupdate docs\n",
                stderr="", returncode=0, trace_dir=None, artifacts=[],
            )
        if invocation.kind == "kb-maintenance":
            maintenance_calls.append(invocation.prompt)
            trace_dir = tmp_path / ".brr" / "traces" / "kb-maintenance" / "kb-maintenance-test"
            return RunnerResult(
                invocation=invocation, runner_name=runner_name,
                command=["mock"], stdout="ok", stderr="",
                returncode=0, trace_dir=trace_dir, artifacts=[],
            )
        Path(invocation.response_path).write_text("---\n---\ndone\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation, runner_name=runner_name,
            command=["mock"], stdout="ok", stderr="", returncode=0,
            trace_dir=None,
            artifacts=[
                daemon.runner.RunnerArtifactRecord(
                    path=Path(invocation.response_path),
                    label=f"response:{event['id']}", exists=True, trace_copy=None,
                )
            ],
        )

    monkeypatch.setattr(daemon.runner, "invoke_runner", fake_invoke)

    task = daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    assert task.status == "done"
    assert len(maintenance_calls) == 1
    assert maintenance_calls[0] == "KB MAINTENANCE"
    assert "traces/kb-maintenance/kb-maintenance-test" in task.meta["trace_dirs"]

    persisted = Task.from_file(tmp_path / ".brr" / "tasks" / f"{task.id}.md")
    assert persisted is not None
    assert "traces/kb-maintenance/kb-maintenance-test" in persisted.meta["trace_dirs"]


def test_kb_maintenance_skipped_when_no_changes(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    event = {
        "id": "evt-nochange",
        "status": "pending",
        "body": "quick fix",
        "source": "telegram",
        "_path": tmp_path / ".brr" / "inbox" / "evt-nochange.md",
    }
    event["_path"].write_text(
        "---\nid: evt-nochange\nstatus: pending\nsource: telegram\n---\nquick fix\n",
        encoding="utf-8",
    )

    maintenance_calls = []

    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _: "codex")
    monkeypatch.setattr(
        daemon.runner, "build_triage_prompt",
        lambda body, eid, _, **_kwargs: f"TRIAGE {eid}: {body}",
    )
    monkeypatch.setattr(
        daemon.runner, "build_daemon_prompt",
        lambda task, eid, rp, rr, **kw: f"RUN {eid}: {task} -> {rp}",
    )
    monkeypatch.setattr(daemon, "_kb_changed", lambda _: False)

    def fake_invoke(runner_name, invocation, cfg=None, *, trace=False):
        if invocation.prompt.startswith("TRIAGE"):
            return RunnerResult(
                invocation=invocation, runner_name=runner_name,
                command=["mock"],
                stdout="---\nbranch: current\nenv: local\n---\nquick fix\n",
                stderr="", returncode=0, trace_dir=None, artifacts=[],
            )
        if invocation.kind == "kb-maintenance":
            maintenance_calls.append(True)
            return RunnerResult(
                invocation=invocation, runner_name=runner_name,
                command=["mock"], stdout="", stderr="",
                returncode=0, trace_dir=None, artifacts=[],
            )
        Path(invocation.response_path).write_text("---\n---\ndone\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation, runner_name=runner_name,
            command=["mock"], stdout="ok", stderr="", returncode=0,
            trace_dir=None,
            artifacts=[
                daemon.runner.RunnerArtifactRecord(
                    path=Path(invocation.response_path),
                    label=f"response:{event['id']}", exists=True, trace_copy=None,
                )
            ],
        )

    monkeypatch.setattr(daemon.runner, "invoke_runner", fake_invoke)

    task = daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    assert task.status == "done"
    assert maintenance_calls == [], "maintenance should not run when kb/ unchanged"


def test_kb_maintenance_never_config(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    event = {
        "id": "evt-never",
        "status": "pending",
        "body": "docs",
        "source": "telegram",
        "_path": tmp_path / ".brr" / "inbox" / "evt-never.md",
    }
    event["_path"].write_text(
        "---\nid: evt-never\nstatus: pending\nsource: telegram\n---\ndocs\n",
        encoding="utf-8",
    )

    maintenance_calls = []

    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _: "codex")
    monkeypatch.setattr(
        daemon.runner, "build_triage_prompt",
        lambda body, eid, _, **_kwargs: f"TRIAGE {eid}: {body}",
    )
    monkeypatch.setattr(
        daemon.runner, "build_daemon_prompt",
        lambda task, eid, rp, rr, **kw: f"RUN {eid}: {task} -> {rp}",
    )
    monkeypatch.setattr(daemon, "_kb_changed", lambda _: True)

    def fake_invoke(runner_name, invocation, cfg=None, *, trace=False):
        if invocation.prompt.startswith("TRIAGE"):
            return RunnerResult(
                invocation=invocation, runner_name=runner_name,
                command=["mock"],
                stdout="---\nbranch: current\nenv: local\n---\ndocs\n",
                stderr="", returncode=0, trace_dir=None, artifacts=[],
            )
        if invocation.kind == "kb-maintenance":
            maintenance_calls.append(True)
            return RunnerResult(
                invocation=invocation, runner_name=runner_name,
                command=["mock"], stdout="", stderr="",
                returncode=0, trace_dir=None, artifacts=[],
            )
        Path(invocation.response_path).write_text("---\n---\ndone\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation, runner_name=runner_name,
            command=["mock"], stdout="ok", stderr="", returncode=0,
            trace_dir=None,
            artifacts=[
                daemon.runner.RunnerArtifactRecord(
                    path=Path(invocation.response_path),
                    label=f"response:{event['id']}", exists=True, trace_copy=None,
                )
            ],
        )

    monkeypatch.setattr(daemon.runner, "invoke_runner", fake_invoke)

    cfg = {"kb_maintenance": "never"}
    task = daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", cfg, 0)

    assert task.status == "done"
    assert maintenance_calls == [], "maintenance should not run when config=never"


def _write_repo_scaffold(repo_root: Path) -> None:
    (repo_root / "AGENTS.md").write_text("# Project\n", encoding="utf-8")
    (repo_root / ".brr" / "inbox").mkdir(parents=True)
    (repo_root / ".brr" / "responses").mkdir(parents=True)


def _stop_after_first_push(_repo_root: Path) -> None:
    raise StopIteration

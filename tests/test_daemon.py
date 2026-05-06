"""Tests for the daemon worker after the triage stage was removed."""

from pathlib import Path

import pytest

from brr import daemon, envs
from brr.task import Task
from brr.runner import RunnerResult


def _write_repo_scaffold(repo_root: Path) -> None:
    (repo_root / "AGENTS.md").write_text("# Project\n", encoding="utf-8")
    (repo_root / ".brr" / "inbox").mkdir(parents=True)
    (repo_root / ".brr" / "responses").mkdir(parents=True)


def _stop_after_first_push(_repo_root: Path, **_kwargs) -> None:
    raise StopIteration


def _make_event(
    tmp_path: Path,
    eid: str,
    body: str = "raw event body",
    source: str = "telegram",
) -> dict:
    path = tmp_path / ".brr" / "inbox" / f"{eid}.md"
    path.write_text(
        f"---\nid: {eid}\nstatus: pending\nsource: {source}\n---\n{body}\n",
        encoding="utf-8",
    )
    return {
        "id": eid,
        "status": "pending",
        "body": body,
        "source": source,
        "_path": path,
    }


def _stub_env_isolated(monkeypatch, tmp_path):
    """Replace env backends with stand-ins that don't touch git/docker."""
    worktree_path = tmp_path / ".brr" / "worktrees" / "stub"
    worktree_path.mkdir(parents=True, exist_ok=True)
    finalized: list[str] = []

    class StubEnv:
        name = "worktree"

        def prepare(self, task, repo_root, cfg, *, base_branch, response_path, debug=False):
            return envs.RunContext(
                name=self.name,
                cwd=worktree_path,
                repo_root=repo_root,
                runtime_dir=tmp_path / ".brr",
                response_path_host=response_path,
                response_path_env=response_path,
                branch_name=f"brr/{task.id}",
                base_branch=base_branch,
                log_file=f"kb/log-{task.id}.md",
                env_state={"worktree_path": str(worktree_path)},
            )

        def invoke(self, ctx, runner_name, invocation, cfg=None, *, trace=False):
            raise NotImplementedError("override in test")

        def finalize(self, ctx, task, tasks_dir, *, debug=False):
            finalized.append(task.id)
            return task

    monkeypatch.setattr(envs, "get_env", lambda _name: StubEnv())
    return worktree_path, finalized


def test_run_worker_constructs_task_without_triage(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    event = _make_event(tmp_path, "evt-1")
    worktree_path, _finalized = _stub_env_isolated(monkeypatch, tmp_path)

    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _root: "codex")
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.runner,
        "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: f"PROMPT {eid} {kw.get('task_id')} -> {rp}",
    )

    invocations: list[str] = []

    base_env = envs.get_env("worktree")

    def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
        invocations.append(invocation.kind)
        Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
        Path(invocation.response_path).write_text("plain answer\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation,
            runner_name=runner_name,
            command=["mock"],
            stdout="plain answer\n",
            stderr="",
            returncode=0,
            trace_dir=None,
            artifacts=[],
        )

    monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

    task = daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    assert task.status == "done"
    assert task.body == "raw event body"
    assert task.env == "worktree"
    # Triage no longer runs as a separate stage.
    assert "triage" not in invocations
    assert invocations == ["daemon-run"]
    persisted = Task.from_file(tmp_path / ".brr" / "tasks" / f"{task.id}.md")
    assert persisted is not None
    assert persisted.status == "done"
    response = (tmp_path / ".brr" / "responses" / "evt-1.md").read_text(encoding="utf-8")
    assert response == "plain answer\n"


def test_run_worker_marks_error_on_env_setup_failure(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    event = _make_event(tmp_path, "evt-2")

    class ExplodingEnv:
        name = "worktree"

        def prepare(self, *_args, **_kwargs):
            raise RuntimeError("boom")

        def invoke(self, *_args, **_kwargs):  # pragma: no cover - never reached
            raise AssertionError("invoke should not run")

        def finalize(self, *_args, **_kwargs):  # pragma: no cover - never reached
            return None

    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _root: "codex")
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(daemon.envs, "get_env", lambda _name: ExplodingEnv())

    task = daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    assert task.status == "error"
    persisted = Task.from_file(tmp_path / ".brr" / "tasks" / f"{task.id}.md")
    assert persisted is not None
    assert persisted.status == "error"


def test_run_worker_retries_on_empty_stdout(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    event = _make_event(tmp_path, "evt-3")
    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _root: "codex")
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.runner,
        "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: f"P {eid}",
    )
    attempts: list[str] = []

    class RetryEnv:
        name = "worktree"

        def prepare(self, task, repo_root, cfg, *, base_branch, response_path, debug=False):
            return envs.RunContext(
                name=self.name, cwd=tmp_path, repo_root=repo_root,
                runtime_dir=tmp_path / ".brr",
                response_path_host=response_path,
                response_path_env=response_path,
                branch_name=f"brr/{task.id}",
                base_branch=base_branch,
                log_file=f"kb/log-{task.id}.md",
                env_state={"worktree_path": str(tmp_path)},
            )

        def invoke(self, ctx, runner_name, invocation, cfg, *, trace=False):
            attempts.append(invocation.label)
            stdout = "" if invocation.label.endswith("attempt-1") else "fixed reply\n"
            if stdout:
                Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
                Path(invocation.response_path).write_text(stdout, encoding="utf-8")
            return RunnerResult(
                invocation=invocation,
                runner_name=runner_name,
                command=["mock"],
                stdout=stdout,
                stderr="",
                returncode=0,
                trace_dir=None,
                artifacts=[],
            )

        def finalize(self, _ctx, task, _tasks_dir, *, debug=False):
            return task

    monkeypatch.setattr(daemon.envs, "get_env", lambda _name: RetryEnv())

    task = daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 1)

    assert task.status == "done"
    assert attempts == ["evt-3-attempt-1", "evt-3-attempt-2"]


def test_start_preserves_error_event_status(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    event = {"id": "evt-err", "status": "pending", "_path": tmp_path / ".brr" / "inbox" / "evt-err.md"}
    event["_path"].write_text(
        "---\nid: evt-err\nstatus: pending\n---\nhelp\n", encoding="utf-8",
    )
    statuses: list[str] = []

    monkeypatch.setattr(daemon, "read_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_write_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_clear_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_start_gates", lambda *_args: [])
    monkeypatch.setattr(daemon.conf, "load_config", lambda _root: {})
    monkeypatch.setattr(
        daemon.protocol,
        "list_pending",
        lambda _inbox: [event] if not statuses else [],
    )
    monkeypatch.setattr(daemon.protocol, "set_status", lambda _ev, status: statuses.append(status))
    monkeypatch.setattr(
        daemon,
        "_run_worker",
        lambda *_a, **_k: Task(id="task-err", event_id="evt-err", body="help", status="error"),
    )
    monkeypatch.setattr(daemon, "_push_if_needed", _stop_after_first_push)
    monkeypatch.setattr(daemon.signal, "signal", lambda *_args: None)

    with pytest.raises(StopIteration):
        daemon.start(tmp_path)

    assert statuses == ["processing", "error"]


def test_debug_mode_from_config(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    event = {"id": "evt-dbg", "status": "pending", "_path": tmp_path / ".brr" / "inbox" / "evt-dbg.md"}
    event["_path"].write_text(
        "---\nid: evt-dbg\nstatus: pending\n---\nhelp\n", encoding="utf-8",
    )
    statuses: list[str] = []
    seen_debug: list[bool] = []

    monkeypatch.setattr(daemon, "read_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_write_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_clear_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_start_gates", lambda *_args: [])
    monkeypatch.setattr(daemon.conf, "load_config", lambda _root: {"debug": True})
    monkeypatch.setattr(
        daemon.protocol,
        "list_pending",
        lambda _inbox: [event] if not statuses else [],
    )
    monkeypatch.setattr(daemon.protocol, "set_status", lambda _ev, status: statuses.append(status))

    def capturing_run_worker(*_args, **kwargs):
        seen_debug.append(kwargs.get("debug", False))
        return Task(id="task-dbg", event_id="evt-dbg", body="help", status="done")

    monkeypatch.setattr(daemon, "_run_worker", capturing_run_worker)
    monkeypatch.setattr(daemon, "_push_if_needed", _stop_after_first_push)
    monkeypatch.setattr(daemon.signal, "signal", lambda *_args: None)

    with pytest.raises(StopIteration):
        daemon.start(tmp_path)

    assert seen_debug == [True], "debug=True from config should propagate to worker"


def test_kb_maintenance_runs_when_kb_changed(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    event = _make_event(tmp_path, "evt-kb", body="update docs")
    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _root: "codex")
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.runner,
        "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: f"P {eid}",
    )
    monkeypatch.setattr(daemon, "_kb_changed", lambda _root: True)
    monkeypatch.setattr(
        daemon.runner,
        "build_kb_maintenance_prompt",
        lambda _root: "KB MAINTENANCE",
    )
    maintenance: list[str] = []

    class StubEnv:
        name = "worktree"

        def prepare(self, task, repo_root, cfg, *, base_branch, response_path, debug=False):
            return envs.RunContext(
                name=self.name, cwd=tmp_path, repo_root=repo_root,
                runtime_dir=tmp_path / ".brr",
                response_path_host=response_path,
                response_path_env=response_path,
                branch_name=f"brr/{task.id}",
                base_branch=base_branch,
                log_file=f"kb/log-{task.id}.md",
                env_state={"worktree_path": str(tmp_path)},
            )

        def invoke(self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
            Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
            Path(invocation.response_path).write_text("ok\n", encoding="utf-8")
            return RunnerResult(
                invocation=invocation, runner_name=runner_name, command=["mock"],
                stdout="ok\n", stderr="", returncode=0, trace_dir=None, artifacts=[],
            )

        def finalize(self, _ctx, task, _tasks_dir, *, debug=False):
            return task

    monkeypatch.setattr(daemon.envs, "get_env", lambda _name: StubEnv())

    def fake_invoke_runner(runner_name, invocation, cfg=None, *, trace=False):
        if invocation.kind == "kb-maintenance":
            maintenance.append(invocation.prompt)
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="ok", stderr="", returncode=0, trace_dir=None, artifacts=[],
        )

    monkeypatch.setattr(daemon.runner, "invoke_runner", fake_invoke_runner)

    task = daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    assert task.status == "done"
    assert maintenance == ["KB MAINTENANCE"]


def test_kb_maintenance_skipped_when_no_changes(tmp_path, monkeypatch):
    _write_repo_scaffold(tmp_path)
    event = _make_event(tmp_path, "evt-skip", body="quick fix")
    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _root: "codex")
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.runner,
        "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: f"P {eid}",
    )
    monkeypatch.setattr(daemon, "_kb_changed", lambda _root: False)
    maintenance: list[str] = []

    class StubEnv:
        name = "worktree"

        def prepare(self, task, repo_root, cfg, *, base_branch, response_path, debug=False):
            return envs.RunContext(
                name=self.name, cwd=tmp_path, repo_root=repo_root,
                runtime_dir=tmp_path / ".brr",
                response_path_host=response_path,
                response_path_env=response_path,
                branch_name=f"brr/{task.id}",
                base_branch=base_branch,
                log_file=f"kb/log-{task.id}.md",
                env_state={"worktree_path": str(tmp_path)},
            )

        def invoke(self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
            Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
            Path(invocation.response_path).write_text("ok\n", encoding="utf-8")
            return RunnerResult(
                invocation=invocation, runner_name=runner_name, command=["mock"],
                stdout="ok\n", stderr="", returncode=0, trace_dir=None, artifacts=[],
            )

        def finalize(self, _ctx, task, _tasks_dir, *, debug=False):
            return task

    monkeypatch.setattr(daemon.envs, "get_env", lambda _name: StubEnv())

    def fake_invoke_runner(runner_name, invocation, cfg=None, *, trace=False):
        if invocation.kind == "kb-maintenance":
            maintenance.append(invocation.prompt)
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="ok", stderr="", returncode=0, trace_dir=None, artifacts=[],
        )

    monkeypatch.setattr(daemon.runner, "invoke_runner", fake_invoke_runner)

    task = daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    assert task.status == "done"
    assert maintenance == []

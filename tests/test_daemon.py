"""Tests for the daemon worker after the triage stage was removed."""

import subprocess
from pathlib import Path

import pytest

from brr import daemon, envs
from brr.task import Task
from brr.runner import RunnerResult

from _helpers import (
    StubWorktreeEnv,
    commit_files,
    init_git_repo,
    make_event,
    succeed_invoke,
    write_repo_scaffold,
)


def _stop_after_first_push(_repo_root: Path, **_kwargs) -> None:
    raise StopIteration


def _stub_env_isolated(monkeypatch, tmp_path):
    """Replace env backends with stand-ins that don't touch git/docker."""
    worktree_path = tmp_path / ".brr" / "worktrees" / "stub"
    worktree_path.mkdir(parents=True, exist_ok=True)
    finalized: list[str] = []

    class StubEnv:
        name = "worktree"

        def prepare(self, task, repo_root, cfg, *, branch_plan, response_path):
            return envs.RunContext(
                name=self.name,
                cwd=worktree_path,
                repo_root=repo_root,
                runtime_dir=tmp_path / ".brr",
                response_path_host=response_path,
                response_path_env=response_path,
                branch_name=f"brr/{task.id}",
                env_state={"worktree_path": str(worktree_path)},
            )

        def invoke(self, ctx, runner_name, invocation, cfg=None, *, trace=False):
            raise NotImplementedError("override in test")

        def finalize(self, ctx, task, tasks_dir):
            finalized.append(task.id)
            return task

    monkeypatch.setattr(envs, "get_env", lambda _name: StubEnv())
    return worktree_path, finalized


def test_run_worker_constructs_task_without_triage(tmp_path, monkeypatch):
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-1")
    worktree_path, _finalized = _stub_env_isolated(monkeypatch, tmp_path)

    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _root: "codex")
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts,
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
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-2")

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
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-3")
    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _root: "codex")
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts,
        "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: f"P {eid}",
    )
    attempts: list[str] = []

    class RetryEnv:
        name = "worktree"

        def prepare(self, task, repo_root, cfg, *, branch_plan, response_path):
            return envs.RunContext(
                name=self.name, cwd=tmp_path, repo_root=repo_root,
                runtime_dir=tmp_path / ".brr",
                response_path_host=response_path,
                response_path_env=response_path,
                branch_name=f"brr/{task.id}",
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

        def finalize(self, _ctx, task, _tasks_dir):
            return task

    monkeypatch.setattr(daemon.envs, "get_env", lambda _name: RetryEnv())

    task = daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 1)

    assert task.status == "done"
    assert attempts == ["evt-3-attempt-1", "evt-3-attempt-2"]


def test_run_worker_calls_sync_before_resolving_branch_plan(
    tmp_path, monkeypatch,
):
    """Pre-task fetch+ff fires before the daemon picks a seed ref."""
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-sync-order")
    _stub_env_isolated(monkeypatch, tmp_path)

    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _root: "codex")
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts,
        "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: "PROMPT",
    )

    call_order: list[str] = []
    captured_targets: list[list[str]] = []

    def fake_refresh(_repo, *, target_branches, cfg=None):
        call_order.append("sync")
        captured_targets.append(list(target_branches))
        return daemon.sync.SyncResult(fetched=True)

    real_resolve = daemon.branching.resolve_branch_plan

    def wrapped_resolve(repo_root, ev, cfg):
        call_order.append("resolve")
        return real_resolve(repo_root, ev, cfg)

    monkeypatch.setattr(daemon.sync, "refresh_before_task", fake_refresh)
    monkeypatch.setattr(daemon.branching, "resolve_branch_plan", wrapped_resolve)

    base_env = envs.get_env("worktree")

    def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
        Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
        Path(invocation.response_path).write_text("ok\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation,
            runner_name=runner_name,
            command=["mock"],
            stdout="ok\n",
            stderr="",
            returncode=0,
            trace_dir=None,
            artifacts=[],
        )

    monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

    daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    assert call_order[:2] == ["sync", "resolve"]
    # When the event carries no structured branch field, we still
    # ask sync to consider the host's default branch (or whatever
    # gitops returns there) — empty is acceptable for a repo without
    # a default branch but the call must happen.
    assert captured_targets, "sync.refresh_before_task was not called"


def test_run_worker_proceeds_when_sync_fails(tmp_path, monkeypatch):
    """A sync error never blocks task execution."""
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-sync-fail")
    _stub_env_isolated(monkeypatch, tmp_path)

    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _root: "codex")
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts,
        "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: "PROMPT",
    )
    monkeypatch.setattr(
        daemon.sync, "refresh_before_task",
        lambda _repo, *, target_branches, cfg=None: daemon.sync.SyncResult(
            error="git fetch origin: simulated network failure",
        ),
    )

    base_env = envs.get_env("worktree")

    def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
        Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
        Path(invocation.response_path).write_text("ok\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation,
            runner_name=runner_name,
            command=["mock"],
            stdout="ok\n",
            stderr="",
            returncode=0,
            trace_dir=None,
            artifacts=[],
        )

    monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

    task = daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    assert task.status == "done"


def test_branches_to_refresh_includes_default_and_structured(monkeypatch, tmp_path):
    """The helper merges the local default branch with structured event keys."""
    write_repo_scaffold(tmp_path)
    monkeypatch.setattr(daemon.gitops, "default_branch", lambda _root: "main")
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(daemon.gitops, "valid_branch_name", lambda _root, _b: True)

    targets = daemon._branches_to_refresh(
        tmp_path,
        {
            "branch_target": "feature-x",
            "target_branch": "release",
            "branch": "auto",
        },
    )

    assert targets[0] == "main"
    assert "feature-x" in targets
    assert "release" in targets
    # ``branch=auto`` is a no-op sentinel and must not appear.
    assert "auto" not in targets


def test_start_preserves_error_event_status(tmp_path, monkeypatch):
    write_repo_scaffold(tmp_path)
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


def _seed_trace_dir(brr_dir: Path, rel: str) -> Path:
    path = brr_dir / rel
    path.mkdir(parents=True, exist_ok=True)
    (path / "stdout.txt").write_text("ok\n", encoding="utf-8")
    return path


def test_cleanup_traces_on_success_removes_dirs_and_meta(tmp_path):
    brr_dir = tmp_path / ".brr"
    tasks_dir = brr_dir / "tasks"
    tasks_dir.mkdir(parents=True)
    trace_a = _seed_trace_dir(brr_dir, "traces/daemon-run/evt-1-attempt-1")
    trace_b = _seed_trace_dir(brr_dir, "traces/kb-maintenance/kb-1")
    task = Task(id="task-clean", event_id="evt-1", body="x", status="done")
    task.meta["trace_dirs"] = (
        "traces/daemon-run/evt-1-attempt-1, traces/kb-maintenance/kb-1"
    )
    task.save(tasks_dir)

    daemon._cleanup_traces_on_success(brr_dir, tasks_dir, task)

    assert not trace_a.exists()
    assert not trace_b.exists()
    assert "trace_dirs" not in task.meta
    reloaded = Task.from_file(tasks_dir / f"{task.id}.md")
    assert reloaded is not None
    assert "trace_dirs" not in reloaded.meta


def test_cleanup_traces_on_success_keeps_on_failure(tmp_path):
    brr_dir = tmp_path / ".brr"
    tasks_dir = brr_dir / "tasks"
    tasks_dir.mkdir(parents=True)
    trace = _seed_trace_dir(brr_dir, "traces/daemon-run/evt-2-attempt-1")
    for status in ("error", "conflict"):
        task = Task(id=f"task-{status}", event_id="evt-2", body="x", status=status)
        task.meta["trace_dirs"] = "traces/daemon-run/evt-2-attempt-1"
        task.save(tasks_dir)

        daemon._cleanup_traces_on_success(brr_dir, tasks_dir, task)

        assert trace.exists(), f"trace removed on status={status}"
        assert task.meta.get("trace_dirs"), f"meta cleared on status={status}"


def test_start_allows_same_pid_during_reexec(tmp_path, monkeypatch):
    write_repo_scaffold(tmp_path)
    calls: list[str] = []

    monkeypatch.setenv("BRR_REEXEC", "1")
    monkeypatch.setattr(daemon, "read_pid", lambda _brr_dir: daemon.os.getpid())
    monkeypatch.setattr(daemon, "_write_pid", lambda _brr_dir: calls.append("write-pid"))
    monkeypatch.setattr(daemon, "_clear_pid", lambda _brr_dir: calls.append("clear-pid"))
    monkeypatch.setattr(daemon, "_start_gates", lambda *_args: [])
    monkeypatch.setattr(daemon.conf, "load_config", lambda _root: {})
    monkeypatch.setattr(daemon.signal, "signal", lambda *_args: None)

    def stop_on_scan(_inbox):
        calls.append("scan")
        raise StopIteration

    monkeypatch.setattr(daemon.protocol, "list_pending", stop_on_scan)

    with pytest.raises(StopIteration):
        daemon.start(tmp_path)

    assert calls == ["write-pid", "scan", "clear-pid"]


def test_start_rejects_existing_pid_without_reexec(tmp_path, monkeypatch):
    write_repo_scaffold(tmp_path)
    monkeypatch.delenv("BRR_REEXEC", raising=False)
    monkeypatch.setattr(daemon, "read_pid", lambda _brr_dir: daemon.os.getpid())

    with pytest.raises(SystemExit) as exc:
        daemon.start(tmp_path)

    assert "daemon already running" in str(exc.value)


def test_start_rejects_different_pid_during_reexec(tmp_path, monkeypatch):
    write_repo_scaffold(tmp_path)
    monkeypatch.setenv("BRR_REEXEC", "1")
    monkeypatch.setattr(daemon, "read_pid", lambda _brr_dir: daemon.os.getpid() + 1)

    with pytest.raises(SystemExit) as exc:
        daemon.start(tmp_path)

    assert "daemon already running" in str(exc.value)


def test_dev_reload_mode_from_config_reexecs_at_idle_boundary(tmp_path, monkeypatch):
    write_repo_scaffold(tmp_path)
    order: list[str] = []

    class FakeWatcher:
        def changed(self):
            order.append("watch")
            return True

    def _stop_after_reexec():
        order.append("reexec")
        raise StopIteration

    monkeypatch.setattr(
        daemon.reload_mod.DevReloadWatcher,
        "for_repo",
        classmethod(lambda cls, _repo_root: order.append("watcher") or FakeWatcher()),
    )
    monkeypatch.setattr(daemon.reload_mod, "reexec", _stop_after_reexec)
    monkeypatch.setattr(daemon, "read_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_write_pid", lambda _brr_dir: order.append("write-pid"))
    monkeypatch.setattr(daemon, "_clear_pid", lambda _brr_dir: order.append("clear-pid"))
    monkeypatch.setattr(daemon, "_start_gates", lambda *_args: [])
    monkeypatch.setattr(daemon.conf, "load_config", lambda _root: {"dev_reload": True})
    monkeypatch.setattr(
        daemon.protocol,
        "list_pending",
        lambda _inbox: (_ for _ in ()).throw(AssertionError("should reexec first")),
    )
    monkeypatch.setattr(daemon.signal, "signal", lambda *_args: None)

    with pytest.raises(StopIteration):
        daemon.start(tmp_path)

    assert order == ["write-pid", "watcher", "watch", "reexec", "clear-pid"]


def test_dev_reload_reexecs_only_after_task_push(tmp_path, monkeypatch):
    write_repo_scaffold(tmp_path)
    event = {
        "id": "evt-reload",
        "status": "pending",
        "_path": tmp_path / ".brr" / "inbox" / "evt-reload.md",
    }
    event["_path"].write_text(
        "---\nid: evt-reload\nstatus: pending\n---\nhelp\n",
        encoding="utf-8",
    )
    order: list[str] = []

    class FakeWatcher:
        def __init__(self):
            self.calls = 0

        def changed(self):
            self.calls += 1
            order.append(f"watch:{self.calls}")
            return self.calls == 2

    watcher = FakeWatcher()

    def _stop_after_reexec():
        raise StopIteration

    monkeypatch.setattr(
        daemon.reload_mod.DevReloadWatcher,
        "for_repo",
        classmethod(lambda cls, _repo_root: watcher),
    )
    monkeypatch.setattr(daemon.reload_mod, "reexec", _stop_after_reexec)
    monkeypatch.setattr(daemon, "read_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_write_pid", lambda _brr_dir: order.append("write-pid"))
    monkeypatch.setattr(daemon, "_clear_pid", lambda _brr_dir: order.append("clear-pid"))
    monkeypatch.setattr(daemon, "_start_gates", lambda *_args: [])
    monkeypatch.setattr(daemon.conf, "load_config", lambda _root: {})
    monkeypatch.setattr(
        daemon.protocol,
        "list_pending",
        lambda _inbox: [event],
    )
    monkeypatch.setattr(
        daemon.protocol,
        "set_status",
        lambda _event, status: order.append(f"status:{status}"),
    )
    monkeypatch.setattr(
        daemon,
        "_run_worker",
        lambda *_args, **_kwargs: (
            order.append("worker")
            or Task(
                id="task-reload",
                event_id="evt-reload",
                body="help",
                status="done",
            )
        ),
    )
    monkeypatch.setattr(
        daemon,
        "_push_if_needed",
        lambda *_args, **_kwargs: order.append("push"),
    )
    monkeypatch.setattr(daemon.signal, "signal", lambda *_args: None)

    with pytest.raises(StopIteration):
        daemon.start(tmp_path, dev_reload=True)

    assert order == [
        "write-pid",
        "watch:1",
        "status:processing",
        "worker",
        "status:done",
        "push",
        "watch:2",
        "clear-pid",
    ]


def test_kb_maintenance_runs_when_kb_changed(tmp_path, monkeypatch):
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-kb", body="update docs")
    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _root: "codex")
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts,
        "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: f"P {eid}",
    )
    monkeypatch.setattr(daemon, "_kb_changed", lambda _root: True)
    monkeypatch.setattr(
        daemon.prompts,
        "build_kb_maintenance_prompt",
        lambda _root: "KB MAINTENANCE",
    )
    maintenance: list[str] = []

    monkeypatch.setattr(
        daemon.envs, "get_env",
        lambda _name: StubWorktreeEnv(invoke_fn=succeed_invoke("ok\n")),
    )

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
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-skip", body="quick fix")
    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _root: "codex")
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts,
        "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: f"P {eid}",
    )
    monkeypatch.setattr(daemon, "_kb_changed", lambda _root: False)
    maintenance: list[str] = []

    monkeypatch.setattr(
        daemon.envs, "get_env",
        lambda _name: StubWorktreeEnv(invoke_fn=succeed_invoke("ok\n")),
    )

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


def test_kb_maintenance_runs_on_preflight_findings_even_when_kb_unchanged(
    tmp_path, monkeypatch,
):
    """Preflight is the safety net: if it sees inconsistencies left over
    from an earlier task, the maintenance pass runs even when the
    current task didn't touch ``kb/``.
    """
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-preflight", body="no kb changes here")
    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _root: "codex")
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts,
        "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: f"P {eid}",
    )
    monkeypatch.setattr(daemon, "_kb_changed", lambda _root: False)
    monkeypatch.setattr(
        daemon.prompts,
        "build_kb_maintenance_prompt",
        lambda _root: "KB MAINTENANCE BASE",
    )
    monkeypatch.setattr(
        daemon.kb_preflight,
        "scan",
        lambda _root: [
            daemon.kb_preflight.Finding(
                type="missing-from-index",
                target="kb/decision-orphan.md",
                description="needs an entry",
            ),
        ],
    )

    captured: list[str] = []

    monkeypatch.setattr(
        daemon.envs, "get_env",
        lambda _name: StubWorktreeEnv(invoke_fn=succeed_invoke("ok\n")),
    )

    def fake_invoke_runner(runner_name, invocation, cfg=None, *, trace=False):
        if invocation.kind == "kb-maintenance":
            captured.append(invocation.prompt)
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="ok", stderr="", returncode=0, trace_dir=None, artifacts=[],
        )

    monkeypatch.setattr(daemon.runner, "invoke_runner", fake_invoke_runner)

    task = daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    assert task.status == "done"
    assert len(captured) == 1, captured
    prompt = captured[0]
    assert "KB MAINTENANCE BASE" in prompt
    assert "Findings (deterministic preflight)" in prompt
    assert "missing-from-index" in prompt
    assert "kb/decision-orphan.md" in prompt


def test_kb_maintenance_skipped_when_clean_and_unchanged(tmp_path, monkeypatch):
    """Skip-fast: preflight clean + kb unchanged → no LLM pass at all."""
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-clean", body="non-kb work")
    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _root: "codex")
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts,
        "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: f"P {eid}",
    )
    monkeypatch.setattr(daemon, "_kb_changed", lambda _root: False)
    monkeypatch.setattr(daemon.kb_preflight, "scan", lambda _root: [])

    captured: list[str] = []

    monkeypatch.setattr(
        daemon.envs, "get_env",
        lambda _name: StubWorktreeEnv(invoke_fn=succeed_invoke("ok\n")),
    )

    def fake_invoke_runner(runner_name, invocation, cfg=None, *, trace=False):
        if invocation.kind == "kb-maintenance":
            captured.append(invocation.prompt)
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="ok", stderr="", returncode=0, trace_dir=None, artifacts=[],
        )

    monkeypatch.setattr(daemon.runner, "invoke_runner", fake_invoke_runner)

    daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    assert captured == []


def test_kb_maintenance_runs_when_kb_changed_with_clean_preflight(tmp_path, monkeypatch):
    """When kb changed but preflight is clean, run with the bare prompt."""
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-touched", body="touched kb but cleanly")
    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _root: "codex")
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts,
        "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: f"P {eid}",
    )
    monkeypatch.setattr(daemon, "_kb_changed", lambda _root: True)
    monkeypatch.setattr(
        daemon.prompts,
        "build_kb_maintenance_prompt",
        lambda _root: "KB MAINTENANCE BASE",
    )
    monkeypatch.setattr(daemon.kb_preflight, "scan", lambda _root: [])

    captured: list[str] = []

    monkeypatch.setattr(
        daemon.envs, "get_env",
        lambda _name: StubWorktreeEnv(invoke_fn=succeed_invoke("ok\n")),
    )

    def fake_invoke_runner(runner_name, invocation, cfg=None, *, trace=False):
        if invocation.kind == "kb-maintenance":
            captured.append(invocation.prompt)
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="ok", stderr="", returncode=0, trace_dir=None, artifacts=[],
        )

    monkeypatch.setattr(daemon.runner, "invoke_runner", fake_invoke_runner)

    daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    assert captured == ["KB MAINTENANCE BASE"]


# ── _commit_kb_maintenance_edits ────────────────────────────────────


def _init_real_repo(repo: Path) -> str:
    """Real git repo with one commit seeding AGENTS.md + kb/subject-x.md.

    The maintenance commit step uses real git plumbing
    (``git add``/``commit``/``rev-list``), so these unit tests need a
    real repo rather than the stubs used elsewhere in this module.
    """
    init_git_repo(repo)
    return commit_files(repo, {
        "AGENTS.md": "# Agents\n",
        "kb/subject-x.md": "# Subject x\n\nInitial body.\n",
    })


def test_commit_kb_maintenance_edits_rolls_up_leftover_kb_changes(tmp_path):
    """The agent may forget the 'commit your edits' instruction. The
    daemon then stamps everything inside kb/ as one brr-maintenance
    commit so cleanup rides on the task's branch instead of getting
    swept under the worktree-salvage rug.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    pre_head = _init_real_repo(repo)
    # Simulate the maintenance pass touching kb/ without committing.
    (repo / "kb" / "subject-x.md").write_text(
        "# Subject x\n\nGroomed body.\n", encoding="utf-8",
    )
    (repo / "kb" / "decision-new.md").write_text(
        "# New decision\n\nStatus: accepted on 2026-05-13\n",
        encoding="utf-8",
    )

    commits, files = daemon._commit_kb_maintenance_edits(repo, pre_head)

    assert commits == 1
    assert files == 2
    # The auto-rolled commit is authored as brr maintenance, not the
    # repo's configured identity.
    author = subprocess.run(
        ["git", "log", "-1", "--format=%an <%ae>"], cwd=repo, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    assert author == "brr maintenance <brr-maintenance@brr.local>"


def test_commit_kb_maintenance_edits_counts_agent_commits(tmp_path):
    """If the maintenance agent committed on its own, the daemon
    should count those commits without adding a redundant
    brr-maintenance commit on top."""
    repo = tmp_path / "repo"
    repo.mkdir()
    pre_head = _init_real_repo(repo)
    # Simulate the agent committing its own edit cleanly.
    (repo / "kb" / "subject-x.md").write_text(
        "# Subject x\n\nGroomed by the agent.\n", encoding="utf-8",
    )
    subprocess.run(["git", "add", "kb"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "kb: groom subject-x"], cwd=repo, check=True,
        capture_output=True,
    )

    commits, files = daemon._commit_kb_maintenance_edits(repo, pre_head)

    assert commits == 1
    assert files == 1
    # Only the agent's commit exists — no brr-maintenance commit.
    log = subprocess.run(
        ["git", "log", "--format=%an"], cwd=repo, check=True,
        capture_output=True, text=True,
    ).stdout.splitlines()
    assert "brr maintenance" not in log


def test_commit_kb_maintenance_edits_quiet_when_clean(tmp_path):
    """A clean working tree with no new commits since pre_head means
    the maintenance pass had nothing to do. (0, 0) tells the daemon
    to render 'maintenance: clean' on the card."""
    repo = tmp_path / "repo"
    repo.mkdir()
    pre_head = _init_real_repo(repo)

    commits, files = daemon._commit_kb_maintenance_edits(repo, pre_head)

    assert (commits, files) == (0, 0)


def test_commit_kb_maintenance_edits_leaves_non_kb_changes_alone(tmp_path):
    """A maintenance pass that strayed outside its lane (touched
    runtime code or anything outside kb/AGENTS.md) should NOT have
    its stray edits absorbed into a kb commit. The worktree-salvage
    rule preserves the worktree so the operator sees the violation.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    pre_head = _init_real_repo(repo)
    (repo / "src").mkdir()
    (repo / "src" / "rogue.py").write_text("print('bad')\n", encoding="utf-8")
    (repo / "kb" / "subject-x.md").write_text(
        "# Subject x\n\nGroomed.\n", encoding="utf-8",
    )

    commits, files = daemon._commit_kb_maintenance_edits(repo, pre_head)

    assert commits == 1
    assert files == 1
    # The rogue file is still uncommitted — visible to the salvage
    # rule rather than silently absorbed.
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout
    assert "src/rogue.py" in status


# ── kb_maintenance_done packet ──────────────────────────────────────


def test_maybe_kb_maintenance_emits_done_packet(tmp_path, monkeypatch):
    """When brr_dir/conv_key are provided, ``_maybe_kb_maintenance``
    emits a ``kb_maintenance_done`` packet carrying the commit/file
    counts so gates can surface "maintenance: N kb commits" on the
    response card. Without this packet the pass historically
    appeared to silently drop edits."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_real_repo(repo)
    brr_dir = repo / ".brr"
    (brr_dir / "conversations").mkdir(parents=True)

    monkeypatch.setattr(daemon, "_kb_changed", lambda _root: True)
    monkeypatch.setattr(
        daemon.prompts,
        "build_kb_maintenance_prompt",
        lambda _root: "KB MAINTENANCE",
    )
    monkeypatch.setattr(daemon.kb_preflight, "scan", lambda _root: [])

    def fake_invoke_runner(runner_name, invocation, cfg=None, *, trace=False):
        # Simulate the maintenance agent groom: one kb edit, no commit.
        (repo / "kb" / "subject-x.md").write_text(
            "# Subject x\n\nGroomed.\n", encoding="utf-8",
        )
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="groomed", stderr="", returncode=0, trace_dir=None,
            artifacts=[],
        )

    monkeypatch.setattr(daemon.runner, "invoke_runner", fake_invoke_runner)

    daemon._maybe_kb_maintenance(
        repo, repo, {}, "codex",
        brr_dir=brr_dir, conv_key="telegram:1:", task_id="task-x",
    )

    from brr import conversations
    log_path = conversations.conversation_path(brr_dir, "telegram:1:")
    assert log_path.exists()
    records = [
        line for line in log_path.read_text(encoding="utf-8").splitlines()
        if '"kb_maintenance_done"' in line
    ]
    assert len(records) == 1
    record = records[0]
    assert '"commits": 1' in record
    assert '"files": 1' in record
    assert '"ok": true' in record
    assert '"task_id": "task-x"' in record


def test_maybe_kb_maintenance_emits_clean_packet_when_no_edits(tmp_path, monkeypatch):
    """A maintenance pass that ran but didn't change anything still
    emits a packet, with commits/files = 0, so the renderer can show
    'maintenance: clean'. Skipping the packet would hide the fact
    that the pass executed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_real_repo(repo)
    brr_dir = repo / ".brr"
    (brr_dir / "conversations").mkdir(parents=True)

    monkeypatch.setattr(daemon, "_kb_changed", lambda _root: True)
    monkeypatch.setattr(
        daemon.prompts,
        "build_kb_maintenance_prompt",
        lambda _root: "KB MAINTENANCE",
    )
    monkeypatch.setattr(daemon.kb_preflight, "scan", lambda _root: [])

    def fake_invoke_runner(runner_name, invocation, cfg=None, *, trace=False):
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="nothing to do", stderr="", returncode=0, trace_dir=None,
            artifacts=[],
        )

    monkeypatch.setattr(daemon.runner, "invoke_runner", fake_invoke_runner)

    daemon._maybe_kb_maintenance(
        repo, repo, {}, "codex",
        brr_dir=brr_dir, conv_key="telegram:1:", task_id="task-y",
    )

    from brr import conversations
    log_path = conversations.conversation_path(brr_dir, "telegram:1:")
    records = [
        line for line in log_path.read_text(encoding="utf-8").splitlines()
        if '"kb_maintenance_done"' in line
    ]
    assert len(records) == 1
    assert '"commits": 0' in records[0]
    assert '"files": 0' in records[0]


def test_maybe_kb_maintenance_skips_packet_when_no_routing_info(tmp_path, monkeypatch):
    """When the caller doesn't pass brr_dir/conv_key, packet emission
    is suppressed — keeps the helper safe to call from contexts that
    don't have routing info (none today, but a hedge against future
    drift)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_real_repo(repo)
    brr_dir = repo / ".brr"
    (brr_dir / "conversations").mkdir(parents=True)

    monkeypatch.setattr(daemon, "_kb_changed", lambda _root: True)
    monkeypatch.setattr(
        daemon.prompts,
        "build_kb_maintenance_prompt",
        lambda _root: "KB MAINTENANCE",
    )
    monkeypatch.setattr(daemon.kb_preflight, "scan", lambda _root: [])
    monkeypatch.setattr(
        daemon.runner,
        "invoke_runner",
        lambda runner_name, invocation, cfg=None, *, trace=False: RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="", stderr="", returncode=0, trace_dir=None, artifacts=[],
        ),
    )

    daemon._maybe_kb_maintenance(repo, repo, {}, "codex")

    # No conversations log was created since no packet was emitted.
    assert not any((brr_dir / "conversations").iterdir())


# ── Forge URL inference ──────────────────────────────────────────────


def test_forge_view_url_returns_link_for_known_remote(tmp_path):
    """When ``origin`` points at a recognised forge, ``_forge_view_url``
    constructs the branch view URL from the live remote so gates can
    show a clickable link in chat."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_real_repo(repo)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:Gurio/brr.git"],
        cwd=repo, check=True,
    )

    url = daemon._forge_view_url(repo, "origin", "brr/task-xyz")

    assert url == "https://github.com/Gurio/brr/tree/brr/task-xyz"


def test_forge_view_url_returns_none_for_unknown_remote(tmp_path):
    """A bare internal host without ``forge.kind`` configured stays
    quiet — better silent than a guessed URL."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_real_repo(repo)
    subprocess.run(
        ["git", "remote", "add", "origin",
         "git@git.example.com:team/repo.git"],
        cwd=repo, check=True,
    )

    assert daemon._forge_view_url(repo, "origin", "main") is None


def test_forge_view_url_honors_brr_config_forge_kind(tmp_path):
    """``forge.kind = gitlab`` in ``.brr/config`` teaches brr which
    template to apply to an internal host the default patterns
    don't recognise."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_real_repo(repo)
    subprocess.run(
        ["git", "remote", "add", "origin",
         "git@git.internal.example.com:team/repo.git"],
        cwd=repo, check=True,
    )
    (repo / ".brr").mkdir(exist_ok=True)
    (repo / ".brr" / "config").write_text(
        "forge.kind=gitlab\n", encoding="utf-8",
    )

    url = daemon._forge_view_url(repo, "origin", "feature/foo")

    assert url == (
        "https://git.internal.example.com/team/repo/-/tree/feature/foo"
    )


def test_forge_view_url_returns_none_for_missing_remote(tmp_path):
    """If the remote isn't configured at all, the helper returns
    ``None`` rather than raising — the push has already happened and
    the link is just polish."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_real_repo(repo)

    assert daemon._forge_view_url(repo, "origin", "main") is None


# ── Task-touched kb pages ────────────────────────────────────────────


def test_kb_pages_touched_since_lists_changed_paths(tmp_path):
    """``_kb_pages_touched_since`` returns the kb / AGENTS.md files a
    task changed relative to the seed-ref OID so the maintenance
    pass has a concrete review target."""
    repo = tmp_path / "repo"
    repo.mkdir()
    pre_head = _init_real_repo(repo)
    (repo / "kb").mkdir(exist_ok=True)
    (repo / "kb" / "subject-x.md").write_text("# X\n", encoding="utf-8")
    (repo / "AGENTS.md").write_text("# Agents v2\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "foo.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "task work"], cwd=repo, check=True,
        capture_output=True,
    )

    touched = daemon._kb_pages_touched_since(repo, pre_head)

    # Only kb/ and AGENTS.md paths appear — src/ is filtered out.
    assert touched == ["AGENTS.md", "kb/subject-x.md"]


def test_kb_pages_touched_since_returns_empty_without_pre_head(tmp_path):
    """A missing seed-ref OID falls back to an empty list rather
    than triggering a "diff against HEAD" that would always return
    the working tree's full set."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_real_repo(repo)

    assert daemon._kb_pages_touched_since(repo, None) == []


def test_kb_pages_touched_since_skips_non_kb_changes(tmp_path):
    """Non-kb edits don't show up — the review target is intentionally
    narrow to keep the maintenance agent in its lane."""
    repo = tmp_path / "repo"
    repo.mkdir()
    pre_head = _init_real_repo(repo)
    (repo / "src").mkdir()
    (repo / "src" / "foo.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "code change"], cwd=repo, check=True,
        capture_output=True,
    )

    assert daemon._kb_pages_touched_since(repo, pre_head) == []


def test_format_touched_block_renders_paths_when_present():
    """The block uses the header cue the maintenance prompt
    references ('Task-touched kb pages') and lists each path on its
    own line."""
    block = daemon._format_touched_block(
        ["kb/subject-x.md", "AGENTS.md"]
    )

    assert "## Task-touched kb pages" in block
    assert "- `kb/subject-x.md`" in block
    assert "- `AGENTS.md`" in block


def test_format_touched_block_empty_when_no_paths():
    """An empty list collapses to ``""`` so callers can join without
    leaking an empty header into the prompt."""
    assert daemon._format_touched_block([]) == ""

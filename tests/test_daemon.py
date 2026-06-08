"""Tests for the daemon worker after the triage stage was removed."""

import subprocess
import threading
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


def _stub_env_isolated(monkeypatch, tmp_path):
    """Replace env backends with stand-ins that don't touch git/docker."""
    worktree_path = tmp_path / ".brr" / "worktrees" / "stub"
    worktree_path.mkdir(parents=True, exist_ok=True)
    finalized: list[str] = []

    class StubEnv:
        name = "worktree"

        def prepare(self, task, repo_root, cfg, *, branch_plan, response_path,
                    outbox_path=None):
            return envs.RunContext(
                name=self.name,
                cwd=worktree_path,
                repo_root=repo_root,
                runtime_dir=tmp_path / ".brr",
                response_path_host=response_path,
                response_path_env=response_path,
                outbox_host=outbox_path,
                outbox_env=outbox_path,
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
    # Happy path: the daemon-run invocation is the only runner call —
    # no separate triage stage, no retry. The labelled-kind check
    # captures both halves of that intent in one assertion.
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

        def prepare(self, task, repo_root, cfg, *, branch_plan, response_path,
                    outbox_path=None):
            return envs.RunContext(
                name=self.name, cwd=tmp_path, repo_root=repo_root,
                runtime_dir=tmp_path / ".brr",
                response_path_host=response_path,
                response_path_env=response_path,
                outbox_host=outbox_path,
                outbox_env=outbox_path,
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

    real_resolve = daemon.branching.resolve_publish_plan

    def wrapped_resolve(repo_root, ev, cfg):
        call_order.append("resolve")
        return real_resolve(repo_root, ev, cfg)

    monkeypatch.setattr(daemon.sync, "refresh_before_task", fake_refresh)
    monkeypatch.setattr(daemon.branching, "resolve_publish_plan", wrapped_resolve)

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
    pending_calls: list[int] = []

    monkeypatch.setattr(daemon, "read_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_write_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_clear_pid", lambda _brr_dir: None)
    monkeypatch.setattr(daemon, "_start_gates", lambda *_args: [])
    monkeypatch.setattr(daemon.conf, "load_config", lambda _root: {})
    # Compress the polling sleep so the loop reaches its second
    # iteration (where StopIteration is raised) without the test
    # waiting on the production interval.
    monkeypatch.setattr(daemon, "_SCAN_INTERVAL", 0.01)

    def fake_list_pending(_inbox):
        pending_calls.append(1)
        if len(pending_calls) == 1:
            return [event]
        # Second call breaks the loop in the main thread. The finally
        # block waits for the in-flight worker to finish before
        # tearing the pool down, so statuses observed by the worker
        # thread are present when pytest.raises captures the exit.
        raise StopIteration

    monkeypatch.setattr(daemon.protocol, "list_pending", fake_list_pending)
    monkeypatch.setattr(daemon.protocol, "set_status", lambda _ev, status: statuses.append(status))
    monkeypatch.setattr(
        daemon,
        "_run_worker",
        lambda *_a, **_k: Task(id="task-err", event_id="evt-err", body="help", status="error"),
    )
    monkeypatch.setattr(daemon, "publish", lambda *_a, **_k: None)
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
    trace_b = _seed_trace_dir(brr_dir, "traces/daemon-run/evt-1-attempt-2")
    task = Task(id="task-clean", event_id="evt-1", body="x", status="done")
    task.meta["trace_dirs"] = (
        "traces/daemon-run/evt-1-attempt-1, traces/daemon-run/evt-1-attempt-2"
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
    order_lock = threading.Lock()

    def record(label: str) -> None:
        # Worker thread and main thread both append; the lock keeps
        # the timeline observable without rare interleaving artefacts.
        with order_lock:
            order.append(label)

    class FakeWatcher:
        def __init__(self):
            self.calls = 0

        def changed(self):
            self.calls += 1
            record(f"watch:{self.calls}")
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
    monkeypatch.setattr(daemon, "_write_pid", lambda _brr_dir: record("write-pid"))
    monkeypatch.setattr(daemon, "_clear_pid", lambda _brr_dir: record("clear-pid"))
    monkeypatch.setattr(daemon, "_start_gates", lambda *_args: [])
    monkeypatch.setattr(daemon.conf, "load_config", lambda _root: {})
    # Short scan interval so the loop's second iteration (where the
    # watcher reports a change and the now-empty pool triggers
    # reexec) lands quickly after the worker thread finishes.
    monkeypatch.setattr(daemon, "_SCAN_INTERVAL", 0.05)
    monkeypatch.setattr(
        daemon.protocol,
        "list_pending",
        lambda _inbox: [event],
    )
    monkeypatch.setattr(
        daemon.protocol,
        "set_status",
        lambda _event, status: record(f"status:{status}"),
    )

    def fake_run_worker(*_args, **_kwargs):
        record("worker")
        return Task(
            id="task-reload",
            event_id="evt-reload",
            body="help",
            status="done",
        )

    monkeypatch.setattr(daemon, "_run_worker", fake_run_worker)
    monkeypatch.setattr(
        daemon,
        "publish",
        lambda *_args, **_kwargs: record("push"),
    )
    monkeypatch.setattr(daemon.signal, "signal", lambda *_args: None)

    with pytest.raises(StopIteration):
        daemon.start(tmp_path, dev_reload=True)

    # The dispatch order is deterministic: the main thread records
    # write-pid → watch:1 → status:processing → submits the worker;
    # the worker thread records worker → status:done → push during
    # the scan-interval sleep; the next iteration records watch:2 →
    # observes the empty pool → reexecs; the finally block records
    # clear-pid.
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


def test_publish_runs_with_task_meta_for_pr_rebase(tmp_path, monkeypatch):
    """The publish kernel reads ``publish_branch`` + ``expected_remote_oid``
    directly from ``task.meta`` (no extra threading from the worker)."""
    task = Task(
        id="task-lease",
        event_id="evt-lease",
        body="rebase",
        status="done",
        source="github",
        conversation_key="github:owner/repo#17",
        meta={
            "publish_branch": "brr/deliver-pr-rebase",
            "target_branch": "brr/deliver-pr-rebase",
            "expected_remote_oid": "6c1ca158d19c6ba40c06e8a46f7c338ada056246",
        },
    )
    monkeypatch.setattr(daemon, "_run_worker", lambda *_a, **_k: task)
    monkeypatch.setattr(daemon.protocol, "set_status", lambda *_a, **_k: None)
    captured: dict = {}

    def fake_publish(repo, t):
        captured["repo"] = repo
        captured["publish_branch"] = t.meta.get("publish_branch")
        captured["expected_remote_oid"] = t.meta.get("expected_remote_oid")

    monkeypatch.setattr(daemon, "publish", fake_publish)

    event = {"id": "evt-lease", "source": "github", "body": "rebase"}
    daemon._run_worker_and_finalize(event, tmp_path, tmp_path / ".brr", {}, 0)

    assert captured["publish_branch"] == "brr/deliver-pr-rebase"
    assert (
        captured["expected_remote_oid"]
        == "6c1ca158d19c6ba40c06e8a46f7c338ada056246"
    )


def test_worker_finalize_tolerates_gate_cleanup_after_response(
    tmp_path, monkeypatch,
):
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-cleaned", body="answer first")

    def fake_run_worker(ev, *_args, **_kwargs):
        daemon._set_event_status_if_present(ev, "done")
        ev["_path"].unlink()
        return Task(
            id="task-cleaned",
            event_id=ev["id"],
            body=ev["body"],
            source=ev["source"],
            status="done",
        )

    monkeypatch.setattr(daemon, "_run_worker", fake_run_worker)
    monkeypatch.setattr(daemon, "publish", lambda *_args, **_kwargs: None)

    task = daemon._run_worker_and_finalize(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    assert task.status == "done"


# ── Forge URL inference ──────────────────────────────────────────────
#
# The URL-template logic itself is covered exhaustively in
# tests/test_forges.py. ``daemon._forge_view_url`` is a thin wrapper
# that reads the remote URL via ``gitops``, reads forge overrides from
# ``.brr/config``, and swallows any failure into ``None``. The tests
# below only cover those wrapper-specific responsibilities.


def test_forge_view_url_feeds_remote_and_config_overrides_to_forges(monkeypatch, tmp_path):
    """The wrapper reads the remote URL via gitops and the
    ``forge.kind`` / ``forge.url_base`` overrides via the config
    loader, then delegates to ``forges.view_branch_url``. This guards
    the *plumbing* — that the wrapper still wires the right inputs
    together — without re-testing URL templating."""
    monkeypatch.setattr(
        daemon.gitops, "remote_url",
        lambda _repo, _remote: "git@git.internal.example.com:team/repo.git",
    )
    monkeypatch.setattr(
        daemon.conf, "load_config",
        lambda _repo: {
            "forge.kind": "gitlab",
            "forge.url_base": "https://gitlab.example.com",
        },
    )
    captured: dict = {}

    def fake_view_branch_url(url, branch, **kwargs):
        captured["args"] = (url, branch)
        captured["kwargs"] = kwargs
        return "https://gitlab.example.com/team/repo/-/tree/feature/foo"

    monkeypatch.setattr(daemon.forges, "view_branch_url", fake_view_branch_url)

    url = daemon._forge_view_url(tmp_path, "origin", "feature/foo")

    assert url == "https://gitlab.example.com/team/repo/-/tree/feature/foo"
    assert captured["args"] == (
        "git@git.internal.example.com:team/repo.git", "feature/foo",
    )
    assert captured["kwargs"] == {
        "override_kind": "gitlab",
        "override_url_base": "https://gitlab.example.com",
    }


def test_forge_view_url_returns_none_when_remote_missing(monkeypatch, tmp_path):
    """No remote URL means nothing to template against — the wrapper
    short-circuits to ``None`` rather than calling ``forges`` with
    ``None``."""
    monkeypatch.setattr(daemon.gitops, "remote_url", lambda _repo, _remote: None)
    called = False

    def _should_not_call(*_a, **_kw):
        nonlocal called
        called = True
        return "should not happen"

    monkeypatch.setattr(daemon.forges, "view_branch_url", _should_not_call)

    assert daemon._forge_view_url(tmp_path, "origin", "main") is None
    assert called is False


def test_forge_view_url_swallows_exceptions(monkeypatch, tmp_path):
    """The push has already succeeded by the time we reach
    ``_forge_view_url``; a missing link is never worth failing the
    task over, so any exception in the resolve chain returns
    ``None``."""
    def _boom(*_a, **_kw):
        raise RuntimeError("git binary exploded")

    monkeypatch.setattr(daemon.gitops, "remote_url", _boom)

    assert daemon._forge_view_url(tmp_path, "origin", "main") is None

"""Tests for the daemon worker after the triage stage was removed."""

import subprocess
import threading
from pathlib import Path

import pytest

from brr import daemon, envs, presence, protocol
from brr.run import Run
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

        def finalize(self, ctx, task, runs_dir):
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
        lambda task, eid, rp, root, **kw: f"PROMPT {eid} {kw.get('run_id')} -> {rp}",
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
    persisted = Run.from_file(tmp_path / ".brr" / "runs" / task.id / "run.md")
    assert persisted is not None
    assert persisted.status == "done"
    response = (tmp_path / ".brr" / "responses" / "evt-1.md").read_text(encoding="utf-8")
    assert response == "plain answer\n"


def test_run_worker_does_not_infer_native_hooks_from_runner_name(
    tmp_path, monkeypatch
):
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-no-hooks")
    _stub_env_isolated(monkeypatch, tmp_path)

    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _root: "claude")
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.runner,
        "profile_hooks_flavour",
        lambda _runner_name, _repo_root=None: None,
    )
    monkeypatch.setattr(
        daemon.hooks_mod,
        "hook_capability",
        lambda *_args, **_kwargs: pytest.fail(
            "hook capability should only be checked for declared hooks"
        ),
    )
    monkeypatch.setattr(
        daemon.prompts, "build_daemon_prompt", lambda *args, **kwargs: "PROMPT"
    )
    base_env = envs.get_env("worktree")

    def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
        Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
        Path(invocation.response_path).write_text("ok\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="ok\n", stderr="", returncode=0, trace_dir=None, artifacts=[],
        )

    monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    assert task.status == "done"


def test_run_worker_installs_native_hooks_only_when_profile_declares_them(
    tmp_path, monkeypatch
):
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-declared-hooks")
    _stub_env_isolated(monkeypatch, tmp_path)

    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _root: "custom")
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.runner,
        "profile_hooks_flavour",
        lambda _runner_name, _repo_root=None: "claude",
    )
    checked: list[str] = []
    installed: list[str] = []

    def fake_capability(flavour, _cwd):
        checked.append(flavour)
        return True

    def fake_install(flavour, cwd):
        installed.append(flavour)
        return cwd / ".claude" / "settings.local.json"

    monkeypatch.setattr(daemon.hooks_mod, "hook_capability", fake_capability)
    monkeypatch.setattr(daemon.hooks_mod, "install_hook_config", fake_install)
    monkeypatch.setattr(
        daemon.prompts, "build_daemon_prompt", lambda *args, **kwargs: "PROMPT"
    )
    base_env = envs.get_env("worktree")
    seen_env: dict[str, str] = {}

    def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
        seen_env.update(invocation.env)
        Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
        Path(invocation.response_path).write_text("ok\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="ok\n", stderr="", returncode=0, trace_dir=None, artifacts=[],
        )

    monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    assert task.status == "done"
    assert checked == ["claude"]
    assert installed == ["claude"]
    assert seen_env["BRR_RUNNER"] == "claude"


def test_run_worker_threads_runner_quota_into_prompt(tmp_path, monkeypatch):
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-quota")
    _stub_env_isolated(monkeypatch, tmp_path)

    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _root: "codex")
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.runner_quota,
        "describe_runner_quota",
        lambda runner_name, _cfg, _brr_dir: (
            "weekly 0% - resets 2026-06-17T01:29Z"
            if runner_name == "codex"
            else None
        ),
    )
    captured: dict[str, object] = {}

    def _prompt(_task, _eid, _rp, _root, **kw):
        captured.update(kw)
        return "PROMPT"

    monkeypatch.setattr(daemon.prompts, "build_daemon_prompt", _prompt)
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

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    assert task.status == "done"
    assert captured["runner_medium"] == "codex"
    assert captured["runner_quota"] == "weekly 0% - resets 2026-06-17T01:29Z"


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
    assert event["status"] == "done"
    response = protocol.read_response(tmp_path / ".brr" / "responses", "evt-2")
    assert response is not None
    assert "environment setup failed: boom" in response
    persisted = Run.from_file(tmp_path / ".brr" / "runs" / task.id / "run.md")
    assert persisted is not None
    assert persisted.status == "error"


def test_presence_registered_during_run_and_cleared_after(tmp_path, monkeypatch):
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-p1")
    _stub_env_isolated(monkeypatch, tmp_path)
    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _root: "codex")
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts, "build_daemon_prompt", lambda *a, **k: "PROMPT",
    )
    # _run_worker_and_finalize calls publish at the end; stub it so the test
    # exercises the presence finally without real git pushes.
    monkeypatch.setattr(daemon, "publish", lambda *_a, **_k: None)

    brr_dir = tmp_path / ".brr"
    seen: dict[str, object] = {}
    base_env = envs.get_env("worktree")

    def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
        # Mid-run: this thought is recorded as present on its stream, so a
        # concurrent session would see it and could avoid colliding.
        active = presence.list_active(brr_dir)
        seen["during"] = [(e["kind"], e["run_id"]) for e in active]
        Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
        Path(invocation.response_path).write_text("ok\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="ok\n", stderr="", returncode=0, trace_dir=None, artifacts=[],
        )

    monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

    task = daemon._run_worker_and_finalize(
        event, tmp_path, brr_dir / "responses", {}, 0,
    )

    assert seen["during"] == [("daemon", task.id)]
    # The thought is no longer awake → its presence entry is gone.
    assert presence.list_active(brr_dir) == []


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


def test_run_worker_accepts_current_outbox_reply_without_stdout(
    tmp_path, monkeypatch,
):
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-outbox-only")
    _stub_env_isolated(monkeypatch, tmp_path)
    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _root: "codex")
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts,
        "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: "PROMPT",
    )
    base_env = envs.get_env("worktree")

    def fake_invoke(_self, ctx, runner_name, invocation, cfg=None, *, trace=False):
        assert ctx.outbox_host is not None
        ctx.outbox_host.mkdir(parents=True, exist_ok=True)
        (ctx.outbox_host / "reply.md").write_text(
            "handled through outbox\n", encoding="utf-8",
        )
        return RunnerResult(
            invocation=invocation,
            runner_name=runner_name,
            command=["mock"],
            stdout="",
            stderr="",
            returncode=0,
            trace_dir=None,
            artifacts=[],
        )

    monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 1,
    )

    assert task.status == "done"
    assert event["status"] == "done"
    responses = tmp_path / ".brr" / "responses"
    assert protocol.read_response(responses, "evt-outbox-only") is None
    assert [
        protocol.read_partial(p)
        for p in protocol.list_partials(responses, "evt-outbox-only")
    ] == ["handled through outbox"]


def test_drain_outbox_queues_respawn_request(tmp_path):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    outbox = brr_dir / "outbox" / "evt-current"
    outbox.mkdir(parents=True)
    path = protocol.create_event(
        inbox,
        "telegram",
        "original task",
        status="processing",
        conversation_key="telegram:42:",
        chat_id="42",
        origin_message_key="telegram:42::99",
    )
    event_id = path.stem
    (outbox / "respawn.md").write_text(
        "---\n"
        "respawn: true\n"
        "shell: codex-mini\n"
        "repo: Gurio/other\n"
        "reason: needs a stronger core\n"
        "defer_until: +30m\n"
        "---\n"
        "carry this exact task forward\n",
        encoding="utf-8",
    )
    task = Run(
        id="run-dispatch",
        event_id=event_id,
        body="original task",
        source="telegram",
        conversation_key="telegram:42:",
    )
    stats: dict[str, int] = {}

    promoted = daemon._drain_outbox(
        daemon._WorkerEmit(brr_dir, "telegram:42:", event_id),
        task,
        responses,
        event_id,
        outbox,
        inbox,
        stats=stats,
    )

    assert promoted == 1
    assert stats == {"respawn": 1}
    spawned = [
        ev for ev in protocol.list_pending(inbox)
        if ev.get("respawned_from_event") == event_id
    ][0]
    assert spawned["source"] == "telegram"
    assert spawned["conversation_key"] == "telegram:42:"
    assert spawned["chat_id"] == 42
    assert spawned["shell"] == "codex-mini"
    assert spawned["repo"] == "Gurio/other"
    assert spawned["repo_label"] == "Gurio/other"
    assert spawned["respawn_reason"] == "needs a stronger core"
    assert spawned["body"] == "carry this exact task forward"
    assert "origin_message_key" not in spawned
    assert protocol.event_is_deferred(spawned)


def test_drain_outbox_quality_respawn_resolves_local_escalation(
    tmp_path, monkeypatch,
):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    responses = brr_dir / "responses"
    outbox = brr_dir / "outbox" / "evt-current"
    outbox.mkdir(parents=True)
    path = protocol.create_event(
        inbox,
        "telegram",
        "original task",
        status="processing",
        conversation_key="telegram:42:",
        chat_id="42",
    )
    event_id = path.stem
    monkeypatch.setattr(
        daemon.runner,
        "quality_escalation_runner",
        lambda _repo, current, *, target_class=None, tried=(): (
            "claude-opus"
            if current == "codex-mini" and target_class == "strong"
            else None
        ),
    )
    (outbox / "respawn.md").write_text(
        "---\n"
        "respawn: true\n"
        "quality: escalate\n"
        "reason: needs a stronger core\n"
        "---\n"
        "carry this exact task forward\n",
        encoding="utf-8",
    )
    task = Run(
        id="run-dispatch",
        event_id=event_id,
        body="original task",
        source="telegram",
        conversation_key="telegram:42:",
        meta={"runner_name": "codex-mini"},
    )
    stats: dict[str, int] = {}

    promoted = daemon._drain_outbox(
        daemon._WorkerEmit(brr_dir, "telegram:42:", event_id),
        task,
        responses,
        event_id,
        outbox,
        inbox,
        repo_root=tmp_path,
        stats=stats,
    )

    assert promoted == 1
    assert stats == {"respawn": 1}
    spawned = [
        ev for ev in protocol.list_pending(inbox)
        if ev.get("respawned_from_event") == event_id
    ][0]
    assert spawned["shell"] == "claude-opus"
    assert spawned["respawn_quality"] == "strong"
    assert spawned["respawn_reason"] == "needs a stronger core"


def test_run_worker_writes_terminal_failure_response_on_runner_error(
    tmp_path, monkeypatch,
):
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-run-fail")
    _stub_env_isolated(monkeypatch, tmp_path)
    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _root: "codex")
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts,
        "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: "PROMPT",
    )
    monkeypatch.setattr(daemon, "publish", lambda *_a, **_k: None)
    base_env = envs.get_env("worktree")

    def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
        return RunnerResult(
            invocation=invocation,
            runner_name=runner_name,
            command=["mock"],
            stdout="",
            stderr="connection dropped",
            returncode=1,
            trace_dir=None,
            artifacts=[],
        )

    monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

    task = daemon._run_worker_and_finalize(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    assert task.status == "error"
    assert event["status"] == "done"
    response = protocol.read_response(tmp_path / ".brr" / "responses", "evt-run-fail")
    assert response is not None
    assert "runner failed after 1 attempt(s): connection dropped" in response


def test_run_worker_writes_terminal_failure_response_after_empty_stdout(
    tmp_path, monkeypatch,
):
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-empty-final")
    _stub_env_isolated(monkeypatch, tmp_path)
    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _root: "codex")
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts,
        "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: "PROMPT",
    )
    base_env = envs.get_env("worktree")

    def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
        return RunnerResult(
            invocation=invocation,
            runner_name=runner_name,
            command=["mock"],
            stdout="",
            stderr="",
            returncode=0,
            trace_dir=None,
            artifacts=[],
        )

    monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

    task = daemon._run_worker(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    assert task.status == "error"
    assert event["status"] == "done"
    response = protocol.read_response(tmp_path / ".brr" / "responses", "evt-empty-final")
    assert response is not None
    assert "runner produced no reply after 1 attempt(s)" in response


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

    monkeypatch.setattr(daemon.sync, "refresh_before_run", fake_refresh)
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
    assert captured_targets, "sync.refresh_before_run was not called"


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
        daemon.sync, "refresh_before_run",
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
        lambda *_a, **_k: Run(id="task-err", event_id="evt-err", body="help", status="error"),
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
    runs_dir = brr_dir / "runs"
    runs_dir.mkdir(parents=True)
    trace_a = _seed_trace_dir(brr_dir, "traces/daemon-run/evt-1-attempt-1")
    trace_b = _seed_trace_dir(brr_dir, "traces/daemon-run/evt-1-attempt-2")
    task = Run(id="task-clean", event_id="evt-1", body="x", status="done")
    task.meta["trace_dirs"] = (
        "traces/daemon-run/evt-1-attempt-1, traces/daemon-run/evt-1-attempt-2"
    )
    task.save(runs_dir)

    daemon._cleanup_traces_on_success(brr_dir, runs_dir, task)

    assert not trace_a.exists()
    assert not trace_b.exists()
    assert "trace_dirs" not in task.meta
    reloaded = Run.from_file(runs_dir / task.id / "run.md")
    assert reloaded is not None
    assert "trace_dirs" not in reloaded.meta


def test_cleanup_traces_on_success_keeps_on_failure(tmp_path):
    brr_dir = tmp_path / ".brr"
    runs_dir = brr_dir / "runs"
    runs_dir.mkdir(parents=True)
    trace = _seed_trace_dir(brr_dir, "traces/daemon-run/evt-2-attempt-1")
    for status in ("error", "conflict"):
        task = Run(id=f"task-{status}", event_id="evt-2", body="x", status=status)
        task.meta["trace_dirs"] = "traces/daemon-run/evt-2-attempt-1"
        task.save(runs_dir)

        daemon._cleanup_traces_on_success(brr_dir, runs_dir, task)

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
        return Run(
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
    task = Run(
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
        return Run(
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


# ── §8 re-alignment: success-signal axis on _result_satisfied_delivery ──


def _result(ok=True, has_response=False, missing=()):
    """Tiny stand-in for runner.RunnerResult covering the fields read by
    ``_result_satisfied_delivery``."""
    class _R:
        pass
    r = _R()
    r.ok = ok
    r.has_response = has_response
    r.missing_artifacts = list(missing)
    return r


def test_result_satisfied_delivery_picks_current_reply_signal():
    """A stdout reply on the current thread is one satisfying signal; it
    wins over commit/outbound and identifies as ``current_reply``."""
    event = {"source": "telegram"}
    stats = {"current": 1, "other": 0, "outbound": 0}
    ok, signal = daemon._result_satisfied_delivery(
        _result(has_response=True), stats, event,
    )
    assert ok is True
    assert signal == "current_reply"


def test_result_satisfied_delivery_picks_outbox_current_reply_signal():
    """An outbox-only current-thread interim counts as success even
    without stdout. Preserves the existing shipped behavior, now with
    the named signal so the card can reflect it."""
    event = {"source": "telegram"}
    stats = {"current": 1, "other": 0, "outbound": 0}
    ok, signal = daemon._result_satisfied_delivery(_result(), stats, event)
    assert ok is True
    assert signal == "current_reply"


def test_result_satisfied_delivery_recognises_other_thread_reply():
    """A folded-in reply to a sibling event (no current-thread reply)
    is a successful delivery — §6 says events go to threads, not stdout.
    Previously this read as a silent drop."""
    event = {"source": "telegram"}
    stats = {"current": 0, "other": 1, "outbound": 0}
    ok, signal = daemon._result_satisfied_delivery(_result(), stats, event)
    assert ok is True
    assert signal == "other_reply"


def test_result_satisfied_delivery_recognises_outbound_gate_send():
    """A `gate:` out-of-bound message is a delivery event — a co-maintainer
    that pinged a forge or chat from a scheduled wake didn't fail just
    because the current thread had no reply."""
    event = {"source": "schedule"}
    stats = {"current": 0, "other": 0, "outbound": 1}
    ok, signal = daemon._result_satisfied_delivery(_result(), stats, event)
    assert ok is True
    assert signal == "outbound"


def test_result_satisfied_delivery_recognises_respawn_signal():
    """A parked respawn is an explicit success signal: the current run handed the
    work to a new Shell/Core instead of silently producing no output."""
    event = {"source": "telegram"}
    stats = {"current": 0, "other": 0, "outbound": 0, "respawn": 1}
    ok, signal = daemon._result_satisfied_delivery(_result(), stats, event)
    assert ok is True
    assert signal == "respawn"


def test_result_satisfied_delivery_recognises_commit_signal():
    """A run that committed new work on the worktree branch is a
    successful run, even without any reply event — §6's commit signal."""
    event = {"source": "telegram"}
    stats = {"current": 0, "other": 0, "outbound": 0}
    ok, signal = daemon._result_satisfied_delivery(
        _result(), stats, event, has_new_commit=True,
    )
    assert ok is True
    assert signal == "commit"


def test_result_satisfied_delivery_internal_event_passes_without_reply():
    """Internal-source events (schedule fires) have no user thread to
    close, so a clean exit with no signal still resolves as ``internal``
    success. Preserves the shipped behavior with the named signal."""
    event = {"source": "schedule"}
    stats = {"current": 0, "other": 0, "outbound": 0}
    ok, signal = daemon._result_satisfied_delivery(_result(), stats, event)
    assert ok is True
    assert signal == "internal"


def test_result_satisfied_delivery_user_event_without_signal_fails():
    """The §6 invariant: silence on a user-addressed event is failure.
    No reply, no commit, no internal-event exemption → satisfied=False
    so the failure-path writes a terminal note instead of swallowing
    the request."""
    event = {"source": "telegram"}
    stats = {"current": 0, "other": 0, "outbound": 0}
    ok, signal = daemon._result_satisfied_delivery(_result(), stats, event)
    assert ok is False
    assert signal == ""


def test_result_satisfied_delivery_runner_error_fails():
    """A runner.ok=False result is operational failure regardless of
    any output stats — those didn't come from the failed attempt."""
    event = {"source": "telegram"}
    stats = {"current": 5, "other": 5, "outbound": 5}
    ok, signal = daemon._result_satisfied_delivery(
        _result(ok=False), stats, event, has_new_commit=True,
    )
    assert ok is False
    assert signal == ""


def test_result_satisfied_delivery_missing_artifact_fails():
    """A missing required artifact means the runner didn't validate —
    treat as failure even if other output paths fired."""
    event = {"source": "telegram"}
    stats = {"current": 1, "other": 0, "outbound": 0}
    ok, signal = daemon._result_satisfied_delivery(
        _result(missing=["foo"]), stats, event,
    )
    assert ok is False
    assert signal == ""


def test_run_worker_writes_prompt_to_run_dir(tmp_path, monkeypatch):
    """The daemon persists the assembled prompt in .brr/runs/<run-id>/prompt.md.

    On successful runs the trace directories are cleaned up, but the run dir
    is not, so prompt.md survives — giving a faithful "what did this wake
    see?" answer.
    """
    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-prompt")
    worktree_path, _finalized = _stub_env_isolated(monkeypatch, tmp_path)

    monkeypatch.setattr(daemon.runner, "resolve_runner", lambda _root: "codex")
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")

    captured_prompts: list[str] = []

    def fake_build_prompt(task, eid, rp, root, **kw):
        p = f"PROMPT run={kw.get('run_id')} evt={eid}"
        captured_prompts.append(p)
        return p

    monkeypatch.setattr(daemon.prompts, "build_daemon_prompt", fake_build_prompt)

    base_env = envs.get_env("worktree")

    def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
        Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
        Path(invocation.response_path).write_text("done\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation,
            runner_name=runner_name,
            command=["mock"],
            stdout="done\n",
            stderr="",
            returncode=0,
            trace_dir=None,
            artifacts=[],
        )

    monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

    task = daemon._run_worker(event, tmp_path, tmp_path / ".brr" / "responses", {}, 0)

    assert task.status == "done"
    prompt_path = tmp_path / ".brr" / "runs" / task.id / "prompt.md"
    assert prompt_path.exists(), f"prompt.md not found at {prompt_path}"
    content = prompt_path.read_text(encoding="utf-8")
    # The first attempt's prompt (not a retry prompt) is what's persisted.
    assert "evt=evt-prompt" in content


# ── _scm_facet (portal-state SCM posture) ────────────────────────────


def test_scm_facet_unknown_without_workdir():
    # No readable worktree → known=False so the back channel stays silent
    # rather than claim a clean tree it never inspected.
    facet = daemon._scm_facet(None, "brr/run-x")
    assert facet == {
        "known": False, "branch": "brr/run-x",
        "unpushed_commits": 0, "modified_files": 0,
    }


def test_scm_facet_reports_dirty_unpushed_tree(tmp_path):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_files(repo, {"a.txt": "x\n"})  # no remote → 1 unpushed
    (repo / "b.txt").write_text("dirty\n", encoding="utf-8")  # 1 untracked
    facet = daemon._scm_facet(repo, "brr/run-x")
    assert facet["known"] is True
    assert facet["branch"] == "brr/run-x"
    assert facet["unpushed_commits"] == 1
    assert facet["modified_files"] == 1


# ── _resources_facet (portal-state work-status posture) ──────────────


def test_resources_facet_quota_known_when_summary_present():
    facet = daemon._resources_facet("weekly 42% - resets 3d")
    assert facet["quota"]["status"] == "known"
    assert facet["quota"]["summary"] == "weekly 42% - resets 3d"
    # The level facets with no collector wired for this medium advertise
    # themselves as unimplemented and whether they are required, so a future
    # wake sees the slot and its weight.
    assert facet["spend"]["status"] == "unimplemented"
    assert facet["spend"]["required"] is True
    assert facet["context_window"]["status"] == "unimplemented"
    assert facet["context_window"]["required"] is True
    assert facet["coexisting_runs"]["status"] == "unimplemented"
    assert facet["coexisting_runs"]["required"] is False


def test_resources_facet_level_collector_flips_empty_to_absent():
    # With a level collector wired (for example Claude result JSON), an empty spend /
    # context-window slot is affirmative-'absent', not unbuilt 'unimplemented'.
    facet = daemon._resources_facet(None, levels_collector=True)
    assert facet["spend"]["status"] == "absent"
    assert facet["context_window"]["status"] == "absent"
    # A populated level snapshot reads 'known' and carries its summary.
    facet = daemon._resources_facet(
        None,
        levels_collector=True,
        levels={
            "spend": {"summary": "$0.42 this session"},
            "context_window": {"summary": "62% context left"},
            "quota": {"summary": "5h 58% left"},
        },
    )
    assert facet["spend"]["status"] == "known"
    assert facet["spend"]["summary"] == "$0.42 this session"
    assert facet["context_window"]["status"] == "known"
    # A level-source quota wins over the local snapshot path.
    assert facet["quota"]["status"] == "known"
    assert facet["quota"]["summary"] == "5h 58% left"


def test_resources_facet_quota_absent_without_summary():
    # Quota's collector exists but proved nothing for this medium: that is an
    # affirmative-empty 'absent', not an unbuilt 'unimplemented'.
    facet = daemon._resources_facet(None)
    assert facet["quota"]["status"] == "absent"
    assert facet["quota"]["summary"] is None
    assert facet["quota"]["note"]
    facet_blank = daemon._resources_facet("   ")
    assert facet_blank["quota"]["status"] == "absent"


def test_resources_facet_remote_scm_pr_not_created_is_absent():
    facet = daemon._resources_facet(None, branch="brr/feature")
    assert facet["remote_scm"]["status"] == "absent"
    assert facet["remote_scm"]["pr_state"] == "none"
    assert facet["remote_scm"]["branch"] == "brr/feature"
    assert facet["remote_scm"]["pr_number"] is None
    assert "no PR" in facet["remote_scm"]["note"]


def test_resources_facet_remote_scm_known_when_pr_recorded():
    facet = daemon._resources_facet(None, branch="brr/feature", pr_number="207")
    assert facet["remote_scm"]["status"] == "known"
    assert facet["remote_scm"]["pr_state"] == "open"
    assert facet["remote_scm"]["pr_number"] == "207"
    assert facet["remote_scm"]["note"] is None


def test_resources_facet_threads_runner_catalog():
    facet = daemon._resources_facet(
        None,
        runner_name="codex-mini",
        runner_catalog=[
            {
                "name": "codex-mini",
                "shell": "codex",
                "model": "gpt-5.4-mini",
                "selected": True,
                "availability": "available",
            }
        ],
    )

    catalog = facet["runner"]["catalog"]
    assert catalog[0]["name"] == "codex-mini"
    assert catalog[0]["selected"] is True


def test_repo_label_prefers_event_repo():
    label = daemon._repo_label(
        Path("/tmp/local-brr"),
        {"github_repo": "Gurio/brr"},
        {},
    )

    assert label == "Gurio/brr"


def test_repo_label_falls_back_to_remote(monkeypatch, tmp_path):
    monkeypatch.setattr(daemon.gitops, "default_remote", lambda _root: "origin")
    monkeypatch.setattr(
        daemon.gitops,
        "remote_url",
        lambda _root, _remote: "git@github.com:Gurio/brr.git",
    )

    assert daemon._repo_label(tmp_path, {}, {}) == "Gurio/brr"


def test_repo_label_uses_config_before_directory_name(tmp_path):
    assert daemon._repo_label(tmp_path, {}, {"repo.label": "local/demo"}) == "local/demo"


def test_collect_levels_for_claude_merges_usage_and_result(monkeypatch, tmp_path):
    monkeypatch.setattr(
        daemon.claude_usage,
        "load_or_refresh_snapshot",
        lambda outbox, cwd=None: {
            "source": "claude /usage PTY",
            "quota": {"summary": "session 100% left; week 55% left"},
        },
    )
    monkeypatch.setattr(
        daemon.claude_status,
        "load_snapshot",
        lambda outbox: {
            "source": "claude result JSON",
            "spend": {"summary": "$0.0100 this session"},
            "context_window": {"summary": "95% context left (est)"},
        },
    )

    levels, slots = daemon._collect_levels("claude", tmp_path, tmp_path)

    assert slots == {"quota", "spend", "context_window"}
    assert levels["quota"]["summary"] == "session 100% left; week 55% left"
    assert levels["spend"]["summary"] == "$0.0100 this session"
    assert levels["context_window"]["summary"] == "95% context left (est)"
    assert levels["source"] == "claude /usage PTY + claude result JSON"

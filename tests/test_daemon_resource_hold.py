"""Resource hold, exercised through the production worker (``_run_worker``/
``_run_worker_and_finalize``) and the main-loop dispatch filter.

Acceptance bar (design-the-allowance.md / the maintainer's own spec for this
slice): a confident, structured quota-exhaustion signal parks a run instead
of retrying/falling back/erroring; a routine child/schedule event for a held
conversation accumulates without a model invocation and without event loss;
an explicit resume consumes the hold exactly once; a measured reset only
releases a hold that chose that condition; a restart-surviving status
excludes it from every boot janitor; cancellation still works. Fake runners
throughout — no real quota is ever touched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brr import daemon, envs, protocol, resource_hold
from brr.run import Run
from brr.runner import RunnerResult

from _helpers import make_event, write_repo_scaffold


@pytest.fixture(autouse=True)
def _clean_run_controls():
    with daemon._run_controls_lock:
        daemon._run_controls.clear()
    yield
    with daemon._run_controls_lock:
        daemon._run_controls.clear()


def _stub_env_isolated(monkeypatch, tmp_path):
    worktree_path = tmp_path / ".brr" / "worktrees" / "stub"
    worktree_path.mkdir(parents=True, exist_ok=True)

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
            return task

    monkeypatch.setattr(envs, "get_env", lambda _name: StubEnv())
    return worktree_path


def _wire_common(monkeypatch):
    monkeypatch.setattr(
        daemon.runner, "resolve_runner_profile",
        lambda _root, _overrides=None: daemon.runner.runner_profile("codex", _root),
    )
    monkeypatch.setattr(daemon.gitops, "current_branch", lambda _root: "main")
    monkeypatch.setattr(
        daemon.prompts, "build_daemon_prompt",
        lambda task, eid, rp, root, **kw: "PROMPT",
    )
    monkeypatch.setattr(daemon, "publish", lambda *_a, **_k: None)


def _usage_limit_result(invocation, runner_name) -> RunnerResult:
    return RunnerResult(
        invocation=invocation, runner_name=runner_name, command=["mock"],
        stdout="", stderr="codex task_complete error (usage limit exceeded): out of quota",
        returncode=1, trace_dir=None, artifacts=[],
        codex_task_error={"kind": "usage_limit_exceeded", "message": "out of quota"},
    )


class TestArmOnConfidentUsageLimitError:
    def test_arms_hold_instead_of_error(self, tmp_path, monkeypatch):
        write_repo_scaffold(tmp_path)
        event = make_event(tmp_path, eid="evt-quota")
        _stub_env_isolated(monkeypatch, tmp_path)
        _wire_common(monkeypatch)
        base_env = envs.get_env("worktree")
        calls = []

        def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
            calls.append(invocation)
            return _usage_limit_result(invocation, runner_name)

        monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

        task = daemon._run_worker_and_finalize(
            event, tmp_path, tmp_path / ".brr" / "responses", {}, 3,
        )

        # No retry, no fallback attempt — one invocation only.
        assert len(calls) == 1
        assert task.status == resource_hold.RUN_STATUS
        hold = task.meta["resource_hold"]
        assert hold["reason"] == resource_hold.REASON_QUOTA_EXHAUSTED
        assert hold["provider"] == "codex"
        assert hold["resume_condition"] == resource_hold.RESUME_OPERATOR
        assert hold["released"] is False
        # The letter's own lifecycle settles at "done" either way
        # (design-the-post.md), with the run's real outcome in its own key.
        assert event.get("status") == "done"
        assert event.get("run_outcome") == resource_hold.RUN_STATUS
        response = protocol.read_response(tmp_path / ".brr" / "responses", "evt-quota")
        assert response is not None
        assert "Parking this conversation" in response

    def test_persisted_manifest_carries_the_hold(self, tmp_path, monkeypatch):
        write_repo_scaffold(tmp_path)
        event = make_event(tmp_path, eid="evt-quota-2")
        _stub_env_isolated(monkeypatch, tmp_path)
        _wire_common(monkeypatch)
        base_env = envs.get_env("worktree")

        def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
            return _usage_limit_result(invocation, runner_name)

        monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

        task = daemon._run_worker_and_finalize(
            event, tmp_path, tmp_path / ".brr" / "responses", {}, 3,
        )

        persisted = Run.from_file(tmp_path / ".brr" / "runs" / task.id / "run.md")
        assert persisted is not None
        assert persisted.status == "held"
        assert persisted.meta["resource_hold"]["reason"] == resource_hold.REASON_QUOTA_EXHAUSTED

    def test_strand_run_falls_through_to_ordinary_failure(self, tmp_path, monkeypatch):
        """A strand's own allowance/ask-park contract owns its lifecycle —
        the automatic hold detection must never fire for one."""
        write_repo_scaffold(tmp_path)
        event = make_event(
            tmp_path, eid="evt-quota-strand",
            spawn_parent_run_id="run-parent-xyz",
        )
        _stub_env_isolated(monkeypatch, tmp_path)
        _wire_common(monkeypatch)
        base_env = envs.get_env("worktree")

        def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
            return _usage_limit_result(invocation, runner_name)

        monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

        task = daemon._run_worker_and_finalize(
            event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
        )

        assert task.status == "error"
        assert "resource_hold" not in task.meta

    def test_unrelated_error_retains_ordinary_failure_path(self, tmp_path, monkeypatch):
        """A regex-shaped quota guess with no structured codex_task_error
        ("unrelated error retains failure") must not arm a hold."""
        write_repo_scaffold(tmp_path)
        event = make_event(tmp_path, eid="evt-plain-fail")
        _stub_env_isolated(monkeypatch, tmp_path)
        _wire_common(monkeypatch)
        base_env = envs.get_env("worktree")

        def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
            return RunnerResult(
                invocation=invocation, runner_name=runner_name, command=["mock"],
                stdout="", stderr="connection dropped", returncode=1,
                trace_dir=None, artifacts=[],
            )

        monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

        task = daemon._run_worker_and_finalize(
            event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
        )

        assert task.status == "error"
        assert "resource_hold" not in task.meta

    def test_confirmed_hold_survives_an_active_user_stop_untouched(self, tmp_path, monkeypatch):
        """Cancellation still works: a run already stopped takes the stop
        path, never the hold path, even if its last attempt happens to
        carry a usage_limit_exceeded result."""
        write_repo_scaffold(tmp_path)
        event = make_event(tmp_path, eid="evt-stop-quota")
        _stub_env_isolated(monkeypatch, tmp_path)
        _wire_common(monkeypatch)
        base_env = envs.get_env("worktree")

        def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
            with daemon._run_controls_lock:
                daemon._run_controls[invocation.label.rsplit("-attempt-", 1)[0]] = {
                    "stopped": True, "stopped_by": "operator",
                }
            return _usage_limit_result(invocation, runner_name)

        monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)
        monkeypatch.setattr(daemon, "_capture_worktree", lambda *_a, **_k: None)

        task = daemon._run_worker_and_finalize(
            event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
        )

        assert task.status == "stopped"
        assert "resource_hold" not in task.meta


class TestResidentHoldDirective:
    def test_hold_true_on_a_clean_turn_parks_instead_of_done(self, tmp_path, monkeypatch):
        write_repo_scaffold(tmp_path)
        event = make_event(tmp_path, eid="evt-hold-verb")
        _stub_env_isolated(monkeypatch, tmp_path)
        _wire_common(monkeypatch)
        base_env = envs.get_env("worktree")

        def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
            outbox_dir = Path(invocation.env["BRR_OUTBOX_DIR"])
            outbox_dir.mkdir(parents=True, exist_ok=True)
            (outbox_dir / "0001-hold.md").write_text(
                "---\nhold: true\nreason: near weekly quota\n---\n"
                "Parking here for now.\n",
                encoding="utf-8",
            )
            Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
            Path(invocation.response_path).write_text(
                "Parking here for now.\n", encoding="utf-8",
            )
            return RunnerResult(
                invocation=invocation, runner_name=runner_name, command=["mock"],
                stdout="Parking here for now.\n", stderr="", returncode=0,
                trace_dir=None, artifacts=[],
            )

        monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

        task = daemon._run_worker_and_finalize(
            event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
        )

        assert task.status == resource_hold.RUN_STATUS
        hold = task.meta["resource_hold"]
        assert hold["reason"] == "near weekly quota"
        assert hold["resume_condition"] == resource_hold.RESUME_OPERATOR


class TestHeldRunsForRepo:
    def _held_run(self, runs_dir: Path, run_id: str, **hold_overrides) -> Run:
        meta = resource_hold.build(reason="x", provider="codex")
        meta.update(hold_overrides)
        task = Run(id=run_id, event_id="evt-1", body="", status=resource_hold.RUN_STATUS)
        task.meta["resource_hold"] = meta
        task.save(runs_dir)
        return task

    def test_finds_only_active_holds(self, tmp_path):
        runs_dir = tmp_path / ".brr" / "runs"
        runs_dir.mkdir(parents=True)
        self._held_run(runs_dir, "run-active")
        released = resource_hold.mark_released(
            resource_hold.build(reason="x", provider="codex"), by="operator",
        )
        stale = Run(id="run-released", event_id="evt-2", body="", status=resource_hold.RUN_STATUS)
        stale.meta["resource_hold"] = released
        stale.save(runs_dir)

        held = daemon._held_runs_for_repo(runs_dir)

        assert [r.id for r in held] == ["run-active"]

    def test_empty_when_no_holds(self, tmp_path):
        runs_dir = tmp_path / ".brr" / "runs"
        runs_dir.mkdir(parents=True)
        assert daemon._held_runs_for_repo(runs_dir) == []


class TestHandleResourceHeldEvents:
    def _target(self, tmp_path, *, source: str, eid: str) -> "daemon._DispatchTarget":
        inbox_dir = tmp_path / ".brr" / "inbox"
        inbox_dir.mkdir(parents=True, exist_ok=True)
        path = inbox_dir / f"{eid}.md"
        path.write_text(
            f"---\nid: {eid}\nsource: {source}\nstatus: pending\n---\nbody\n",
            encoding="utf-8",
        )
        event = protocol._read_event(path)
        return daemon._DispatchTarget(
            event=event, repo_root=tmp_path, inbox_dir=inbox_dir,
            responses_dir=tmp_path / ".brr" / "responses", repo_label="home",
        )

    def _arm_held_run(self, tmp_path, **hold_overrides) -> Run:
        runs_dir = tmp_path / ".brr" / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        meta = resource_hold.build(
            reason=resource_hold.REASON_QUOTA_EXHAUSTED, provider="codex",
            native_session_id="held-thread-1",
            resume_kind=resource_hold.RESUME_NATIVE,
        )
        meta.update(hold_overrides)
        task = Run(id="run-held-1", event_id="evt-lead", body="", status=resource_hold.RUN_STATUS)
        task.meta["resource_hold"] = meta
        task.save(runs_dir)
        return task

    def test_no_active_hold_passes_everything_through(self, tmp_path):
        targets = [self._target(tmp_path, source="telegram", eid="evt-a")]
        result = daemon._handle_resource_held_events(targets, None)
        assert result == targets

    def test_schedule_event_is_deferred_and_accumulated(self, tmp_path):
        held = self._arm_held_run(tmp_path)
        target = self._target(tmp_path, source="schedule", eid="evt-sched")

        survivors = daemon._handle_resource_held_events([target], None)

        assert survivors == []
        reread = protocol._read_event(target.inbox_dir / "evt-sched.md")
        assert reread.get("defer_reason") == "resource_hold"
        assert reread.get("deferred_by_run") == held.id
        persisted = Run.from_file(tmp_path / ".brr" / "runs" / held.id / "run.md")
        assert "evt-sched" in persisted.meta["resource_hold"]["accumulated_event_ids"]

    def test_spawn_completed_event_is_deferred_and_accumulated(self, tmp_path):
        self._arm_held_run(tmp_path)
        target = self._target(tmp_path, source="spawn_completed", eid="evt-child")

        survivors = daemon._handle_resource_held_events([target], None)

        assert survivors == []
        reread = protocol._read_event(target.inbox_dir / "evt-child.md")
        assert reread.get("defer_reason") == "resource_hold"

    def test_correspondent_message_passes_through_and_releases(self, tmp_path):
        self._arm_held_run(tmp_path)
        target = self._target(tmp_path, source="telegram", eid="evt-human")

        survivors = daemon._handle_resource_held_events([target], None)

        assert len(survivors) == 1
        assert survivors[0] is target
        assert target.event["resume_native_session_id"] == "held-thread-1"
        assert target.event["resume_native_provider"] == "codex"
        persisted = Run.from_file(tmp_path / ".brr" / "runs" / "run-held-1" / "run.md")
        assert persisted.meta["resource_hold"]["released"] is True
        assert persisted.meta["resource_hold"]["released_by"] == "operator"

    def test_resume_consumes_the_hold_exactly_once(self, tmp_path):
        self._arm_held_run(tmp_path)
        first = self._target(tmp_path, source="telegram", eid="evt-human-1")
        second = self._target(tmp_path, source="telegram", eid="evt-human-2")

        survivors = daemon._handle_resource_held_events([first, second], None)

        # Both pass through untouched by the *accumulate* branch (neither is
        # a routine source) — but only the read of an already-released hold
        # matters for "consumed once"; assert the second call is a no-op on
        # an already-released record rather than re-releasing/re-stamping.
        assert len(survivors) == 2
        persisted = Run.from_file(tmp_path / ".brr" / "runs" / "run-held-1" / "run.md")
        assert persisted.meta["resource_hold"]["released"] is True

    def test_accumulated_siblings_are_undeferred_on_resume(self, tmp_path):
        self._arm_held_run(tmp_path)
        sched_target = self._target(tmp_path, source="schedule", eid="evt-sched-2")
        daemon._handle_resource_held_events([sched_target], None)
        human_target = self._target(tmp_path, source="telegram", eid="evt-human-3")

        daemon._handle_resource_held_events([human_target], None)

        reread = protocol._read_event(sched_target.inbox_dir / "evt-sched-2.md")
        assert reread.get("defer_until") is None
        assert reread.get("defer_reason") is None
        assert reread.get("resume_native_session_id") == "held-thread-1"

    def test_operator_condition_is_never_released_by_reset_check(self, tmp_path):
        held = self._arm_held_run(
            tmp_path, resume_condition=resource_hold.RESUME_OPERATOR,
        )
        released = daemon._release_reset_holds_due(None, tmp_path)
        assert released == 0
        persisted = Run.from_file(
            tmp_path / ".brr" / "runs" / held.id / "run.md",
        )
        assert persisted.meta["resource_hold"]["released"] is False

    def test_reset_condition_releases_once_deadline_passes(self, tmp_path, monkeypatch):
        held = self._arm_held_run(
            tmp_path,
            resume_condition=resource_hold.RESUME_RESET,
            reset_deadline=1000.0,
        )
        sched_target = self._target(tmp_path, source="schedule", eid="evt-sched-3")
        daemon._handle_resource_held_events([sched_target], None)
        monkeypatch.setattr(daemon.time, "time", lambda: 2000.0)

        released = daemon._release_reset_holds_due(None, tmp_path)

        assert released == 1
        persisted = Run.from_file(
            tmp_path / ".brr" / "runs" / held.id / "run.md",
        )
        assert persisted.meta["resource_hold"]["released"] is True
        assert persisted.meta["resource_hold"]["released_by"] == "reset"
        inbox_dir = tmp_path / ".brr" / "inbox"
        reread = protocol._read_event(inbox_dir / "evt-sched-3.md")
        assert reread.get("defer_until") is None
        assert reread.get("resume_native_session_id") == "held-thread-1"

    def test_reset_condition_before_deadline_does_not_release(self, tmp_path, monkeypatch):
        self._arm_held_run(
            tmp_path,
            resume_condition=resource_hold.RESUME_RESET,
            reset_deadline=1000.0,
        )
        monkeypatch.setattr(daemon.time, "time", lambda: 500.0)

        released = daemon._release_reset_holds_due(None, tmp_path)

        assert released == 0

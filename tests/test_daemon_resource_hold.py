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

    def test_siblings_pending_at_arm_time_are_accumulated_not_lost(
        self, tmp_path, monkeypatch,
    ):
        """Parent review, defect A (severe): a sibling event already
        pending when the hold arms is deferred by
        `_defer_pending_siblings_after_failure`, but until this fix its id
        never reached `accumulated_event_ids` — only events arriving
        *after* arming flowed through `_handle_resource_held_events`'s own
        accumulation. In the real incident three events were pending when
        the seat died; on resume they would have stayed deferred for the
        full five-year horizon. Drives the real `_run_worker_and_finalize`
        with two siblings already pending, then a real resume through
        `_handle_resource_held_events`, and asserts both come all the way
        back to ordinary pending eligibility."""
        write_repo_scaffold(tmp_path)
        event = make_event(tmp_path, eid="evt-quota-lead")
        sibling_1 = make_event(tmp_path, eid="evt-sibling-1", body="already pending 1")
        sibling_2 = make_event(tmp_path, eid="evt-sibling-2", body="already pending 2")
        _stub_env_isolated(monkeypatch, tmp_path)
        _wire_common(monkeypatch)
        base_env = envs.get_env("worktree")

        def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
            return _usage_limit_result(invocation, runner_name)

        monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

        task = daemon._run_worker_and_finalize(
            event, tmp_path, tmp_path / ".brr" / "responses", {}, 3,
        )
        assert task.status == resource_hold.RUN_STATUS

        inbox_dir = tmp_path / ".brr" / "inbox"
        persisted = Run.from_file(tmp_path / ".brr" / "runs" / task.id / "run.md")
        accumulated = persisted.meta["resource_hold"]["accumulated_event_ids"]
        assert set(accumulated) == {"evt-sibling-1", "evt-sibling-2"}
        for sib_id in ("evt-sibling-1", "evt-sibling-2"):
            reread = protocol._read_event(inbox_dir / f"{sib_id}.md")
            assert reread.get("defer_reason") == "resource_hold"
            assert reread.get("defer_until") is not None

        # A correspondent message now resumes the hold — both siblings
        # must come all the way back to ordinary pending eligibility, not
        # stay parked for the remaining ~5 years.
        resume_event = make_event(tmp_path, eid="evt-resume", source="telegram")
        resume_target = daemon._DispatchTarget(
            event=resume_event, repo_root=tmp_path, inbox_dir=inbox_dir,
            responses_dir=tmp_path / ".brr" / "responses", repo_label="home",
        )
        daemon._handle_resource_held_events([resume_target], None)

        for sib_id in ("evt-sibling-1", "evt-sibling-2"):
            reread = protocol._read_event(inbox_dir / f"{sib_id}.md")
            assert reread.get("defer_until") is None
            assert reread.get("defer_reason") is None
            assert reread.get("resume_native_session_id") is None  # no native session here

    def test_resume_only_releases_a_matching_conversation_key(
        self, tmp_path, monkeypatch,
    ):
        """Parent review, defect B (medium): a correspondent event from a
        *different* conversation than the one the hold belongs to must not
        release it or steal its native-resume stamp — "the slot is free,
        the quota is theirs to risk"."""
        write_repo_scaffold(tmp_path)
        event = make_event(
            tmp_path, eid="evt-quota-conv", conversation_key="telegram:1:",
        )
        # Run.from_event copies conversation_key from the event dict.
        event_path = tmp_path / ".brr" / "inbox" / "evt-quota-conv.md"
        event_path.write_text(
            "---\nid: evt-quota-conv\nstatus: pending\nsource: telegram\n"
            "trust_tier: owner\nconversation_key: telegram:1:\n---\nraw event body\n",
            encoding="utf-8",
        )
        event = protocol._read_event(event_path)
        _stub_env_isolated(monkeypatch, tmp_path)
        _wire_common(monkeypatch)
        base_env = envs.get_env("worktree")

        def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
            return _usage_limit_result(invocation, runner_name)

        monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

        task = daemon._run_worker_and_finalize(
            event, tmp_path, tmp_path / ".brr" / "responses", {}, 3,
        )
        assert task.status == resource_hold.RUN_STATUS
        assert task.conversation_key == "telegram:1:"

        inbox_dir = tmp_path / ".brr" / "inbox"
        unrelated_event = make_event(
            tmp_path, eid="evt-github-unrelated", source="github",
            conversation_key="github:issue:42:",
        )
        unrelated_path = inbox_dir / "evt-github-unrelated.md"
        unrelated_path.write_text(
            "---\nid: evt-github-unrelated\nstatus: pending\nsource: github\n"
            "conversation_key: github:issue:42:\n---\nunrelated\n",
            encoding="utf-8",
        )
        unrelated_event = protocol._read_event(unrelated_path)
        target = daemon._DispatchTarget(
            event=unrelated_event, repo_root=tmp_path, inbox_dir=inbox_dir,
            responses_dir=tmp_path / ".brr" / "responses", repo_label="home",
        )

        survivors = daemon._handle_resource_held_events([target], None)

        assert len(survivors) == 1
        assert "resume_native_session_id" not in survivors[0].event
        persisted = Run.from_file(tmp_path / ".brr" / "runs" / task.id / "run.md")
        assert persisted.meta["resource_hold"]["released"] is False

        # Now the matching-conversation message: this one does release it.
        matching_path = inbox_dir / "evt-telegram-followup.md"
        matching_path.write_text(
            "---\nid: evt-telegram-followup\nstatus: pending\nsource: telegram\n"
            "conversation_key: telegram:1:\n---\nfollow up\n",
            encoding="utf-8",
        )
        matching_event = protocol._read_event(matching_path)
        matching_target = daemon._DispatchTarget(
            event=matching_event, repo_root=tmp_path, inbox_dir=inbox_dir,
            responses_dir=tmp_path / ".brr" / "responses", repo_label="home",
        )
        daemon._handle_resource_held_events([matching_target], None)
        persisted = Run.from_file(tmp_path / ".brr" / "runs" / task.id / "run.md")
        assert persisted.meta["resource_hold"]["released"] is True

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

    def test_telegram_event_without_raw_key_still_resumes_its_own_hold(self, tmp_path):
        """A real cloud/Telegram event carries no `conversation_key` field —
        its key is derived from `cloud_platform` + `cloud_chat_id`. The
        guard used to read the raw field, saw `""`, and passed every
        correspondent message through as "a different conversation": a
        fresh full-boot run beside a hold that stayed active
        (run-260907-2223-avku, 2026-09-07)."""
        self._arm_held_run(tmp_path, conversation_key="cloud:telegram:155783668:")
        inbox_dir = tmp_path / ".brr" / "inbox"
        inbox_dir.mkdir(parents=True, exist_ok=True)
        path = inbox_dir / "evt-tg.md"
        path.write_text(
            "---\nid: evt-tg\nsource: cloud\nstatus: pending\n"
            "cloud_platform: telegram\ncloud_chat_id: 155783668\n"
            "cloud_topic_id: \ncloud_user_id: 155783668\n---\nwhat does parked mean?\n",
            encoding="utf-8",
        )
        event = protocol._read_event(path)
        assert "conversation_key" not in event  # the production shape
        target = daemon._DispatchTarget(
            event=event, repo_root=tmp_path, inbox_dir=inbox_dir,
            responses_dir=tmp_path / ".brr" / "responses", repo_label="home",
        )

        survivors = daemon._handle_resource_held_events([target], None)

        assert len(survivors) == 1
        assert survivors[0].event["resume_native_session_id"] == "held-thread-1"
        persisted = Run.from_file(tmp_path / ".brr" / "runs" / "run-held-1" / "run.md")
        assert persisted.meta["resource_hold"]["released"] is True
        assert persisted.meta["resource_hold"]["released_by"] == "operator"

    def test_other_gate_thread_without_raw_key_does_not_release(self, tmp_path):
        """The mirror: a GitHub issue comment (also no raw key) on the same
        repo while a Telegram seat is held must still pass through untouched."""
        self._arm_held_run(tmp_path, conversation_key="cloud:telegram:155783668:")
        inbox_dir = tmp_path / ".brr" / "inbox"
        inbox_dir.mkdir(parents=True, exist_ok=True)
        path = inbox_dir / "evt-gh.md"
        path.write_text(
            "---\nid: evt-gh\nsource: github\nstatus: pending\n"
            "cloud_platform: telegram\ncloud_chat_id: 999\n---\nunrelated\n",
            encoding="utf-8",
        )
        event = protocol._read_event(path)
        target = daemon._DispatchTarget(
            event=event, repo_root=tmp_path, inbox_dir=inbox_dir,
            responses_dir=tmp_path / ".brr" / "responses", repo_label="home",
        )
        survivors = daemon._handle_resource_held_events([target], None)
        assert len(survivors) == 1
        assert "resume_native_session_id" not in survivors[0].event
        persisted = Run.from_file(tmp_path / ".brr" / "runs" / "run-held-1" / "run.md")
        assert persisted.meta["resource_hold"]["released"] is False

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


class TestApplyRunReleaseAndRespawn:
    """the-parked-seat-has-two-buttons — the dashboard-triggered release and
    respawn-on-another-core, one level below the HTTP/publish plumbing.

    Same fixture shape as ``TestHeldRunsForRepo``/``TestHandleResourceHeldEvents``
    above: a held run manifest in a tmp runs dir, an accumulated event in a
    tmp inbox dir.
    """

    def _held_run(self, runs_dir: Path, run_id: str, **hold_overrides) -> Run:
        meta = resource_hold.build(
            reason=resource_hold.REASON_QUOTA_EXHAUSTED, provider="claude",
            conversation_key="cloud:telegram:1:",
        )
        meta.update(hold_overrides)
        task = Run(
            id=run_id, event_id="evt-lead", body="carry me forward",
            status=resource_hold.RUN_STATUS, source="telegram",
            conversation_key="cloud:telegram:1:",
        )
        task.meta["resource_hold"] = meta
        task.meta["repo_label"] = "Gurio/brr"
        task.save(runs_dir)
        return task

    def _accumulated_event(self, inbox_dir: Path, eid: str) -> None:
        inbox_dir.mkdir(parents=True, exist_ok=True)
        (inbox_dir / f"{eid}.md").write_text(
            f"---\nid: {eid}\nsource: telegram\nstatus: pending\n"
            f"defer_until: 9999999999\ndeferred_by_run: run-held-1\n---\nsome message\n",
            encoding="utf-8",
        )

    def test_release_ends_the_run_and_undefers_accumulated_events(self, tmp_path):
        runs_dir = tmp_path / ".brr" / "runs"
        inbox_dir = tmp_path / ".brr" / "inbox"
        held = self._held_run(runs_dir, "run-held-1", accumulated_event_ids=["evt-side-1"])
        self._accumulated_event(inbox_dir, "evt-side-1")

        daemon._apply_run_release(runs_dir, inbox_dir, held)

        persisted = Run.from_file(runs_dir / held.id / "run.md")
        assert persisted.status == "done"
        assert persisted.meta["resource_hold"]["released"] is True
        assert persisted.meta["resource_hold"]["released_by"] == "dashboard"
        reread = protocol._read_event(inbox_dir / "evt-side-1.md")
        assert reread.get("defer_until") is None
        # A release never fabricates a native-session resume hint — this is
        # a cold start, deliberately, so it must not carry one forward.
        assert reread.get("resume_native_session_id") is None

    def test_release_is_a_no_op_on_an_already_released_hold(self, tmp_path):
        runs_dir = tmp_path / ".brr" / "runs"
        held = self._held_run(runs_dir, "run-held-2")
        held.meta["resource_hold"] = resource_hold.mark_released(
            held.meta["resource_hold"], by="operator",
        )
        held.save(runs_dir)

        daemon._apply_run_release(runs_dir, None, held)

        persisted = Run.from_file(runs_dir / held.id / "run.md")
        # Still the first releaser's attribution — a second release must not
        # overwrite an already-consumed hold's receipt.
        assert persisted.meta["resource_hold"]["released_by"] == "operator"
        assert persisted.status == resource_hold.RUN_STATUS

    def test_respawn_releases_the_hold_and_mints_an_event_on_the_same_thread(self, tmp_path):
        runs_dir = tmp_path / ".brr" / "runs"
        inbox_dir = tmp_path / ".brr" / "inbox"
        held = self._held_run(runs_dir, "run-held-3", accumulated_event_ids=["evt-side-2"])
        self._accumulated_event(inbox_dir, "evt-side-2")

        new_path = daemon._apply_run_respawn(
            runs_dir, inbox_dir, held, shell="claude", core="opus",
        )

        assert new_path is not None
        persisted = Run.from_file(runs_dir / held.id / "run.md")
        assert persisted.meta["resource_hold"]["released"] is True
        assert persisted.meta["resource_hold"]["released_by"] == "respawn"
        # The seat itself does not resume — only a fresh event does.
        assert persisted.status == resource_hold.RUN_STATUS

        minted = protocol._read_event(new_path)
        assert minted["respawned_from_event"] == "evt-lead"
        assert minted["respawned_by_run"] == "run-held-3"
        assert minted["shell"] == "claude"
        assert minted["core"] == "opus"
        assert minted["conversation_key"] == "cloud:telegram:1:"
        # A handoff, never a replay of the parked seat's original ask.
        assert "carry me forward" not in minted["body"]
        assert "Respawned from the dashboard on claude / opus" in minted["body"]
        assert "run-held-3" in minted["body"]

        reread = protocol._read_event(inbox_dir / "evt-side-2.md")
        assert reread.get("defer_until") is None

    def test_respawn_returns_none_without_an_inbox(self, tmp_path):
        runs_dir = tmp_path / ".brr" / "runs"
        held = self._held_run(runs_dir, "run-held-4")

        assert daemon._apply_run_respawn(runs_dir, None, held) is None
        persisted = Run.from_file(runs_dir / held.id / "run.md")
        # Nothing touched — the hold is still active, not silently consumed
        # by a call that could not actually mint a successor.
        assert persisted.meta["resource_hold"]["released"] is False


class TestFindHeldRun:
    def test_finds_by_id(self, tmp_path):
        runs_dir = tmp_path / ".brr" / "runs"
        meta = resource_hold.build(reason="x", provider="codex")
        task = Run(id="run-x", event_id="evt-1", body="", status=resource_hold.RUN_STATUS)
        task.meta["resource_hold"] = meta
        task.save(runs_dir)

        found = daemon._find_held_run(runs_dir, "run-x")
        assert found is not None
        assert found.id == "run-x"

    def test_none_when_absent_or_blank(self, tmp_path):
        runs_dir = tmp_path / ".brr" / "runs"
        runs_dir.mkdir(parents=True)
        assert daemon._find_held_run(runs_dir, "run-nope") is None
        assert daemon._find_held_run(runs_dir, "") is None

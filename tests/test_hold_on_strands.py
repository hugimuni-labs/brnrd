"""The seat parks on its own strands — `resume: strands` (2026-09-06).

The measured failure: a seat cut with three children still running, each
dispositioned ``handoff``; the bolt accepted it; the successor that woke on
their submits was a stranger. The maintainer's read is the rule: *there is
no reason to stop the run when there are living strands it should be
waiting on.* Two mechanisms pinned here: a hold that a child's own event
releases, and a bolt that lands ``held`` instead of ``done`` when it hands
off a live strand.
"""

from __future__ import annotations

import pytest

from pathlib import Path

from brr import cut_verb, daemon, envs, hold_verb, protocol, resource_hold
from brr.run import Run
from brr.runner import RunnerResult

from _helpers import make_event, write_repo_scaffold


@pytest.fixture(autouse=True)
def _isolated_controls(monkeypatch):
    monkeypatch.setattr(daemon, "_run_controls", {})


def _register_live_child(event_id, parent_run_id, *, child_run_id=None):
    daemon._register_run_control(event_id, parent_run_id)
    if child_run_id:
        daemon._bind_run_control(event_id, child_run_id)


# ── the verb ─────────────────────────────────────────────────────────


class TestHoldVerbStrands:
    def test_resume_strands_alias(self):
        spec, error = hold_verb.parse_hold({"hold": "true", "resume": "strands"})
        assert error is None
        assert spec["resume_condition"] == resource_hold.RESUME_STRANDS
        assert spec["reason"] == resource_hold.REASON_WAITING_ON_STRANDS

    def test_children_alias_and_explicit_reason(self):
        spec, error = hold_verb.parse_hold(
            {"hold": "true", "resume": "children", "reason": "three PRs cooking"}
        )
        assert error is None
        assert spec["resume_condition"] == resource_hold.RESUME_STRANDS
        assert spec["reason"] == "three PRs cooking"

    def test_operator_default_reason_unchanged(self):
        spec, _ = hold_verb.parse_hold({"hold": "true"})
        assert spec["reason"] == resource_hold.REASON_RESIDENT_REQUESTED

    def test_refusal_names_all_three(self):
        _, error = hold_verb.parse_hold({"hold": "true", "resume": "later"})
        assert "strands" in error and "operator" in error and "reset" in error


# ── the pure predicate ───────────────────────────────────────────────


class TestStrandEventReleases:
    def _meta(self, condition=resource_hold.RESUME_STRANDS):
        return resource_hold.build(
            reason=resource_hold.REASON_WAITING_ON_STRANDS, provider="claude",
            resume_condition=condition,
        )

    def test_own_child_submitted_releases(self):
        ev = {"source": "spawn_submitted", "spawn_parent_run_id": "run-parent"}
        assert resource_hold.strand_event_releases(self._meta(), ev, held_run_id="run-parent")

    def test_own_child_completed_and_allowance_release(self):
        for source in ("spawn_completed", "spawn_allowance_requested"):
            ev = {"source": source, "spawn_parent_run_id": "run-parent"}
            assert resource_hold.strand_event_releases(
                self._meta(), ev, held_run_id="run-parent",
            ), source

    def test_someone_elses_child_does_not(self):
        ev = {"source": "spawn_submitted", "spawn_parent_run_id": "run-other"}
        assert not resource_hold.strand_event_releases(self._meta(), ev, held_run_id="run-parent")

    def test_child_known_by_run_id_list_counts(self):
        ev = {"source": "spawn_completed", "spawned_by_run": "run-kid"}
        assert resource_hold.strand_event_releases(
            self._meta(), ev, held_run_id="run-parent", child_run_ids="run-a, run-kid",
        )
        assert resource_hold.strand_event_releases(
            self._meta(), ev, held_run_id="run-parent", child_run_ids=["run-kid"],
        )

    def test_queued_and_schedule_never_release(self):
        for source in ("spawn_queued", "schedule", "telegram"):
            ev = {"source": source, "spawn_parent_run_id": "run-parent"}
            assert not resource_hold.strand_event_releases(
                self._meta(), ev, held_run_id="run-parent",
            ), source

    def test_operator_condition_ignores_children(self):
        ev = {"source": "spawn_submitted", "spawn_parent_run_id": "run-parent"}
        assert not resource_hold.strand_event_releases(
            self._meta(resource_hold.RESUME_OPERATOR), ev, held_run_id="run-parent",
        )

    def test_released_hold_is_inert(self):
        meta = resource_hold.mark_released(self._meta(), by="strand")
        ev = {"source": "spawn_submitted", "spawn_parent_run_id": "run-parent"}
        assert not resource_hold.strand_event_releases(meta, ev, held_run_id="run-parent")

    def test_build_accepts_strands_condition(self):
        assert self._meta()["resume_condition"] == resource_hold.RESUME_STRANDS


# ── the daemon's dispatch-time interception ──────────────────────────


class TestHeldEventsOnStrands:
    def _target(self, tmp_path, *, source, eid, **extra):
        inbox_dir = tmp_path / ".brr" / "inbox"
        inbox_dir.mkdir(parents=True, exist_ok=True)
        lines = [f"id: {eid}", f"source: {source}", "status: pending"]
        lines += [f"{k}: {v}" for k, v in extra.items()]
        (inbox_dir / f"{eid}.md").write_text(
            "---\n" + "\n".join(lines) + "\n---\nbody\n", encoding="utf-8",
        )
        event = protocol._read_event(inbox_dir / f"{eid}.md")
        return daemon._DispatchTarget(
            event=event, repo_root=tmp_path, inbox_dir=inbox_dir,
            responses_dir=tmp_path / ".brr" / "responses", repo_label="home",
        )

    def _arm(self, tmp_path, *, condition=resource_hold.RESUME_STRANDS) -> Run:
        runs_dir = tmp_path / ".brr" / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        task = Run(id="run-parent", event_id="evt-lead", body="", status=resource_hold.RUN_STATUS)
        task.meta["resource_hold"] = resource_hold.build(
            reason=resource_hold.REASON_WAITING_ON_STRANDS, provider="claude",
            resume_condition=condition, conversation_key="cloud:telegram:1:",
        )
        task.meta["child_run_ids"] = "run-kid"
        task.save(runs_dir)
        return task

    def _persisted(self, tmp_path):
        return Run.from_file(tmp_path / ".brr" / "runs" / "run-parent" / "run.md")

    def test_own_strand_submit_releases_and_leads(self, tmp_path):
        self._arm(tmp_path)
        target = self._target(
            tmp_path, source="spawn_submitted", eid="evt-kid",
            spawn_parent_run_id="run-parent", conversation_key="cloud:telegram:1:",
        )

        survivors = daemon._handle_resource_held_events([target], None)

        assert survivors == [target]
        hold = self._persisted(tmp_path).meta["resource_hold"]
        assert hold["released"] is True
        assert hold["released_by"] == "strand"
        reread = protocol._read_event(target.inbox_dir / "evt-kid.md")
        assert reread.get("defer_reason") is None

    def test_reload_window_keeps_the_return_a_strands_hold_waits_for(self, tmp_path):
        # 2026-09-09, run-260909-1542-jokz: a dev-reload requested while
        # the seat was parked on its children filtered every
        # `spawned_by_run` event out of dispatch, so `resume: strands`
        # could never fire and the reload could never drain the lingering
        # strand — until a correspondent wrote 80 minutes later.
        self._arm(tmp_path)
        own = self._target(
            tmp_path, source="spawn_submitted", eid="evt-kid",
            spawn_parent_run_id="run-parent", spawned_by_run="run-kid",
            conversation_key="cloud:telegram:1:",
        )
        stranger = self._target(
            tmp_path, source="spawn_submitted", eid="evt-other",
            spawn_parent_run_id="run-elsewhere", spawned_by_run="run-x",
            conversation_key="cloud:telegram:1:",
        )
        plain = self._target(
            tmp_path, source="cloud", eid="evt-msg",
            conversation_key="cloud:telegram:1:",
        )
        assert daemon._strand_return_resumes_held_parent(own) is True
        assert daemon._strand_return_resumes_held_parent(stranger) is False
        assert daemon._strand_return_resumes_held_parent(plain) is False

    def test_reload_window_parks_a_strand_return_with_no_hold(self, tmp_path):
        target = self._target(
            tmp_path, source="spawn_submitted", eid="evt-kid",
            spawn_parent_run_id="run-parent", spawned_by_run="run-kid",
        )
        assert daemon._strand_return_resumes_held_parent(target) is False

    def test_a_strangers_strand_still_accumulates(self, tmp_path):
        # A stranger's strand on the seat's own thread is that seat's mail
        # — accumulated, never a release. On another conversation it is
        # not this seat's at all (#1890, test_seat_per_conversation.py).
        held = self._arm(tmp_path)
        target = self._target(
            tmp_path, source="spawn_submitted", eid="evt-other",
            spawn_parent_run_id="run-someone-else", conversation_key="cloud:telegram:1:",
        )

        survivors = daemon._handle_resource_held_events([target], None)

        assert survivors == []
        reread = protocol._read_event(target.inbox_dir / "evt-other.md")
        assert reread.get("defer_reason") == "resource_hold"
        assert reread.get("deferred_by_run") == held.id
        assert self._persisted(tmp_path).meta["resource_hold"]["released"] is False

    def test_operator_hold_ignores_its_own_strand(self, tmp_path):
        self._arm(tmp_path, condition=resource_hold.RESUME_OPERATOR)
        target = self._target(
            tmp_path, source="spawn_completed", eid="evt-kid",
            spawn_parent_run_id="run-parent",
        )
        assert daemon._handle_resource_held_events([target], None) == []
        assert self._persisted(tmp_path).meta["resource_hold"]["released"] is False

    def test_schedule_still_accumulates_under_a_strands_hold(self, tmp_path):
        self._arm(tmp_path)
        target = self._target(
            tmp_path, source="schedule", eid="evt-tick", conversation_key="cloud:telegram:1:",
        )
        assert daemon._handle_resource_held_events([target], None) == []

    def test_siblings_in_the_releasing_batch_pass_through(self, tmp_path):
        """A schedule tick sorted behind the releasing child event must not
        be deferred against a hold that is already released — the resuming
        dispatch reads its inbox and should see it."""
        self._arm(tmp_path)
        kid = self._target(
            tmp_path, source="spawn_submitted", eid="evt-kid",
            spawn_parent_run_id="run-parent", conversation_key="cloud:telegram:1:",
        )
        tick = self._target(
            tmp_path, source="schedule", eid="evt-tick", conversation_key="cloud:telegram:1:",
        )

        survivors = daemon._handle_resource_held_events([kid, tick], None)

        assert survivors == [kid, tick]
        reread = protocol._read_event(tick.inbox_dir / "evt-tick.md")
        assert reread.get("defer_reason") is None

    def test_correspondent_message_still_releases_a_strands_hold(self, tmp_path):
        self._arm(tmp_path)
        target = self._target(
            tmp_path, source="cloud", eid="evt-human", conversation_key="cloud:telegram:1:",
        )
        assert daemon._handle_resource_held_events([target], None) == [target]
        assert self._persisted(tmp_path).meta["resource_hold"]["released_by"] == "operator"

    def test_arm_time_sibling_from_own_child_stays_pending(self, tmp_path):
        """A child that already reported back when the hold arms is the
        release, not a sibling to brake."""
        inbox_dir = tmp_path / ".brr" / "inbox"
        kid = self._target(
            tmp_path, source="spawn_completed", eid="evt-kid-early",
            spawn_parent_run_id="run-parent",
        )
        tick = self._target(tmp_path, source="schedule", eid="evt-tick")
        meta = resource_hold.build(
            reason=resource_hold.REASON_WAITING_ON_STRANDS, provider="claude",
            resume_condition=resource_hold.RESUME_STRANDS,
        )

        deferred = daemon._defer_pending_siblings_after_failure(
            inbox_dir, lead_event_id="evt-lead", run_id="run-parent",
            seconds=3600, reason="resource_hold",
            keep_pending=lambda pending: resource_hold.strand_event_releases(
                meta, pending, held_run_id="run-parent",
            ),
        )

        assert deferred == ["evt-tick"]
        assert protocol._read_event(kid.inbox_dir / "evt-kid-early.md").get("defer_until") is None
        assert protocol._read_event(tick.inbox_dir / "evt-tick.md").get("defer_until") is not None


# ── the bolt lands held, not done ────────────────────────────────────


class TestBoltParksOnLiveStrands:
    def _declaration(self, **strands):
        return cut_verb.CutDeclaration(
            strands=tuple(
                cut_verb.StrandDisposition(run=run, disposition=disp)
                for run, disp in strands.items()
            ),
        )

    def test_live_handoff_arms_a_strands_hold(self, tmp_path):
        parent = Run(id="run-parent", event_id="evt-p", body="", env="host")
        parent.meta["runner_shell"] = "claude"
        _register_live_child("evt-kid", parent.id, child_run_id="run-kid")

        parked = daemon._park_bolt_on_live_strands(
            parent, self._declaration(**{"run-kid": "handoff — successor opens the PR"}),
            outbox_dir=tmp_path,
        )

        assert parked == ["run-kid"]
        hold = parent.meta["pending_resource_hold"]
        assert hold["resume_condition"] == resource_hold.RESUME_STRANDS
        assert hold["reason"] == resource_hold.REASON_WAITING_ON_STRANDS
        assert "run-kid" in hold["detail"]

    def test_stopped_or_converged_do_not_park(self, tmp_path):
        parent = Run(id="run-parent", event_id="evt-p", body="", env="host")
        _register_live_child("evt-kid", parent.id, child_run_id="run-kid")
        for disposition in ("stopped", "converged — diff read whole", "abandoned"):
            parent.meta.pop("pending_resource_hold", None)
            parked = daemon._park_bolt_on_live_strands(
                parent, self._declaration(**{"run-kid": disposition}), outbox_dir=tmp_path,
            )
            assert parked == [], disposition
            assert "pending_resource_hold" not in parent.meta

    def test_handoff_of_a_child_no_longer_live_is_an_ordinary_close(self, tmp_path):
        parent = Run(id="run-parent", event_id="evt-p", body="", env="host")
        _register_live_child("evt-kid", parent.id, child_run_id="run-kid")
        with daemon._run_controls_lock:
            daemon._run_controls["evt-kid"]["stopped"] = True

        parked = daemon._park_bolt_on_live_strands(
            parent, self._declaration(**{"run-kid": "handoff"}), outbox_dir=tmp_path,
        )

        assert parked == []
        assert "pending_resource_hold" not in parent.meta

    def test_an_explicit_hold_this_turn_wins(self, tmp_path):
        parent = Run(id="run-parent", event_id="evt-p", body="", env="host")
        _register_live_child("evt-kid", parent.id, child_run_id="run-kid")
        parent.meta["pending_resource_hold"] = {"resume_condition": resource_hold.RESUME_OPERATOR}

        parked = daemon._park_bolt_on_live_strands(
            parent, self._declaration(**{"run-kid": "handoff"}), outbox_dir=tmp_path,
        )

        assert parked == []
        assert parent.meta["pending_resource_hold"]["resume_condition"] == resource_hold.RESUME_OPERATOR

    def test_hold_body_names_the_strands_release(self):
        meta = resource_hold.build(
            reason=resource_hold.REASON_WAITING_ON_STRANDS, provider="claude",
            resume_condition=resource_hold.RESUME_STRANDS,
            detail="bolt dispositioned live strands handoff: run-kid",
        )
        body = daemon._hold_body(meta)
        assert "strands" in body and "run-kid" in body
        assert "resumes this conversation" in body


# ── the seat's resting state: resume: any (design-the-seat-that-never-quits.md) ──


class TestResumeAny:
    def test_verb_alias(self):
        spec, error = hold_verb.parse_hold({"hold": "true", "resume": "any"})
        assert error is None
        assert spec["resume_condition"] == resource_hold.RESUME_ANY

    def test_schedule_releases_only_under_any(self):
        conv = "cloud:telegram:1:"
        meta_any = resource_hold.build(
            reason="x", provider="claude", resume_condition=resource_hold.RESUME_ANY,
            conversation_key=conv,
        )
        meta_str = resource_hold.build(
            reason="x", provider="claude", resume_condition=resource_hold.RESUME_STRANDS,
            conversation_key=conv,
        )
        tick = {"source": "schedule", "conversation_key": conv}
        assert resource_hold.schedule_event_releases(meta_any, tick)
        assert not resource_hold.schedule_event_releases(meta_str, tick)
        assert not resource_hold.schedule_event_releases(
            meta_any, {"source": "spawn_queued", "conversation_key": conv},
        )
        # #1890: a tick on another conversation never wakes this seat.
        assert not resource_hold.schedule_event_releases(
            meta_any, {"source": "schedule", "conversation_key": "schedule:the-wire-round"},
        )

    def test_own_strand_releases_under_any(self):
        meta = resource_hold.build(reason="x", provider="claude", resume_condition=resource_hold.RESUME_ANY)
        ev = {"source": "spawn_completed", "spawn_parent_run_id": "run-parent"}
        assert resource_hold.strand_event_releases(meta, ev, held_run_id="run-parent")
        assert not resource_hold.strand_event_releases(
            meta, {"source": "spawn_completed", "spawn_parent_run_id": "run-other"}, held_run_id="run-parent",
        )

    def test_daemon_resumes_a_parked_seat_on_a_tick(self, tmp_path):
        runs_dir = tmp_path / ".brr" / "runs"; runs_dir.mkdir(parents=True)
        task = Run(id="run-seat", event_id="evt-lead", body="", status=resource_hold.RUN_STATUS)
        task.meta["resource_hold"] = resource_hold.build(
            reason=resource_hold.REASON_TURN_ENDED, provider="claude",
            resume_condition=resource_hold.RESUME_ANY, conversation_key="cloud:telegram:1:",
        )
        task.save(runs_dir)
        helper = TestHeldEventsOnStrands()
        # The tick is on the seat's own conversation (a self-wake the seat
        # scheduled on its thread) — #1890: only that tick wakes it.
        tick = helper._target(
            tmp_path, source="schedule", eid="evt-tick", conversation_key="cloud:telegram:1:",
        )

        assert daemon._handle_resource_held_events([tick], None) == [tick]
        hold = Run.from_file(runs_dir / "run-seat" / "run.md").meta["resource_hold"]
        assert hold["released"] is True and hold["released_by"] == "schedule"

    def test_park_on_turn_end_is_on_by_default_and_off_on_request(self):
        seat = Run(id="run-seat", event_id="evt-p", body="", source="cloud")
        seat.meta["runner_shell"] = "claude"
        assert daemon._park_seat_on_turn_end(seat, {}) is not None
        assert daemon._park_seat_on_turn_end(seat, None) is not None
        assert daemon._park_seat_on_turn_end(seat, {daemon.SEAT_PARK_ON_TURN_END_KEY: "false"}) is None
        assert daemon._park_seat_on_turn_end(seat, {daemon.SEAT_PARK_ON_TURN_END_KEY: False}) is None

    def test_park_on_turn_end_parks_a_seat_when_on(self):
        seat = Run(id="run-seat", event_id="evt-p", body="", source="cloud")
        seat.meta["runner_shell"] = "claude"
        hold = daemon._park_seat_on_turn_end(seat, {daemon.SEAT_PARK_ON_TURN_END_KEY: "true"})
        assert hold is not None
        assert hold["resume_condition"] == resource_hold.RESUME_ANY
        assert hold["reason"] == resource_hold.REASON_TURN_ENDED

    def test_park_on_turn_end_never_parks_a_strand(self):
        strand = Run(id="run-kid", event_id="evt-k", body="", source="spawn")
        strand.meta["strand"] = True
        assert daemon._is_strand(strand.meta)
        assert daemon._park_seat_on_turn_end(strand, {daemon.SEAT_PARK_ON_TURN_END_KEY: True}) is None

    def test_hold_body_for_any(self):
        meta = resource_hold.build(reason=resource_hold.REASON_TURN_ENDED, provider="claude", resume_condition=resource_hold.RESUME_ANY)
        body = daemon._hold_body(meta)
        assert "Parked" in body and "scheduled wake" in body


# ── the default, end to end: a clean turn end parks the seat ─────────────


def test_a_clean_turn_end_parks_the_seat_by_default(tmp_path, monkeypatch):
    """Drive the worker the way ``test_daemon_resource_hold`` does for
    ``hold:`` — no directive at all this time — and watch the seat land
    ``held`` on ``resume: any`` with nothing in config (#1817's default)."""
    from test_daemon_resource_hold import _stub_env_isolated, _wire_common

    write_repo_scaffold(tmp_path)
    event = make_event(tmp_path, eid="evt-plain-turn")
    _stub_env_isolated(monkeypatch, tmp_path)
    _wire_common(monkeypatch)
    base_env = envs.get_env("worktree")

    def fake_invoke(_self, _ctx, runner_name, invocation, cfg=None, *, trace=False):
        Path(invocation.response_path).parent.mkdir(parents=True, exist_ok=True)
        Path(invocation.response_path).write_text("done for now.\n", encoding="utf-8")
        return RunnerResult(
            invocation=invocation, runner_name=runner_name, command=["mock"],
            stdout="done for now.\n", stderr="", returncode=0,
            trace_dir=None, artifacts=[],
        )

    monkeypatch.setattr(base_env.__class__, "invoke", fake_invoke, raising=False)

    task = daemon._run_worker_and_finalize(
        event, tmp_path, tmp_path / ".brr" / "responses", {}, 0,
    )

    assert task.status == resource_hold.RUN_STATUS
    hold = task.meta["resource_hold"]
    assert hold["resume_condition"] == resource_hold.RESUME_ANY
    assert hold["reason"] == resource_hold.REASON_TURN_ENDED

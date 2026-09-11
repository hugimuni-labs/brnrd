"""The starvation park — `quota_starved` / `resume: refill` (2026-09-08).

His ask: "let you park safely … until a refill, so that a user cannot wake
you up when there is <2% of either quota available … or do a refill that a
daemon can validate, and then it unthaws you." Four seams, each driven
through the daemon function that owns it.
"""
from __future__ import annotations

import json

import pytest

from brr import daemon, protocol, resource_hold, runner_failures
from brr.gates import runtime as gate_runtime
from brr.run import Run


@pytest.fixture(autouse=True)
def _controls():
    with daemon._run_controls_lock:
        daemon._run_controls.clear()
    yield
    with daemon._run_controls_lock:
        daemon._run_controls.clear()


def _seat() -> Run:
    task = Run(id="run-seat", event_id="evt-seat", body="", env="host")
    task.meta.update({"runner_name": "claude-fable", "runner_shell": "claude", "runner_core": "fable"})
    return task


# ── the reading judged at a boundary ─────────────────────────────────


class TestStarvationFacet:
    def test_above_the_floor_only_remembers_the_reading(self):
        task = _seat()
        state, facet = daemon._starvation_facet(
            task, {"armed": True, "resolved": False}, {},
            {"binding_remaining_pct": 44.0}, None,
        )
        assert facet == {
            "binding_remaining_pct": 44.0, "starve_floor_pct": 2.0,
            "refill_floor_pct": 10.0, "starved": False,
        }
        assert task.meta["quota_binding_pct"] == 44.0
        assert "pending_resource_hold" not in task.meta
        assert state == {"armed": True, "resolved": False}

    def test_below_the_floor_stamps_a_refill_hold_and_parks_the_await(self):
        task = _seat()
        task.meta["await"] = {"armed": True, "resolved": False}
        state, facet = daemon._starvation_facet(
            task, {"armed": True, "resolved": False}, {},
            {"binding_remaining_pct": 1.4},
            {"quota": {"session_resets_at": 1789000000.0}},
        )
        hold = task.meta["pending_resource_hold"]
        assert hold["reason"] == resource_hold.REASON_QUOTA_STARVED
        assert hold["resume_condition"] == resource_hold.RESUME_REFILL
        assert hold["quota"]["binding_remaining_pct"] == 1.4
        assert hold["quota"]["refill_floor_pct"] == 10.0
        assert hold["quota"]["model"] == "fable"
        assert hold["reset_deadline"] is not None
        assert state["outcome"] == "park" and state["resolved"] is True
        assert task.meta["await"]["outcome"] == "park"
        assert facet["parking"] is True

    def test_configured_floors_and_the_thaw_never_below_the_park(self):
        task = _seat()
        cfg = {"seat.starve_floor_pct": 5, "seat.refill_floor_pct": 3}
        _state, facet = daemon._starvation_facet(
            task, None, cfg, {"binding_remaining_pct": 4.0}, None,
        )
        assert facet["starved"] is True
        assert task.meta["pending_resource_hold"]["quota"]["refill_floor_pct"] == 5.0

    def test_a_strand_is_never_parked_here(self):
        task = _seat()
        task.meta["spawn_parent_run_id"] = "run-parent"
        _state, facet = daemon._starvation_facet(
            task, None, {}, {"binding_remaining_pct": 0.5}, None,
        )
        assert facet["starved"] is True
        assert "pending_resource_hold" not in task.meta

    def test_no_reading_means_no_facet_and_no_park(self):
        task = _seat()
        state, facet = daemon._starvation_facet(task, {"armed": True}, {}, None, None)
        assert facet is None and state == {"armed": True}
        assert "pending_resource_hold" not in task.meta


# ── the Shell that dies of it ────────────────────────────────────────


class TestFailurePath:
    def test_a_stamped_starvation_hold_pre_empts_retry(self):
        task = _seat()
        task.meta["pending_resource_hold"] = daemon._starvation_hold_spec(
            task, {}, 1.0, detail="x",
        )
        spec = daemon._maybe_arm_resource_hold_on_failure(
            {"error": "boom", "failure_kind": "other"}, task=task, cfg={},
        )
        assert spec["reason"] == resource_hold.REASON_QUOTA_STARVED
        assert "pending_resource_hold" not in task.meta

    def test_quota_failure_with_a_starved_last_reading_parks_any_shell(self):
        task = _seat()
        task.meta["quota_binding_pct"] = 1.2
        spec = daemon._maybe_arm_resource_hold_on_failure(
            {"error": "usage limit", "failure_kind": runner_failures.QUOTA_EXHAUSTED},
            task=task, cfg={},
        )
        assert spec["resume_condition"] == resource_hold.RESUME_REFILL
        assert spec["provider"] == "claude"

    def test_quota_failure_with_a_healthy_last_reading_falls_through(self):
        task = _seat()
        task.meta["quota_binding_pct"] = 30.0
        assert daemon._maybe_arm_resource_hold_on_failure(
            {"error": "usage limit", "failure_kind": runner_failures.QUOTA_EXHAUSTED},
            task=task, cfg={},
        ) is None


# ── the message that cannot wake it, and the refill that does ────────


def _partials(responses_dir, event_id: str) -> str:
    """Every interim body staged for *event_id*, joined."""
    bodies = []
    for path in sorted(responses_dir.rglob("*")):
        if path.is_file() and event_id in str(path):
            bodies.append(protocol.read_partial(path) or "")
    return "\n".join(bodies)


class TestRefusedWake:
    def _target(self, tmp_path, eid: str) -> "daemon._DispatchTarget":
        inbox_dir = tmp_path / ".brr" / "inbox"
        inbox_dir.mkdir(parents=True, exist_ok=True)
        (inbox_dir / f"{eid}.md").write_text(
            f"---\nid: {eid}\nsource: telegram\nstatus: pending\n---\nhello?\n",
            encoding="utf-8",
        )
        event = protocol._read_event(inbox_dir / f"{eid}.md")
        responses = tmp_path / ".brr" / "responses"
        responses.mkdir(parents=True, exist_ok=True)
        return daemon._DispatchTarget(
            event=event, repo_root=tmp_path, inbox_dir=inbox_dir,
            responses_dir=responses, repo_label="home",
        )

    def _starved_run(self, tmp_path) -> Run:
        runs_dir = tmp_path / ".brr" / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        task = _seat()
        task.id = "run-starved"
        task.status = resource_hold.RUN_STATUS
        task.meta["resource_hold"] = resource_hold.build(
            **daemon._starvation_hold_spec(task, {}, 1.0, detail="starved"),
        )
        task.save(runs_dir)
        return task

    def test_still_starved_keeps_the_message_and_answers_with_the_reading(
        self, tmp_path, monkeypatch,
    ):
        held = self._starved_run(tmp_path)
        target = self._target(tmp_path, "evt-human")
        monkeypatch.setattr(daemon, "_held_run_binding_pct", lambda *a, **k: 1.7)

        survivors = daemon._handle_resource_held_events([target], None)

        assert survivors == []
        reread = protocol._read_event(target.inbox_dir / "evt-human.md")
        assert reread.get("defer_reason") == "resource_hold"
        persisted = Run.from_file(tmp_path / ".brr" / "runs" / held.id / "run.md")
        hold = persisted.meta["resource_hold"]
        assert hold["released"] is False
        assert "evt-human" in hold["accumulated_event_ids"]
        partial = _partials(target.responses_dir, "evt-human")
        assert "1.7%" in partial and "`respawn <core>`" in partial and "`force`" in partial

    def test_the_gate_delivers_the_kept_answer_while_the_event_stays_pending(
        self, tmp_path, monkeypatch,
    ):
        """The kept message's answer must reach the chat *while* it is kept.

        Measured 2026-09-11 (evt-…-x3co): the reply was staged 3s after his
        message and reached Telegram 4m35s later — after the "Stopped" line
        that made it untrue — because ``deliver_stream`` swept only
        ``processing``/``done`` events and a kept message stays ``pending``
        for the whole ``_HOLD_DEFER_SECONDS`` horizon. Every assertion above
        reads the partial straight off disk, which is exactly the half that
        was never broken; this one goes through the gate that speaks.
        """
        self._starved_run(tmp_path)
        target = self._target(tmp_path, "evt-human")
        monkeypatch.setattr(daemon, "_held_run_binding_pct", lambda *a, **k: 1.7)
        daemon._handle_resource_held_events([target], None)

        sent: list[str] = []

        def _deliver(_event, body):
            sent.append(body)
            return {"message_id": len(sent)}

        gate_runtime.deliver_stream(
            target.inbox_dir, target.responses_dir, "telegram", _deliver,
        )

        assert len(sent) == 1 and "1.7%" in sent[0]
        # Delivered, and still kept: answering a starved seat's mail must not
        # advance the event toward dispatch.
        reread = protocol._read_event(target.inbox_dir / "evt-human.md")
        assert reread.get("status") == "pending"
        assert reread.get("defer_reason") == "resource_hold"

        gate_runtime.deliver_stream(
            target.inbox_dir, target.responses_dir, "telegram", _deliver,
        )
        assert len(sent) == 1  # never twice

    def test_refilled_thaws_on_the_message_itself(self, tmp_path, monkeypatch):
        held = self._starved_run(tmp_path)
        target = self._target(tmp_path, "evt-human")
        monkeypatch.setattr(daemon, "_held_run_binding_pct", lambda *a, **k: 100.0)

        survivors = daemon._handle_resource_held_events([target], None)

        assert survivors == [target]
        persisted = Run.from_file(tmp_path / ".brr" / "runs" / held.id / "run.md")
        assert persisted.meta["resource_hold"]["released_by"] == "refill"

    def test_an_unreadable_quota_is_not_a_refill(self, tmp_path, monkeypatch):
        self._starved_run(tmp_path)
        target = self._target(tmp_path, "evt-human")
        monkeypatch.setattr(daemon, "_held_run_binding_pct", lambda *a, **k: None)
        assert daemon._handle_resource_held_events([target], None) == []
        assert "unreadable" in _partials(target.responses_dir, "evt-human")

    def test_the_sweep_thaws_on_a_measured_refill(self, tmp_path, monkeypatch):
        held = self._starved_run(tmp_path)
        target = self._target(tmp_path, "evt-kept")
        monkeypatch.setattr(daemon, "_held_run_binding_pct", lambda *a, **k: 0.9)
        daemon._handle_resource_held_events([target], None)
        assert daemon._release_reset_holds_due(None, tmp_path) == 0

        monkeypatch.setattr(daemon, "_held_run_binding_pct", lambda *a, **k: 12.0)
        assert daemon._release_reset_holds_due(None, tmp_path) == 1
        persisted = Run.from_file(tmp_path / ".brr" / "runs" / held.id / "run.md")
        assert persisted.meta["resource_hold"]["released_by"] == "refill"
        reread = protocol._read_event(target.inbox_dir / "evt-kept.md")
        assert not reread.get("defer_until")


# ── the record itself ─────────────────────────────────────────────────


def test_build_keeps_quota_and_deadline_only_for_refill():
    refill = resource_hold.build(
        reason="x", provider="claude", resume_condition=resource_hold.RESUME_REFILL,
        reset_deadline=1.0, quota={"refill_floor_pct": 10},
    )
    assert refill["quota"] == {"refill_floor_pct": 10} and refill["reset_deadline"] == 1.0
    plain = resource_hold.build(reason="x", provider="claude", quota={"refill_floor_pct": 10})
    assert plain["quota"] is None
    assert resource_hold.refill_condition_met(refill, 10.0)
    assert not resource_hold.refill_condition_met(refill, 9.9)
    assert not resource_hold.refill_condition_met(refill, None)
    assert resource_hold.refuses_correspondent(refill)
    assert not resource_hold.refuses_correspondent(plain)


def test_hold_verb_parses_refill():
    from brr import hold_verb
    spec, err = hold_verb.parse_hold({"hold": "true", "resume": "refill"})
    assert err is None and spec["resume_condition"] == resource_hold.RESUME_REFILL


# ── the words a person can say to a starved seat ─────────────────────


class TestBounceVerbs:
    def test_parse(self):
        assert daemon._bounce_verb("force") == ("force", "")
        assert daemon._bounce_verb("  FORCE ") == ("force", "")
        assert daemon._bounce_verb("force it") == (None, "")
        assert daemon._bounce_verb("please force") == (None, "")
        assert daemon._bounce_verb("stop") == ("stop", "")
        assert daemon._bounce_verb("release") == ("stop", "")
        assert daemon._bounce_verb("wait") == ("wait", "")
        assert daemon._bounce_verb("respawn claude-opus") == ("respawn", "claude-opus")
        assert daemon._bounce_verb("respawn claude opus") == ("respawn", "claude opus")
        assert daemon._bounce_verb("hello?") == (None, "")
        assert daemon._bounce_verb("") == (None, "")

    def test_force_wakes_the_seat_under_the_floor_and_the_facet_honours_it(
        self, tmp_path, monkeypatch,
    ):
        held = TestRefusedWake._starved_run(TestRefusedWake(), tmp_path)
        target = TestRefusedWake._target(TestRefusedWake(), tmp_path, "evt-force")
        (target.inbox_dir / "evt-force.md").write_text(
            "---\nid: evt-force\nsource: telegram\nstatus: pending\n---\nforce\n",
            encoding="utf-8",
        )
        target.event["body"] = "force"
        monkeypatch.setattr(daemon, "_held_run_binding_pct", lambda *a, **k: 0.8)

        survivors = daemon._handle_resource_held_events([target], None)

        assert survivors == [target]
        assert target.event["starvation_forced"] is True
        persisted = Run.from_file(tmp_path / ".brr" / "runs" / held.id / "run.md")
        assert persisted.meta["resource_hold"]["released_by"] == "force"
        # the resumed seat's boundary reads under the floor and does not re-park
        task = Run.from_event(dict(target.event, id="evt-force"))
        _state, facet = daemon._starvation_facet(
            task, None, {}, {"binding_remaining_pct": 0.8}, None,
        )
        assert facet["forced"] is True and facet["starved"] is True
        assert "pending_resource_hold" not in task.meta

    def test_stop_ends_the_seat_and_answers(self, tmp_path, monkeypatch):
        held = TestRefusedWake._starved_run(TestRefusedWake(), tmp_path)
        target = TestRefusedWake._target(TestRefusedWake(), tmp_path, "evt-stop")
        target.event["body"] = "stop"
        monkeypatch.setattr(daemon, "_held_run_binding_pct", lambda *a, **k: 0.8)
        replies: list[str] = []
        monkeypatch.setattr(daemon, "_write_control_response", lambda t, b: replies.append(b))

        assert daemon._handle_resource_held_events([target], None) == []
        persisted = Run.from_file(tmp_path / ".brr" / "runs" / held.id / "run.md")
        assert persisted.meta["resource_hold"]["released_by"] == "dashboard"
        assert persisted.status == "done"
        assert replies and "Stopped" in replies[0]

    def test_respawn_on_a_catalog_core_mints_the_handoff(self, tmp_path, monkeypatch):
        held = TestRefusedWake._starved_run(TestRefusedWake(), tmp_path)
        target = TestRefusedWake._target(TestRefusedWake(), tmp_path, "evt-respawn")
        target.event["body"] = "respawn claude-opus"
        monkeypatch.setattr(daemon, "_held_run_binding_pct", lambda *a, **k: 0.8)
        monkeypatch.setattr(
            daemon.runner, "available_runner_catalog",
            lambda *a, **k: [{"name": "claude-opus", "shell": "claude", "core": "opus"}],
        )
        replies: list[str] = []
        monkeypatch.setattr(daemon, "_write_control_response", lambda t, b: replies.append(b))

        assert daemon._handle_resource_held_events([target], None) == []
        persisted = Run.from_file(tmp_path / ".brr" / "runs" / held.id / "run.md")
        assert persisted.meta["resource_hold"]["released_by"] == "respawn"
        minted = [
            p for p in target.inbox_dir.glob("*.md")
            if "Respawned from the dashboard on claude / opus" in p.read_text()
        ]
        assert len(minted) == 1
        assert replies and "Respawning on claude / opus" in replies[0]

    def test_respawn_on_an_unknown_core_refuses_with_the_names(self, tmp_path, monkeypatch):
        TestRefusedWake._starved_run(TestRefusedWake(), tmp_path)
        target = TestRefusedWake._target(TestRefusedWake(), tmp_path, "evt-bad")
        target.event["body"] = "respawn gpt-9"
        monkeypatch.setattr(daemon, "_held_run_binding_pct", lambda *a, **k: 0.8)
        monkeypatch.setattr(
            daemon.runner, "available_runner_catalog",
            lambda *a, **k: [{"name": "claude-opus", "shell": "claude", "core": "opus"}],
        )
        replies: list[str] = []
        monkeypatch.setattr(daemon, "_write_control_response", lambda t, b: replies.append(b))
        assert daemon._handle_resource_held_events([target], None) == []
        assert replies and "claude-opus" in replies[0] and "nothing respawned" in replies[0]
        persisted = Run.from_file(tmp_path / ".brr" / "runs" / "run-starved" / "run.md")
        assert persisted.meta["resource_hold"]["released"] is False

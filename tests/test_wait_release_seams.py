"""Four wait/release seams of the seat, driven through production code.

Each test names the guard it reads. Mocks: the quota reading
(``_held_run_binding_pct``), the pace reading handed to
``_resolve_await_state``, and the in-process run-control registry. Nothing
here starts a Shell, so no test proves a provider accepted a native resume.
It proves only what the daemon wrote: a hold record, a one-use claim file,
or an event routed to a dispatch.
"""

import json
import time

import pytest

from brr import daemon, pending_resume, protocol, resource_hold
from brr.run import Run


@pytest.fixture(autouse=True)
def _isolated_controls(monkeypatch):
    monkeypatch.setattr(daemon, "_run_controls", {})


def _dirs(tmp_path):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    outbox = brr_dir / "outbox" / "evt-current"
    responses = brr_dir / "responses"
    outbox.mkdir(parents=True)
    responses.mkdir(parents=True)
    return brr_dir, inbox, outbox, responses


def _seat_task(inbox, run_id="run-parent"):
    event = protocol.create_event(inbox, "telegram", "original", status="processing")
    task = Run(
        id=run_id, event_id=event.stem, body="original", source="telegram",
        env="host", conversation_key="cloud:telegram:1:",
    )
    task.meta["runner_shell"] = "codex"
    return task


def _drain(brr_dir, inbox, outbox, responses, task, directive):
    (outbox / "directive.md").write_text(directive, encoding="utf-8")
    return daemon._drain_outbox(
        daemon._WorkerEmit(brr_dir, None, task.event_id),
        task, responses, task.event_id, outbox, inbox,
    )


def _target(tmp_path, brr_dir, inbox, event_path):
    return daemon._DispatchTarget(
        event=protocol._read_event(event_path), repo_root=tmp_path,
        inbox_dir=inbox, responses_dir=brr_dir / "responses", repo_label="home",
    )


def _park(task, brr_dir, tmp_path, **fields):
    base = {
        "reason": resource_hold.REASON_WAITING_ON_STRANDS,
        "provider": "codex",
        "native_session_id": "native-seat-123",
        "resume_kind": resource_hold.RESUME_NATIVE,
        "resume_condition": resource_hold.RESUME_STRANDS,
        "reset_deadline": None,
    }
    base.update(fields)
    return daemon._arm_resource_hold(
        task, brr_dir / "runs", conversation_key=task.conversation_key,
        repo_root=tmp_path, **base,
    )


def _submitted(inbox, parent, child="run-child", generation=1):
    return protocol.create_event(
        inbox, "spawn_submitted", f"child submitted generation {generation}",
        spawn_parent_run_id=parent, spawned_by_run=child,
        conversation_key=f"run:{parent}",
    )


# ── seam 1: bare initiative timeout vs a message that arrives first ──────────


def _armed_bare_await(tmp_path, monkeypatch):
    brr_dir, inbox, outbox, responses = _dirs(tmp_path)
    task = _seat_task(inbox)
    monkeypatch.setattr(daemon, "_working_child_controls", lambda _run_id: [])
    task.meta["hold_correspondent_at"] = time.time() - 3600
    _drain(
        brr_dir, inbox, outbox, responses, task,
        "---\nawait: true\ntimeout: none\ninitiative-default: true\n---\n",
    )
    # The idle stretch is 6 minutes old and the ceiling is 5: the deadline
    # has crossed, so only an event can stop the initiative timeout.
    task.meta["await"]["idle_since"] = time.time() - 360
    return inbox, outbox, task


_PACE_AHEAD = {"pace": {"elapsed_share_pct": 60, "consumed_share_pct": 40}}
_CFG = {"seat.initiative_after_minutes": 5}


def test_a_message_beats_the_initiative_timeout_and_nothing_parks(tmp_path, monkeypatch):
    inbox, outbox, task = _armed_bare_await(tmp_path, monkeypatch)
    message = protocol.create_event(inbox, "telegram", "are you there?")
    result = daemon._resolve_await_state(
        task, protocol.list_pending(inbox), outbox_dir=outbox,
        cfg=_CFG, pacing_status=_PACE_AHEAD,
    )
    # `await_verb.evaluate` runs before the deadline test, so the event wins.
    assert result["outcome"] == "event"
    assert "initiative" not in result
    assert "initiative" not in task.meta["await"]
    # The wait returns to running. No hold was staged or armed.
    assert not task.meta.get("pending_resource_hold")
    assert not task.meta.get("resource_hold")
    # The message also moves the correspondent clock that guards initiative.
    assert task.meta["hold_correspondent_at"] > time.time() - 60
    assert message.exists()


def test_the_event_outcome_is_sticky_when_the_deadline_is_crossed_later(
    tmp_path, monkeypatch,
):
    inbox, outbox, task = _armed_bare_await(tmp_path, monkeypatch)
    protocol.create_event(inbox, "telegram", "first")
    daemon._resolve_await_state(
        task, protocol.list_pending(inbox), outbox_dir=outbox,
        cfg=_CFG, pacing_status=_PACE_AHEAD,
    )
    again = daemon._resolve_await_state(
        task, [], outbox_dir=outbox, cfg=_CFG, pacing_status=_PACE_AHEAD,
    )
    assert again["outcome"] == "event"
    assert "initiative" not in again


def _arm_with_pending(tmp_path, monkeypatch, source, **extra):
    brr_dir, inbox, outbox, responses = _dirs(tmp_path)
    task = _seat_task(inbox)
    monkeypatch.setattr(daemon, "_working_child_controls", lambda _run_id: [])
    task.meta["hold_correspondent_at"] = time.time() - 3600
    old = protocol.create_event(inbox, source, "pending before arming", **extra)
    _drain(
        brr_dir, inbox, outbox, responses, task,
        "---\nawait: true\ntimeout: none\ninitiative-default: true\n---\n",
    )
    task.meta["await"]["idle_since"] = time.time() - 360
    task.meta["hold_correspondent_at"] = time.time() - 3600
    return old, inbox, outbox, task


def test_a_message_pending_at_arm_time_still_resolves_the_wait(tmp_path, monkeypatch):
    """The arm snapshot excludes only observed ``spawn_completed`` events of
    this run. A correspondent message already pending is not excluded."""
    old, inbox, outbox, task = _arm_with_pending(tmp_path, monkeypatch, "telegram")
    assert task.meta["await"]["armed_pending_ids"] == []
    result = daemon._resolve_await_state(
        task, protocol.list_pending(inbox), outbox_dir=outbox,
        cfg=_CFG, pacing_status=_PACE_AHEAD,
    )
    assert result["outcome"] == "event"
    assert "initiative" not in result


def test_an_observed_completion_pending_at_arm_time_does_not_stop_initiative(
    tmp_path, monkeypatch,
):
    brr_dir, inbox, outbox, responses = _dirs(tmp_path)
    task = _seat_task(inbox)
    monkeypatch.setattr(daemon, "_working_child_controls", lambda _run_id: [])
    done = protocol.create_event(
        inbox, "spawn_completed", "child finished",
        spawn_parent_run_id=task.id, observed_by=task.id,
    )
    _drain(
        brr_dir, inbox, outbox, responses, task,
        "---\nawait: true\ntimeout: none\ninitiative-default: true\n---\n",
    )
    assert task.meta["await"]["armed_pending_ids"] == [done.stem]
    task.meta["await"]["idle_since"] = time.time() - 360
    task.meta["hold_correspondent_at"] = time.time() - 3600
    pending = [
        e for e in protocol.list_pending(inbox) if e.get("source") == "spawn_completed"
    ]
    result = daemon._resolve_await_state(
        task, pending, outbox_dir=outbox, cfg=_CFG, pacing_status=_PACE_AHEAD,
    )
    assert result["outcome"] == "timeout"
    assert result["initiative"] is True


# ── seam 2: refill release, hold generations, claim vs provider proof ───────


def _refill_hold(tmp_path, brr_dir, inbox, run_id, session):
    task = _seat_task(inbox, run_id)
    _park(
        task, brr_dir, tmp_path,
        reason=resource_hold.REASON_QUOTA_STARVED,
        resume_condition=resource_hold.RESUME_REFILL,
        native_session_id=session,
        resume_kind=(
            resource_hold.RESUME_NATIVE if session
            else resource_hold.RESUME_UNSUPPORTED
        ),
        quota={
            "binding_remaining_pct": 1.0, "starve_floor_pct": 2.0,
            "refill_floor_pct": 10.0, "runner": "codex", "model": "default",
        },
    )
    return task


def test_a_refill_claim_belongs_to_the_current_hold_not_an_older_generation(
    tmp_path, monkeypatch,
):
    brr_dir, inbox, _outbox, _responses = _dirs(tmp_path)
    runs_dir = brr_dir / "runs"
    old = _refill_hold(tmp_path, brr_dir, inbox, "run-old", "native-old")
    monkeypatch.setattr(daemon, "_held_run_binding_pct", lambda *a, **k: 50.0)
    assert daemon._release_reset_holds_due(None, tmp_path) == 1
    assert pending_resume.consume(
        brr_dir, conversation_key=old.conversation_key,
    )["session_id"] == "native-old"
    # A later seat parks on a new hold. A cold daemon reads it from disk only.
    new = _refill_hold(tmp_path, brr_dir, inbox, "run-new", "native-new")
    reloaded = Run.from_file(runs_dir / new.id / "run.md")
    assert reloaded.meta["resource_hold"]["generation"] == 1
    assert resource_hold.is_active(reloaded.meta["resource_hold"])
    assert not resource_hold.is_active(
        Run.from_file(runs_dir / old.id / "run.md").meta["resource_hold"]
    )
    monkeypatch.setattr(daemon, "_held_run_binding_pct", lambda *a, **k: 3.0)
    assert daemon._release_reset_holds_due(None, tmp_path) == 0
    assert pending_resume.peek(brr_dir) is None  # no reading, no claim
    monkeypatch.setattr(daemon, "_held_run_binding_pct", lambda *a, **k: 50.0)
    assert daemon._release_reset_holds_due(None, tmp_path) == 1
    claim = pending_resume.peek(brr_dir)
    assert (claim["session_id"], claim["from_run"]) == ("native-new", "run-new")
    # The old record keeps its own receipt and is not touched twice.
    old_meta = Run.from_file(runs_dir / old.id / "run.md").meta["resource_hold"]
    assert old_meta["released_by"] == "refill"
    assert old_meta["generation"] == 1


def test_a_rearmed_hold_on_one_run_counts_its_generation(tmp_path):
    brr_dir, inbox, _outbox, _responses = _dirs(tmp_path)
    task = _refill_hold(tmp_path, brr_dir, inbox, "run-seat", "native-1")
    first = task.meta["resource_hold"]["generation"]
    second = _park(
        task, brr_dir, tmp_path, reason=resource_hold.REASON_TURN_ENDED,
        resume_condition=resource_hold.RESUME_ANY,
    )
    assert (first, second["generation"]) == (1, 2)


def test_a_refill_without_a_native_session_is_a_cold_start_the_daemon_labels(
    tmp_path, monkeypatch,
):
    brr_dir, inbox, _outbox, _responses = _dirs(tmp_path)
    _refill_hold(tmp_path, brr_dir, inbox, "run-cold", None)
    monkeypatch.setattr(daemon, "_held_run_binding_pct", lambda *a, **k: 50.0)
    assert daemon._release_reset_holds_due(None, tmp_path) == 1
    # Daemon authority: the hold released and a wake event was minted.
    assert pending_resume.peek(brr_dir) is None
    sources = [e.get("source") for e in protocol.list_pending(inbox)]
    assert "measured-refill" in sources
    # Provider proof is absent: nothing here names a session to reopen.
    meta = Run.from_file(brr_dir / "runs" / "run-cold" / "run.md").meta["resource_hold"]
    assert meta["resume_kind"] == resource_hold.RESUME_UNSUPPORTED
    assert meta["native_session_id"] is None


def test_a_claimless_refill_leaves_an_earlier_unconsumed_claim_armed(
    tmp_path, monkeypatch,
):
    """Current behavior, pinned as a finding and not as a goal.

    The release path arms a claim only when the hold has a session id. It
    never clears one. A claim armed by an earlier release and not consumed
    therefore survives a later claimless release. The conversation key and
    the 24 hour expiry are the only fences left.
    """
    brr_dir, inbox, _outbox, _responses = _dirs(tmp_path)
    pending_resume.arm(
        brr_dir, session_id="native-stale", provider="codex",
        conversation_key="cloud:telegram:1:", from_run="run-earlier",
    )
    _refill_hold(tmp_path, brr_dir, inbox, "run-cold", None)
    monkeypatch.setattr(daemon, "_held_run_binding_pct", lambda *a, **k: 50.0)
    daemon._release_reset_holds_due(None, tmp_path)
    claim = pending_resume.peek(brr_dir)
    assert claim is not None and claim["session_id"] == "native-stale"


def test_a_claim_for_another_thread_is_left_and_an_old_one_expires(tmp_path):
    pending_resume.arm(
        tmp_path, session_id="s1", provider="codex",
        conversation_key="cloud:telegram:1:", from_run="run-a",
    )
    assert pending_resume.consume(tmp_path, conversation_key="run:run-a") is None
    assert pending_resume.peek(tmp_path)["session_id"] == "s1"
    record = pending_resume.peek(tmp_path)
    pending_resume.clear(tmp_path)
    pending_resume.arm(
        tmp_path, session_id=record["session_id"], provider="codex",
        conversation_key="cloud:telegram:1:", from_run="run-a",
    )
    path = pending_resume.path_for(tmp_path)
    data = json.loads(path.read_text())
    data["armed_at"] = time.time() - pending_resume.MAX_AGE_SECONDS - 5
    path.write_text(json.dumps(data))
    assert pending_resume.consume(
        tmp_path, conversation_key="cloud:telegram:1:",
    ) is None
    assert pending_resume.peek(tmp_path) is None


def test_a_provider_mismatch_is_a_cold_start_with_a_recorded_reason():
    task = Run(id="run-x", event_id="evt-x", body="b", source="telegram", env="host")
    task.meta["resume_native_session_id"] = "codex-thread"
    task.meta["resume_native_provider"] = "codex"

    class Choice:
        shell = "claude"

    assert daemon._resume_session_for_runner(task, Choice()) is None
    assert "Shell changed" in task.meta["resume_cold_reason"]
    Choice.shell = "codex"
    assert daemon._resume_session_for_runner(task, Choice()) == "codex-thread"


# ── seam 3: a retired submit, a handoff park, a tick, a fresh generation ────


def _held_on_child(tmp_path):
    brr_dir, inbox, _outbox, _responses = _dirs(tmp_path)
    task = _seat_task(inbox)
    _park(task, brr_dir, tmp_path)
    return brr_dir, inbox, task


def _handle(tmp_path, brr_dir, inbox, event_path):
    target = _target(tmp_path, brr_dir, inbox, event_path)
    return target, daemon._handle_resource_held_events([target], None)


def test_a_retired_submit_is_not_pending_and_a_fresh_generation_resumes(tmp_path):
    brr_dir, inbox, task = _held_on_child(tmp_path)
    first = _submitted(inbox, task.id, generation=1)
    protocol.set_status(protocol._read_event(first), "delivered")
    assert [e for e in protocol.list_pending(inbox) if e.get("source") == "spawn_submitted"] == []  # the retired one cannot release
    second = _submitted(inbox, task.id, generation=2)
    target, survivors = _handle(tmp_path, brr_dir, inbox, second)
    assert survivors == [target]
    meta = Run.from_file(brr_dir / "runs" / task.id / "run.md").meta["resource_hold"]
    assert meta["released_by"] == "strand"
    assert meta["generation"] == 1  # the hold's own generation, not the strand's
    assert pending_resume.peek(brr_dir)["from_run"] == task.id


def test_another_seats_strand_does_not_release_the_hold(tmp_path):
    brr_dir, inbox, task = _held_on_child(tmp_path)
    foreign = _submitted(inbox, "run-someone-else", child="run-other")
    target, survivors = _handle(tmp_path, brr_dir, inbox, foreign)
    assert resource_hold.is_active(
        Run.from_file(brr_dir / "runs" / task.id / "run.md").meta["resource_hold"]
    )
    # The event is deferred as accumulate-only mail or isolated; it never wakes.
    assert pending_resume.peek(brr_dir) is None


def test_a_schedule_tick_wakes_a_strands_hold_though_the_child_still_works(tmp_path):
    """Pins the guard the spec leaves out.

    ``schedule_event_releases`` wakes every hold that is not a wall, and a
    ``strands`` hold is not one. ``seat.yaml`` lists only a message and a
    strand return as exits of ``held_strands``.
    """
    brr_dir, inbox, task = _held_on_child(tmp_path)
    tick = protocol.create_event(inbox, "schedule", "every: tick")
    target, survivors = _handle(tmp_path, brr_dir, inbox, tick)
    assert survivors == [target]
    meta = Run.from_file(brr_dir / "runs" / task.id / "run.md").meta["resource_hold"]
    assert meta["released_by"] == "schedule"
    # The next strand return finds no active hold and is not deferred.
    later = _submitted(inbox, task.id, generation=2)
    target2, survivors2 = _handle(tmp_path, brr_dir, inbox, later)
    assert survivors2 == [target2]
    assert protocol._read_event(later)["status"] == "pending"


def test_a_schedule_tick_cannot_wake_a_refill_wall(tmp_path):
    brr_dir, inbox, _outbox, _responses = _dirs(tmp_path)
    _refill_hold(tmp_path, brr_dir, inbox, "run-wall", "native-w")
    tick = protocol.create_event(inbox, "schedule", "every: tick")
    target, survivors = _handle(tmp_path, brr_dir, inbox, tick)
    assert survivors == []
    meta = Run.from_file(brr_dir / "runs" / "run-wall" / "run.md").meta["resource_hold"]
    assert resource_hold.is_active(meta)
    assert meta["accumulated_event_ids"] == [tick.stem]


# ── seam 4: a cut's strand declaration is not a stop ────────────────────────


def _live_child(task):
    daemon._register_run_control("evt-child", task.id)
    daemon._bind_run_control("evt-child", "run-child")


@pytest.mark.parametrize("word", ["stopped", "converged", "abandoned"])
def test_declaring_a_live_strand_stopped_neither_stops_it_nor_parks_the_seat(
    tmp_path, word,
):
    brr_dir, inbox, outbox, responses = _dirs(tmp_path)
    task = _seat_task(inbox)
    task.meta["codex_thread_id"] = "native-seat-123"
    (outbox / ".topics").write_text("the-workshop\n", encoding="utf-8")
    _live_child(task)
    _drain(
        brr_dir, inbox, outbox, responses, task,
        f"---\ncut: true\nproduce: none\nowed: none\nstrands:\n"
        f"  run-child: {word} — done\n---\nClosing.\n",
    )
    assert "pending_resource_hold" not in task.meta
    rows = daemon._working_child_controls(task.id)
    assert [r.get("run_id") for r in rows] == ["run-child"]
    assert not daemon._find_run_control("evt-child").get("stopped")


def test_the_stop_verb_is_what_removes_the_child_from_the_owned_set(tmp_path):
    brr_dir, inbox, _outbox, _responses = _dirs(tmp_path)
    task = _seat_task(inbox)
    _live_child(task)
    control = daemon._find_run_control("evt-child")
    assert daemon._apply_run_stop(
        control, inbox, stopped_by=task.id, reason="done",
    ) == "running"  # no process is registered here; the flag is the contract
    assert control["stopped"] is True
    assert daemon._owned_child_controls(task.id) == []


def test_a_handoff_naming_a_child_that_is_not_live_closes_normally(tmp_path):
    brr_dir, inbox, outbox, responses = _dirs(tmp_path)
    task = _seat_task(inbox)
    (outbox / ".topics").write_text("the-workshop\n", encoding="utf-8")
    _drain(
        brr_dir, inbox, outbox, responses, task,
        "---\ncut: true\nproduce: none\nowed: none\nstrands:\n"
        "  run-gone: handoff — wait\n---\nWaiting.\n",
    )
    assert "pending_resource_hold" not in task.meta

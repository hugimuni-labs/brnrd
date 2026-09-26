"""Behavioral seams of the seat and strand lifecycle.

These tests use the real outbox drain and event projection. A held run is
released to ``done``; the next dispatch gets a one-use native resume claim.
"""

import json
import time

import pytest

from brr import daemon, pending_resume, protocol, resource_hold, shuttle
from brr.run import Run


@pytest.fixture(autouse=True)
def _isolated_controls(monkeypatch):
    monkeypatch.setattr(daemon, "_run_controls", {})


def _seat(tmp_path):
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    outbox = brr_dir / "outbox" / "evt-current"
    responses = brr_dir / "responses"
    outbox.mkdir(parents=True)
    event = protocol.create_event(inbox, "telegram", "original", status="processing")
    task = Run(
        id="run-parent", event_id=event.stem, body="original", source="telegram",
        env="host", conversation_key="cloud:telegram:1:",
    )
    task.meta["runner_shell"] = "codex"
    task.meta["codex_thread_id"] = "native-seat-123"
    (outbox / ".topics").write_text("the-workshop\n", encoding="utf-8")
    return brr_dir, inbox, outbox, responses, task


def _drain(brr_dir, inbox, outbox, responses, task, directive):
    (outbox / "directive.md").write_text(directive, encoding="utf-8")
    return daemon._drain_outbox(
        daemon._WorkerEmit(brr_dir, None, task.event_id),
        task, responses, task.event_id, outbox, inbox,
    )


def _live_child(task):
    daemon._register_run_control("evt-child", task.id)
    daemon._bind_run_control("evt-child", "run-child")


def _park_on_child(tmp_path):
    brr_dir, inbox, outbox, responses, task = _seat(tmp_path)
    _live_child(task)
    _drain(
        brr_dir, inbox, outbox, responses, task,
        "---\ncut: true\nproduce: none\nowed: none\nstrands:\n"
        "  run-child: handoff — wait for submission\n---\nWaiting.\n",
    )
    assert task.meta["bolt"]["annotated"] == 0
    hold_fields = task.meta["pending_resource_hold"]
    daemon._arm_resource_hold(
        task, brr_dir / "runs", conversation_key=task.conversation_key,
        repo_root=tmp_path, **hold_fields,
    )
    return brr_dir, inbox, task


def test_seat_await_resumes_on_event(tmp_path):
    brr_dir, inbox, outbox, responses, task = _seat(tmp_path)
    shuttle.Shuttle.load(brr_dir).transition(
        "awake", why="event_dispatched", run_id=task.id,
    )
    assert _drain(
        brr_dir, inbox, outbox, responses, task,
        "---\nawait: true\ntimeout: none\n---\n",
    ) == 1
    assert shuttle.Shuttle.load(brr_dir).state == "listening"
    before = daemon._write_live_portal_state(
        outbox, inbox, task.event_id, task, phase="running", shuttle_home=brr_dir,
    )
    assert json.loads(before.read_text())["await"]["resolved"] is False

    protocol.create_event(inbox, "telegram", "follow-up")
    after = daemon._write_live_portal_state(
        outbox, inbox, task.event_id, task, phase="running", shuttle_home=brr_dir,
    )
    assert json.loads(after.read_text())["await"]["outcome"] == "event"
    assert shuttle.Shuttle.load(brr_dir).state == "awake"


def test_cut_with_strands_handoff_parks_seat(tmp_path):
    brr_dir, _inbox, task = _park_on_child(tmp_path)
    persisted = Run.from_file(brr_dir / "runs" / task.id / "run.md")
    assert persisted.status == "held"
    assert persisted.meta["resource_hold"]["resume_condition"] == (
        resource_hold.RESUME_STRANDS
    )
    assert shuttle.Shuttle.load(brr_dir).state == "parked"


def test_cut_with_owed_does_not_park(tmp_path):
    brr_dir, inbox, outbox, responses, task = _seat(tmp_path)
    _live_child(task)
    # The bolt warns twice about the undispositioned live child, then
    # accepts an annotated declaration. Owed work is not a strand handoff.
    task.meta["cut_bounces"] = daemon._CUT_BOUNCE_CAP - 1
    _drain(
        brr_dir, inbox, outbox, responses, task,
        "---\ncut: true\nproduce: none\nowed:\n  child:\n"
        "    ref: run-child\n    why: still working\n---\nCarried.\n",
    )
    assert task.meta["bolt"]["annotated"] >= 1
    assert "pending_resource_hold" not in task.meta
    assert not (brr_dir / "runs" / task.id / "run.md").exists()


def test_strand_submit_resumes_held_seat(tmp_path):
    brr_dir, inbox, task = _park_on_child(tmp_path)
    child_event = protocol.create_event(
        inbox, "spawn_submitted", "child submitted generation 1",
        spawn_parent_run_id=task.id, spawned_by_run="run-child",
        conversation_key="run:run-parent",
    )
    target = daemon._DispatchTarget(
        event=protocol._read_event(child_event), repo_root=tmp_path,
        inbox_dir=inbox, responses_dir=brr_dir / "responses", repo_label="home",
    )
    assert daemon._handle_resource_held_events([target], None) == [target]
    persisted = Run.from_file(brr_dir / "runs" / task.id / "run.md")
    assert persisted.status == "done"
    assert persisted.meta["resource_hold"]["released_by"] == "strand"
    claim = pending_resume.peek(brr_dir)
    assert claim["session_id"] == "native-seat-123"
    assert claim["from_run"] == task.id


def test_initiative_fires_on_idle_seat(tmp_path, monkeypatch):
    brr_dir, inbox, outbox, responses, task = _seat(tmp_path)
    monkeypatch.setattr(daemon, "_working_child_controls", lambda _run_id: [])
    task.meta["hold_correspondent_at"] = time.time() - 3600
    _drain(
        brr_dir, inbox, outbox, responses, task,
        "---\nawait: true\ntimeout: none\ninitiative-default: true\n---\n",
    )
    task.meta["await"]["idle_since"] = time.time() - 360
    result = daemon._resolve_await_state(
        task, [], outbox_dir=outbox,
        cfg={"seat.initiative_after_minutes": 5},
        pacing_status={"pace": {"elapsed_share_pct": 60, "consumed_share_pct": 40}},
    )
    assert result["outcome"] == "timeout"
    assert result["initiative"] is True
    assert result["timeout_seconds"] == 300

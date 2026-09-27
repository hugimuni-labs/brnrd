"""Test that a held:refill park resumes automatically when quota refills."""

import json
import time

import pytest

from brr import daemon, pending_resume, protocol, resource_hold, shuttle
from brr.run import Run


@pytest.fixture(autouse=True)
def _isolated_controls(monkeypatch):
    monkeypatch.setattr(daemon, "_run_controls", {})


def _create_parked_refill_seat(tmp_path):
    """Set up a seat parked with held:refill hold."""
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    runs_dir = brr_dir / "runs"
    responses = brr_dir / "responses"
    outbox = brr_dir / "outbox" / "evt-current"
    outbox.mkdir(parents=True)
    responses.mkdir(parents=True)

    # Create an initial event and run
    event = protocol.create_event(
        inbox, "telegram", "original", status="processing",
    )
    task = Run(
        id="run-parked-seat",
        event_id=event.stem,
        body="original",
        source="telegram",
        env="host",
        conversation_key="cloud:telegram:1:",
    )
    task.meta["runner_shell"] = "claude"
    task.meta["claude_session_id"] = "native-session-123"

    # Arm a refill hold on this run
    hold_meta = {
        "resume_condition": resource_hold.RESUME_REFILL,
        "seat_key": "cloud:telegram:1:",
        "provider": "claude",
        "native_session_id": "native-session-123",
        "refill_floor_pct": 10.0,
        "armed_at": time.time(),
    }
    daemon._arm_resource_hold(
        task, runs_dir,
        conversation_key=task.conversation_key,
        repo_root=tmp_path,
        **hold_meta,
    )

    # Persist the run to disk
    runs_dir.mkdir(parents=True, exist_ok=True)
    task.write(runs_dir / task.id / "run.md")

    # Update shuttle to parked
    shuttle.Shuttle.load(brr_dir).transition(
        "parked", why="hold_armed", run_id=task.id,
    )

    return brr_dir, inbox, runs_dir, task


def test_held_refill_resume_creates_dispatch_event_when_no_accumulated(
    tmp_path, monkeypatch,
):
    """When a refill hold releases with no accumulated events, dispatch triggers."""
    brr_dir, inbox, runs_dir, task = _create_parked_refill_seat(tmp_path)

    # Mock the quota reading to simulate a refill
    def mock_binding_pct(*args, **kwargs):
        return 15.0  # Above the 10% floor

    monkeypatch.setattr(daemon, "_held_run_binding_pct", mock_binding_pct)

    # Call the release function
    released_count = daemon._release_reset_holds_due(None, tmp_path)

    # Verify one hold was released
    assert released_count == 1

    # Verify the run is now marked as done
    persisted = Run.from_file(runs_dir / task.id / "run.md")
    assert persisted.status == "done"
    assert persisted.meta["resource_hold"]["released_by"] == "refill"

    # Verify pending resume was armed
    claim = pending_resume.peek(brr_dir)
    assert claim is not None
    assert claim["session_id"] == "native-session-123"
    assert claim["from_run"] == task.id

    # Verify a synthetic event was created to trigger dispatch
    events = list(inbox.glob("*.md"))
    # There should be at least one event (the original) + the synthetic one
    assert len(events) >= 1
    # Find the synthetic event (should have source "measured-refill")
    synthetic_found = False
    for event_path in events:
        ev = protocol._read_event(event_path)
        if ev and ev.get("source") == "measured-refill":
            synthetic_found = True
            assert ev.get("body") == "Quota refill: refill"
            assert ev.get("released_seat") == task.id
    assert synthetic_found, "No synthetic measured-refill event created"


def test_held_refill_resume_undefers_accumulated_events(tmp_path, monkeypatch):
    """When a refill hold releases with accumulated events, they are undeferred."""
    brr_dir, inbox, runs_dir, task = _create_parked_refill_seat(tmp_path)

    # Create some accumulated events while the seat is held
    # These would normally be deferred in a real scenario, but for this test
    # we'll just mark them as existing
    accumulated_event = protocol.create_event(
        inbox, "telegram", "accumulated message",
    )
    accumulated_id = accumulated_event.stem

    # Update the hold to include this accumulated event
    held_run = Run.from_file(runs_dir / task.id / "run.md")
    hold_meta = held_run.meta.get("resource_hold") or {}
    hold_meta["accumulated_event_ids"] = [accumulated_id]
    held_run.meta["resource_hold"] = hold_meta
    held_run.write(runs_dir / task.id / "run.md")

    # Mock the quota reading
    def mock_binding_pct(*args, **kwargs):
        return 15.0

    monkeypatch.setattr(daemon, "_held_run_binding_pct", mock_binding_pct)

    # Call the release function
    released_count = daemon._release_reset_holds_due(None, tmp_path)

    assert released_count == 1

    # Verify the accumulated event is now undeferred
    accumulated_ev = protocol._read_event(inbox / f"{accumulated_id}.md")
    assert accumulated_ev is not None
    assert accumulated_ev.get("defer_until") is None
    assert accumulated_ev.get("deferred_by_run") is None

    # Verify no synthetic event was created (since there were accumulated events)
    events = list(inbox.glob("*.md"))
    for event_path in events:
        ev = protocol._read_event(event_path)
        if ev:
            # Should not find a synthetic event
            assert ev.get("source") != "measured-refill"

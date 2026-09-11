"""#1890, redone — one seat per repo, ticks wake it, the resident cannot park by choice.

#1908 split seats per conversation and made a tick spend a fresh run while a
seat was parked; reverted (#1913). The maintainer's shape, 2026-09-11
(evt-…-oiz9): an always-occupied seat that runs as long as it can, parked
only by the user or a resource wall. design-the-seat-that-never-quits.md:
*schedule ticks — land on the parked seat and resume it (a tick is a reason
to wake, not a new life).*

The one right piece of #1908 is kept: a resumed seat stays on its own
conversation. On 2026-09-10 a ``schedule:the-wire-round`` tick released the
Telegram seat and the resume booted into the tick's thread carrying the
seat's warm session; the maintainer's next message found nothing parked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from brr import conversations, daemon, protocol, resource_hold
from brr.run import Run

SEAT_CONV = "cloud:telegram:1:"
TICK_CONV = "schedule:the-wire-round"
WARM = "sess-warm-abc"


def _runs_dir(tmp_path: Path) -> Path:
    runs_dir = tmp_path / ".brr" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    return runs_dir


def _park(
    tmp_path: Path,
    *,
    condition: str,
    run_id: str = "run-seat-A",
    reason: str = resource_hold.REASON_TURN_ENDED,
    accumulated: tuple[str, ...] = (),
    now: float | None = None,
) -> Run:
    quota = (
        {"binding_remaining_pct": 1.0, "starve_floor_pct": 2.0, "refill_floor_pct": 10.0}
        if condition == resource_hold.RESUME_REFILL else None
    )
    meta = resource_hold.build(
        reason=reason, provider="claude", resume_condition=condition,
        conversation_key=SEAT_CONV, native_session_id=WARM,
        resume_kind=resource_hold.RESUME_NATIVE, quota=quota, now=now,
    )
    for eid in accumulated:
        meta = resource_hold.accumulate_event(meta, eid)
    seat = Run(id=run_id, event_id=f"evt-lead-{run_id}", body="", status=resource_hold.RUN_STATUS)
    seat.conversation_key = SEAT_CONV
    seat.meta["resource_hold"] = meta
    seat.save(_runs_dir(tmp_path))
    return seat


def _target(tmp_path: Path, *, eid: str, source: str, **extra: str) -> "daemon._DispatchTarget":
    inbox_dir = tmp_path / ".brr" / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"id: {eid}", f"source: {source}", "status: pending"]
    lines += [f"{k}: {v}" for k, v in extra.items()]
    (inbox_dir / f"{eid}.md").write_text(
        "---\n" + "\n".join(lines) + "\n---\nbody\n", encoding="utf-8",
    )
    return daemon._DispatchTarget(
        event=protocol._read_event(inbox_dir / f"{eid}.md"), repo_root=tmp_path,
        inbox_dir=inbox_dir, responses_dir=tmp_path / ".brr" / "responses",
        repo_label="home",
    )


def _hold(tmp_path: Path, run_id: str = "run-seat-A") -> dict:
    return Run.from_file(_runs_dir(tmp_path) / run_id / "run.md").meta["resource_hold"]


# ── a tick wakes the seat, and the seat stays on its own thread ─────


@pytest.mark.parametrize("condition", [
    resource_hold.RESUME_ANY, resource_hold.RESUME_OPERATOR, resource_hold.RESUME_STRANDS,
])
def test_a_foreign_tick_resumes_the_seat_on_the_seats_own_thread(tmp_path, condition):
    _park(tmp_path, condition=condition)
    tick = _target(tmp_path, eid="evt-tick", source="schedule", conversation_key=TICK_CONV)

    survivors = daemon._handle_resource_held_events([tick], None)

    assert survivors == [tick]
    hold = _hold(tmp_path)
    assert hold["released"] is True and hold["released_by"] == "schedule"
    # the one right piece of #1908: the resume is re-keyed to the seat
    assert conversations.conversation_key_for_event(tick.event) == SEAT_CONV
    assert tick.event["resume_native_session_id"] == WARM
    on_disk = protocol._read_event(tick.inbox_dir / "evt-tick.md")
    assert on_disk.get("conversation_key") == SEAT_CONV
    assert on_disk.get("resume_native_session_id") == WARM


def test_the_next_message_finds_the_seat_not_a_fresh_run(tmp_path):
    """The whole #1890 incident, driven end to end on the hold machinery.

    tick resumes the Telegram seat → the resumed run is keyed to the seat's
    conversation (dispatch derives it from the lead) → its turn ends and the
    daemon parks it again on *that* key → the maintainer's next message
    resumes the seat (native session carried) instead of passing through the
    cross-conversation guard as a stranger (a fresh, cold run).
    """
    runs_dir = _runs_dir(tmp_path)
    _park(tmp_path, condition=resource_hold.RESUME_ANY)
    tick = _target(tmp_path, eid="evt-tick", source="schedule", conversation_key=TICK_CONV)
    assert daemon._handle_resource_held_events([tick], None) == [tick]

    resumed = Run(id="run-seat-A2", event_id="evt-tick", body="", status="running")
    resumed.conversation_key = conversations.conversation_key_for_event(tick.event) or ""
    resumed.save(runs_dir)
    daemon._arm_resource_hold(
        resumed, runs_dir, conversation_key=resumed.conversation_key,
        reason=resource_hold.REASON_TURN_ENDED, provider="claude",
        resume_condition=resource_hold.RESUME_ANY, native_session_id=WARM,
        resume_kind=resource_hold.RESUME_NATIVE,
    )

    msg = _target(tmp_path, eid="evt-msg", source="cloud", conversation_key=SEAT_CONV)
    assert daemon._handle_resource_held_events([msg], None) == [msg]
    assert _hold(tmp_path, "run-seat-A2")["released_by"] == "operator"
    assert msg.event.get("resume_native_session_id") == WARM


def test_a_tick_accumulated_under_a_wall_is_rekeyed_when_the_wall_lifts(tmp_path):
    """A wall's ticks un-defer on the measured refill; the one that leads the
    resumed dispatch must still boot on the seat's thread."""
    _park(tmp_path, condition=resource_hold.RESUME_REFILL)
    tick = _target(tmp_path, eid="evt-tick", source="schedule", conversation_key=TICK_CONV)
    assert daemon._handle_resource_held_events([tick], None) == []

    seat = Run.from_file(_runs_dir(tmp_path) / "run-seat-A" / "run.md")
    msg = _target(tmp_path, eid="evt-msg", source="cloud", conversation_key=SEAT_CONV)
    daemon._apply_resource_hold_resume(
        _runs_dir(tmp_path), msg.inbox_dir, seat, msg.event, by="refill",
    )

    on_disk = protocol._read_event(tick.inbox_dir / "evt-tick.md")
    assert on_disk.get("defer_reason") is None
    assert on_disk.get("conversation_key") == SEAT_CONV
    assert on_disk.get("resume_native_session_id") == WARM


def test_a_correspondent_from_another_thread_keeps_its_own_key(tmp_path):
    """Re-keying is for routine mail only — a person's message routes its
    reply by its own thread, and still never releases a foreign seat."""
    _park(tmp_path, condition=resource_hold.RESUME_ANY)
    other = _target(tmp_path, eid="evt-gh", source="github", conversation_key="github:o/r#7")
    assert daemon._handle_resource_held_events([other], None) == [other]
    assert _hold(tmp_path)["released"] is False
    assert conversations.conversation_key_for_event(other.event) == "github:o/r#7"
    assert "resume_native_session_id" not in other.event


# ── walls: the seat cannot run, so ticks accumulate ─────────────────


@pytest.mark.parametrize("condition,reason", [
    (resource_hold.RESUME_REFILL, resource_hold.REASON_QUOTA_STARVED),
    (resource_hold.RESUME_RESET, resource_hold.REASON_RESIDENT_REQUESTED),
    # the automatic codex usage-limit hold arms `operator` — still a wall
    (resource_hold.RESUME_OPERATOR, resource_hold.REASON_QUOTA_EXHAUSTED),
])
def test_a_tick_under_a_wall_accumulates_and_wakes_nothing(tmp_path, condition, reason):
    _park(tmp_path, condition=condition, reason=reason)
    tick = _target(tmp_path, eid="evt-tick", source="schedule", conversation_key=TICK_CONV)

    assert daemon._handle_resource_held_events([tick], None) == []

    hold = _hold(tmp_path)
    assert hold["released"] is False
    assert "evt-tick" in hold["accumulated_event_ids"]
    on_disk = protocol._read_event(tick.inbox_dir / "evt-tick.md")
    assert on_disk.get("defer_reason") == "resource_hold"
    assert "resume_native_session_id" not in tick.event


def test_wall_predicate():
    wall = resource_hold.is_resource_wall
    build = resource_hold.build
    assert wall(build(reason="x", provider="c", resume_condition=resource_hold.RESUME_REFILL))
    assert wall(build(reason="x", provider="c", resume_condition=resource_hold.RESUME_RESET))
    assert wall(build(reason=resource_hold.REASON_QUOTA_EXHAUSTED, provider="c"))
    assert not wall(build(reason="x", provider="c", resume_condition=resource_hold.RESUME_ANY))
    assert not wall(build(reason="x", provider="c", resume_condition=resource_hold.RESUME_OPERATOR))
    assert not wall(None)


# ── one seat per repo ──────────────────────────────────────────────


def test_arming_a_second_hold_supersedes_the_first_and_folds_its_mail(tmp_path):
    runs_dir = _runs_dir(tmp_path)
    _park(tmp_path, condition=resource_hold.RESUME_ANY, accumulated=("evt-old-1", "evt-old-2"))
    other = Run(id="run-seat-B", event_id="evt-b", body="", status="running")
    other.conversation_key = "github:o/r#7"
    other.save(runs_dir)

    meta = daemon._arm_resource_hold(
        other, runs_dir, conversation_key=other.conversation_key,
        reason=resource_hold.REASON_TURN_ENDED, provider="claude",
        resume_condition=resource_hold.RESUME_ANY,
    )

    old = _hold(tmp_path)
    assert old["released"] is True
    assert old["released_by"] == "superseded"
    assert old["superseded_by"] == "run-seat-B"
    assert meta["accumulated_event_ids"] == ["evt-old-1", "evt-old-2"]
    assert [r.id for r in daemon._held_runs_for_repo(runs_dir)] == ["run-seat-B"]


def test_ghost_holds_already_on_disk_collapse_into_the_newest(tmp_path):
    """The two `resume: any` ghosts in this repo today were armed before
    arm-time supersession existed; the seat read heals them."""
    runs_dir = _runs_dir(tmp_path)
    _park(tmp_path, condition=resource_hold.RESUME_ANY, run_id="run-ghost-old",
          accumulated=("evt-stranded",), now=1_000.0)
    _park(tmp_path, condition=resource_hold.RESUME_ANY, run_id="run-ghost-new", now=2_000.0)

    seat = daemon._repo_seat(runs_dir)

    assert seat is not None and seat.id == "run-ghost-new"
    assert _hold(tmp_path, "run-ghost-old")["superseded_by"] == "run-ghost-new"
    assert _hold(tmp_path, "run-ghost-new")["accumulated_event_ids"] == ["evt-stranded"]
    assert [r.id for r in daemon._held_runs_for_repo(runs_dir)] == ["run-ghost-new"]


# ── the resident cannot park by choice ─────────────────────────────


@pytest.mark.parametrize("condition", [
    resource_hold.RESUME_ANY, resource_hold.RESUME_OPERATOR, resource_hold.RESUME_STRANDS,
])
def test_a_resident_hold_without_a_wall_is_refused(condition):
    task = Run(id="run-r", event_id="evt-r", body="")
    task.meta["quota_binding_pct"] = 0.5  # even starved: these are not walls
    refusal = daemon._resident_hold_refusal(task, condition, {})
    assert refusal and refusal.startswith("hold refused:")
    assert "`brnrd await` is the resting state" in refusal


@pytest.mark.parametrize("condition", [resource_hold.RESUME_REFILL, resource_hold.RESUME_RESET])
def test_a_resident_wall_needs_the_measured_starvation(condition):
    task = Run(id="run-r", event_id="evt-r", body="")
    unmeasured = daemon._resident_hold_refusal(task, condition, {})
    assert unmeasured and "not measured this run" in unmeasured
    task.meta["quota_binding_pct"] = 40.0
    healthy = daemon._resident_hold_refusal(task, condition, {})
    assert healthy and "last read 40.0%" in healthy
    task.meta["quota_binding_pct"] = 1.2
    assert daemon._resident_hold_refusal(task, condition, {}) is None

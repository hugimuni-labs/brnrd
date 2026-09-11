"""One seat per conversation (#1890).

A hold used to be *stored on a run*, *looked up per repo* (every consumer
took ``_held_runs_for_repo(...)[0]``) and *mean per conversation*. Two
faces of that one defect, both driven live:

- 2026-09-10: a ``schedule:the-wire-round`` tick released a Telegram seat
  parked on ``resume: any`` and booted into the tick's thread carrying the
  seat's warm native session (``run-260910-0141-rkzy``);
- 2026-09-11: five overnight ticks on their own ``schedule:*``
  conversations sat deferred 2h–7h52m behind an operator seat on Telegram
  (``run-260910-1206-buo7``), while the repo held three active holds and
  ``held_runs[0]`` chose among them.

These pins drive ``_handle_resource_held_events`` / ``_arm_resource_hold``
directly — the dispatch-time filter and the arm path, the two places the
cardinality lives.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from brr import conversations, daemon, protocol, resource_hold
from brr.run import Run

TELEGRAM = "cloud:telegram:155783668:"
WIRE = "schedule:the-wire-round"
RELEASE_TICK = "schedule:release-push-dispatch-tick"


def _seat(
    tmp_path: Path, run_id: str, conversation: str, *,
    condition: str = resource_hold.RESUME_ANY,
    session: str | None = None,
    armed_at: float = 1_000.0,
    accumulated: tuple[str, ...] = (),
) -> Run:
    runs_dir = tmp_path / ".brr" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    meta = resource_hold.build(
        reason=resource_hold.REASON_TURN_ENDED, provider="claude",
        resume_condition=condition, conversation_key=conversation,
        native_session_id=session or f"sess-{run_id}",
        resume_kind=resource_hold.RESUME_NATIVE,
        now=armed_at,
    )
    for eid in accumulated:
        meta = resource_hold.accumulate_event(meta, eid)
    task = Run(id=run_id, event_id=f"evt-lead-{run_id}", body="", status=resource_hold.RUN_STATUS)
    task.conversation_key = conversation
    task.meta["resource_hold"] = meta
    task.save(runs_dir)
    return task


def _event(tmp_path: Path, eid: str, *, source: str, **fields) -> "daemon._DispatchTarget":
    inbox_dir = tmp_path / ".brr" / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"id: {eid}", f"source: {source}", "status: pending"]
    lines += [f"{k}: {v}" for k, v in fields.items()]
    (inbox_dir / f"{eid}.md").write_text(
        "---\n" + "\n".join(lines) + "\n---\nbody\n", encoding="utf-8",
    )
    return daemon._DispatchTarget(
        event=protocol._read_event(inbox_dir / f"{eid}.md"),
        repo_root=tmp_path, inbox_dir=inbox_dir,
        responses_dir=tmp_path / ".brr" / "responses", repo_label="home",
    )


def _tick(tmp_path: Path, eid: str, conversation: str) -> "daemon._DispatchTarget":
    return _event(tmp_path, eid, source="schedule", conversation_key=conversation)


def _telegram_message(tmp_path: Path, eid: str) -> "daemon._DispatchTarget":
    # The production shape: no raw `conversation_key`, a gate fingerprint.
    return _event(
        tmp_path, eid, source="cloud", cloud_platform="telegram",
        cloud_chat_id="155783668", cloud_user_id="155783668",
    )


def _hold(tmp_path: Path, run_id: str) -> dict:
    return Run.from_file(tmp_path / ".brr" / "runs" / run_id / "run.md").meta["resource_hold"]


# ── acceptance 1: a foreign tick is its own run, under every condition ──


@pytest.mark.parametrize("condition", sorted(resource_hold.RESUME_CONDITIONS))
def test_a_tick_on_another_conversation_dispatches_as_its_own_run(tmp_path, condition):
    _seat(tmp_path, "run-seat-y", TELEGRAM, condition=condition, session="sess-warm-y")
    tick = _tick(tmp_path, "evt-tick-x", WIRE)

    survivors = daemon._handle_resource_held_events([tick], None)

    assert survivors == [tick]  # dispatches
    on_disk = protocol._read_event(tick.inbox_dir / "evt-tick-x.md")
    assert on_disk.get("defer_until") is None  # not deferred behind Y
    assert on_disk.get("deferred_by_run") is None
    hold = _hold(tmp_path, "run-seat-y")
    assert hold["released"] is False  # does not release Y
    assert "evt-tick-x" not in hold["accumulated_event_ids"]
    assert "resume_native_session_id" not in tick.event  # does not inherit Y's session
    assert conversations.conversation_key_for_event(tick.event) == WIRE


# ── acceptance 2: a matching tick releases that seat, and only that one ──


def test_a_tick_on_the_seats_own_conversation_releases_that_seat_only(tmp_path):
    _seat(tmp_path, "run-seat-tg", TELEGRAM, session="sess-tg", armed_at=2_000.0)
    _seat(tmp_path, "run-seat-wire", WIRE, session="sess-wire", armed_at=1_000.0)
    tick = _tick(tmp_path, "evt-tick-wire", WIRE)

    survivors = daemon._handle_resource_held_events([tick], None)

    assert survivors == [tick]
    assert _hold(tmp_path, "run-seat-wire")["released"] is True
    assert _hold(tmp_path, "run-seat-wire")["released_by"] == "schedule"
    assert tick.event["resume_native_session_id"] == "sess-wire"
    assert _hold(tmp_path, "run-seat-tg")["released"] is False


def test_a_matching_tick_still_accumulates_under_a_non_any_seat(tmp_path):
    held = _seat(tmp_path, "run-seat-op", WIRE, condition=resource_hold.RESUME_OPERATOR)
    tick = _tick(tmp_path, "evt-tick-own", WIRE)

    assert daemon._handle_resource_held_events([tick], None) == []

    assert protocol._read_event(tick.inbox_dir / "evt-tick-own.md").get("deferred_by_run") == held.id
    assert "evt-tick-own" in _hold(tmp_path, held.id)["accumulated_event_ids"]


# ── acceptance 3: two seats coexist, each found by its own conversation ──


@pytest.mark.parametrize("telegram_is_newer", [True, False])
def test_two_seats_on_two_conversations_are_each_found_by_their_own(tmp_path, telegram_is_newer):
    _seat(
        tmp_path, "run-seat-tg", TELEGRAM, condition=resource_hold.RESUME_OPERATOR,
        session="sess-tg", armed_at=2_000.0 if telegram_is_newer else 1_000.0,
    )
    _seat(
        tmp_path, "run-seat-tick", RELEASE_TICK, condition=resource_hold.RESUME_ANY,
        session="sess-tick", armed_at=1_000.0 if telegram_is_newer else 2_000.0,
    )
    seats = daemon._held_seats_by_conversation(
        daemon._held_runs_for_repo(tmp_path / ".brr" / "runs"),
    )
    assert {k: r.id for k, r in seats.items()} == {
        TELEGRAM: "run-seat-tg", RELEASE_TICK: "run-seat-tick",
    }

    message = _telegram_message(tmp_path, "evt-human")
    tick = _tick(tmp_path, "evt-tick", RELEASE_TICK)
    survivors = daemon._handle_resource_held_events([tick, message], None)

    assert survivors == [tick, message]
    assert tick.event["resume_native_session_id"] == "sess-tick"
    assert message.event["resume_native_session_id"] == "sess-tg"
    assert _hold(tmp_path, "run-seat-tick")["released_by"] == "schedule"
    assert _hold(tmp_path, "run-seat-tg")["released_by"] == "operator"


# ── the 2026-09-11 overnight shape: ticks while an operator seat holds ──


def test_overnight_ticks_are_not_deferred_behind_an_operator_seat(tmp_path):
    _seat(tmp_path, "run-260910-1206-buo7", TELEGRAM, condition=resource_hold.RESUME_OPERATOR)
    ticks = [
        _tick(tmp_path, f"evt-night-{i}", conv)
        for i, conv in enumerate([WIRE, RELEASE_TICK, "schedule:reckon", WIRE, RELEASE_TICK])
    ]

    survivors = daemon._handle_resource_held_events(ticks, None)

    assert survivors == ticks
    assert _hold(tmp_path, "run-260910-1206-buo7")["accumulated_event_ids"] == []


# ── construction: at most one active hold per conversation ──


def test_arming_supersedes_an_older_seat_on_the_same_conversation(tmp_path):
    runs_dir = tmp_path / ".brr" / "runs"
    older = _seat(tmp_path, "run-old", RELEASE_TICK, accumulated=("evt-a", "evt-b"))
    other = _seat(tmp_path, "run-other", TELEGRAM, condition=resource_hold.RESUME_OPERATOR)
    fresh = Run(id="run-new", event_id="evt-lead-new", body="", status="running")
    fresh.conversation_key = RELEASE_TICK
    fresh.save(runs_dir)

    meta = daemon._arm_resource_hold(
        fresh, runs_dir, conversation_key=RELEASE_TICK,
        reason=resource_hold.REASON_TURN_ENDED, provider="claude",
        resume_condition=resource_hold.RESUME_ANY,
    )

    assert meta["accumulated_event_ids"] == ["evt-a", "evt-b"]  # the mail moved with the seat
    superseded = _hold(tmp_path, older.id)
    assert superseded["released"] is True
    assert superseded["released_by"] == "superseded"
    assert superseded["superseded_by"] == "run-new"
    assert _hold(tmp_path, other.id)["released"] is False  # another conversation's seat
    active = daemon._held_runs_for_repo(runs_dir)
    assert sorted(r.id for r in active) == ["run-new", "run-other"]


# ── strands stay ownership-checked by run id; the resume re-keys ──


def test_a_strand_release_resumes_into_the_seats_conversation(tmp_path):
    seat = _seat(tmp_path, "run-parent", TELEGRAM, condition=resource_hold.RESUME_STRANDS)
    kid = _event(
        tmp_path, "evt-kid", source="spawn_submitted",
        spawn_parent_run_id=seat.id, conversation_key=f"run:{seat.id}",
    )

    assert daemon._handle_resource_held_events([kid], None) == [kid]

    assert _hold(tmp_path, seat.id)["released_by"] == "strand"
    assert conversations.conversation_key_for_event(kid.event) == TELEGRAM


def test_a_strangers_strand_on_another_conversation_is_not_this_seats(tmp_path):
    _seat(tmp_path, "run-parent", TELEGRAM, condition=resource_hold.RESUME_STRANDS)
    stranger = _event(
        tmp_path, "evt-stranger", source="spawn_completed",
        spawn_parent_run_id="run-elsewhere", conversation_key=WIRE,
    )

    assert daemon._handle_resource_held_events([stranger], None) == [stranger]
    assert _hold(tmp_path, "run-parent")["accumulated_event_ids"] == []


# ── the #1890 body's repro, verbatim in shape ──


def test_issue_repro_prints_the_refusal(tmp_path):
    from _helpers import make_event, write_repo_scaffold

    write_repo_scaffold(tmp_path)
    runs_dir = tmp_path / ".brr" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    meta = resource_hold.build(
        reason=resource_hold.REASON_TURN_ENDED, provider="claude",
        resume_condition=resource_hold.RESUME_ANY,
        conversation_key="cloud:telegram:1:",
        native_session_id="sess-warm-abc",
        resume_kind=resource_hold.RESUME_NATIVE,
    )
    seat = Run(id="run-seat-A", event_id="evt-lead-A", body="", status=resource_hold.RUN_STATUS)
    seat.conversation_key = "cloud:telegram:1:"
    seat.meta["resource_hold"] = meta
    seat.save(runs_dir)
    ev = make_event(tmp_path, eid="evt-tick", source="schedule", conversation_key=WIRE)
    target = daemon._DispatchTarget(
        event=ev, repo_root=tmp_path, inbox_dir=tmp_path / ".brr" / "inbox",
        responses_dir=tmp_path / ".brr" / "responses", repo_label="home",
    )

    daemon._handle_resource_held_events([target], None)

    after = Run.from_file(runs_dir / "run-seat-A" / "run.md")
    assert after.meta["resource_hold"]["released"] is False
    assert after.meta["resource_hold"]["released_by"] is None
    assert ev.get("resume_native_session_id") is None

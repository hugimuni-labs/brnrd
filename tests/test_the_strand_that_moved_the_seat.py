"""#1991 — a strand's ``brnrd await`` must not move the seat's Shuttle row.

Observed 2026-09-15 19:25Z: the seat (``run-260915-1705-i18x``) armed a wait
and the row went ``listening``; one of its strands then armed and resolved its
own ``brnrd await --file …``. The arm was already seat-only, the resolve was
not: the strand's resolution flipped the seat's row back to ``awake`` and wrote
the strand's id into ``run_id``. ``brnrd loom`` read the row faithfully and
drew the strand as the resident.

Every test here drives the real pieces — ``_drain_outbox`` arms the staged
``await:`` directive, ``_write_live_portal_state`` (the heartbeat) resolves it
— over one shared ``.brr`` home, the way a seat and its strand share one
account home on the machine.
"""

from __future__ import annotations

from pathlib import Path

from brr import daemon, protocol, resource_hold, shuttle
from brr.run import Run


class _Run:
    """One run (seat or strand) with its own outbox over a shared home."""

    def __init__(self, brr_dir: Path, run_id: str, *, strand: bool, source: str):
        self.brr_dir = brr_dir
        self.inbox = brr_dir / "inbox"
        self.responses = brr_dir / "responses"
        self.inbox.mkdir(parents=True, exist_ok=True)
        own = protocol.create_event(self.inbox, source, "the task", status="processing")
        self.eid = own.stem
        self.outbox = brr_dir / "outbox" / self.eid
        self.outbox.mkdir(parents=True)
        self.task = Run(id=run_id, event_id=self.eid, body="the task", source=source)
        if strand:
            self.task.meta["strand"] = True
            self.task.meta["spawn_parent_run_id"] = "run-seat"

    def stage_await(self, *, file: Path | None = None) -> None:
        lines = ["---", "await: true", "timeout: none"]
        if file is not None:
            lines.append(f"file: {file}")
        lines += ["---", ""]
        (self.outbox / "await.md").write_text("\n".join(lines), encoding="utf-8")

    def drain(self) -> None:
        daemon._drain_outbox(
            daemon._WorkerEmit(self.brr_dir, None, self.eid),
            self.task, self.responses, self.eid, self.outbox, self.inbox,
        )

    def beat(self) -> None:
        daemon._write_live_portal_state(
            self.outbox, self.inbox, self.eid, self.task,
            phase="running", shuttle_home=self.brr_dir,
        )


def _row_bytes(brr_dir: Path) -> bytes:
    return (brr_dir / "shuttle.json").read_bytes()


def _home(tmp_path: Path) -> Path:
    brr_dir = tmp_path / ".brr"
    brr_dir.mkdir()
    return brr_dir


def _seat(brr_dir: Path) -> _Run:
    seat = _Run(brr_dir, "run-seat", strand=False, source="telegram")
    shuttle.Shuttle.load(brr_dir).transition(
        "awake", why="event_dispatched", by="daemon", run_id=seat.task.id,
    )
    return seat


def _strand_waits_on_a_file(brr_dir: Path, tmp_path: Path) -> _Run:
    """The issue's shape: submit, then ``brnrd await --file <gate>``."""
    strand = _Run(brr_dir, "run-strand", strand=True, source="spawn")
    gate = tmp_path / "gate.done"
    strand.stage_await(file=gate)
    strand.drain()
    strand.beat()
    assert strand.task.meta["await"]["resolved"] is False
    gate.write_text("green\n", encoding="utf-8")
    strand.beat()
    # The positive control for every assertion below: the strand's wait
    # really did arm *and* resolve through the path that used to move the row.
    assert strand.task.meta["await"]["resolved"] is True
    assert strand.task.meta["await"]["outcome"] == "condition"
    return strand


def test_a_strand_arming_and_resolving_an_await_leaves_the_row_byte_identical(tmp_path):
    brr_dir = _home(tmp_path)
    _seat(brr_dir)
    before = _row_bytes(brr_dir)

    _strand_waits_on_a_file(brr_dir, tmp_path)

    assert _row_bytes(brr_dir) == before


def test_a_strand_resolving_while_the_seat_listens_does_not_wake_the_seat(tmp_path):
    brr_dir = _home(tmp_path)
    seat = _seat(brr_dir)
    seat.stage_await()
    seat.drain()
    seat.beat()
    assert shuttle.Shuttle.load(brr_dir).state == "listening"
    before = _row_bytes(brr_dir)

    _strand_waits_on_a_file(brr_dir, tmp_path)

    assert _row_bytes(brr_dir) == before
    row = shuttle.Shuttle.load(brr_dir)
    assert (row.state, row.run_id, row.why) == ("listening", "run-seat", "await_armed")


def test_the_seats_own_await_still_goes_awake_listening_awake(tmp_path):
    brr_dir = _home(tmp_path)
    seat = _seat(brr_dir)

    seat.stage_await()
    seat.drain()
    seat.beat()
    assert shuttle.Shuttle.load(brr_dir).state == "listening"

    protocol.create_event(seat.inbox, "telegram", "still there?")
    seat.beat()

    row = shuttle.Shuttle.load(brr_dir)
    assert (row.state, row.run_id) == ("awake", "run-seat")
    assert [t["why"] for t in row.transitions] == [
        "event_dispatched", "await_armed", "await_resolved:event",
    ]


def test_the_seat_wakes_after_a_strand_waited_through_its_listen(tmp_path):
    """The two waits interleaved: the strand's resolution is a no-op, the
    seat's own later resolution is the one that moves the row."""
    brr_dir = _home(tmp_path)
    seat = _seat(brr_dir)
    seat.stage_await()
    seat.drain()
    seat.beat()

    _strand_waits_on_a_file(brr_dir, tmp_path)
    assert shuttle.Shuttle.load(brr_dir).state == "listening"

    protocol.create_event(seat.inbox, "telegram", "back")
    seat.beat()
    row = shuttle.Shuttle.load(brr_dir)
    assert (row.state, row.run_id, row.why) == ("awake", "run-seat", "await_resolved:event")


def test_a_row_left_listening_by_another_run_is_reclaimed_at_the_seats_arm(tmp_path):
    """A seat stopped mid-wait leaves the row ``listening`` under its id (only
    ``_finalize_completed`` releases the row). The resolve is now gated on
    the run id, so the next seat claims the row at its own arm instead."""
    brr_dir = _home(tmp_path)
    stale = shuttle.Shuttle.load(brr_dir)
    stale.transition("awake", why="event_dispatched", run_id="run-stopped")
    stale.transition("listening", why="await_armed", run_id="run-stopped")
    seat = _Run(brr_dir, "run-seat", strand=False, source="telegram")

    seat.stage_await()
    seat.drain()
    seat.beat()
    row = shuttle.Shuttle.load(brr_dir)
    assert (row.state, row.run_id) == ("listening", "run-seat")
    assert [t["why"] for t in row.transitions][-2:] == [
        "await_armed:reclaimed", "await_armed",
    ]

    protocol.create_event(seat.inbox, "telegram", "hello")
    seat.beat()
    row = shuttle.Shuttle.load(brr_dir)
    assert (row.state, row.run_id) == ("awake", "run-seat")


def test_a_strand_never_reclaims_a_listening_row(tmp_path):
    brr_dir = _home(tmp_path)
    seat = _seat(brr_dir)
    seat.stage_await()
    seat.drain()
    before = _row_bytes(brr_dir)

    strand = _Run(brr_dir, "run-strand", strand=True, source="spawn")
    strand.stage_await()
    strand.drain()

    assert strand.task.meta["await"]["resolved"] is False
    assert _row_bytes(brr_dir) == before


def test_a_strand_parked_on_a_resource_hold_leaves_the_seats_row_alone(tmp_path):
    """The other lifecycle write a strand can reach: a strand's ``hold:
    resume: refill`` passes the resident refusal whenever its own boundary
    measured the starvation wall, and ``_arm_resource_hold`` parked the row."""
    brr_dir = _home(tmp_path)
    runs_dir = brr_dir / "runs"
    runs_dir.mkdir()
    _seat(brr_dir)
    before = _row_bytes(brr_dir)
    strand = Run(id="run-strand", event_id="evt-strand", body="", source="spawn")
    strand.meta["strand"] = True
    strand.conversation_key = "spawn:default"
    strand.save(runs_dir)

    meta = daemon._arm_resource_hold(
        strand, runs_dir, conversation_key=strand.conversation_key,
        account_home=brr_dir,
        reason=resource_hold.REASON_TURN_ENDED, provider="claude",
        resume_condition=resource_hold.RESUME_ANY,
    )

    # The hold itself still lands on the strand's run record ...
    assert strand.meta["resource_hold"] == meta
    assert strand.status == resource_hold.RUN_STATUS
    # ... and the seat's row is untouched.
    assert _row_bytes(brr_dir) == before

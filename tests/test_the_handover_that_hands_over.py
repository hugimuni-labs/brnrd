"""brnrd#2022 — a respawn must wake a successor, not resume its predecessor.

Three mechanisms carried a parked seat's scroll into the run that existed to
replace it. All three were measured on disk on 2026-09-18, the afternoon
after #2016 shipped a fix for the same defect that was **nearly inert in
production** — because its own test used a hand-written fixture whose
``source: respawn`` nothing in the system produces.

So the bar for this module: the shape under test is read from
``tests/fixtures/handover/respawn_event_from_disk.md``, a byte-for-byte copy
of a real respawn event's frontmatter (``evt-1789741124175027000-udee``,
minted by a live resident at 14:18Z and dispatched by the daemon), not from
anything a test author invented.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from brr import daemon, pending_resume, protocol, resource_hold
from brr.run import Run

FIXTURE = Path(__file__).parent / "fixtures" / "handover" / "respawn_event_from_disk.md"


def _real_respawn_event(inbox_dir: Path) -> dict:
    """The real respawn event, laid into *inbox_dir* and read back off disk."""
    inbox_dir.mkdir(parents=True, exist_ok=True)
    eid = "evt-1789741124175027000-udee"
    shutil.copyfile(FIXTURE, inbox_dir / f"{eid}.md")
    event = protocol._read_event(inbox_dir / f"{eid}.md")
    assert event, "the fixture must parse as an event"
    return event


# ── the shape itself ────────────────────────────────────────────────


def test_the_real_respawn_event_defeats_the_shipped_predicate(tmp_path):
    """Why #2016 passed its suite and changed almost nothing in production.

    ``handover_event_releases`` shipped asking ``source == "respawn"``.
    ``_queue_respawn_request`` derives the child's source as
    ``fm -> current.get("source") -> task.source -> "respawn"`` — it
    *inherits* the waking event's source, and the literal is reached only
    when nothing upstream has one at all. The real event, read here off
    disk, says ``schedule``.
    """
    event = _real_respawn_event(tmp_path / "inbox")

    assert event["source"] == "schedule"
    assert event["source"] != "respawn"
    # And it is unmistakably a respawn by every other reading.
    assert event["respawned_from_event"].startswith("evt-")
    assert event["respawned_by_run"].startswith("run-")
    assert "respawn_reason" in event
    # ...including the third mechanism's own footprint: the seat's scroll,
    # stamped onto the successor's waking event.
    assert event["resume_native_session_id"] == "5196fef7-a011-4ebd-87ab-b70193b3ba44"


def test_the_marker_is_not_the_respawn_origin_fields(tmp_path):
    """``respawned_from_event``/``respawned_by_run`` cannot carry the meaning.

    ``_queue_spawn_request`` stamps both onto a **strand**'s dispatch event
    too — a spawn is the same system-to-system handoff shape, just concurrent
    instead of sequential. A child starting work must never release its
    parent's hold, so the handover marker has to be a fact of its own.
    """
    meta = resource_hold.build(
        reason="turn_ended", provider="claude",
        resume_condition=resource_hold.RESUME_ANY,
    )
    strandish = {
        "source": "spawn_queued",
        "respawned_from_event": "evt-parent",
        "respawned_by_run": "run-parent",
    }
    assert not resource_hold.handover_event_releases(meta, strandish)
    assert resource_hold.handover_event_releases(
        meta, dict(strandish, handover=True),
    )


@pytest.mark.parametrize("raw", [True, "true", "True", "yes", "1"])
def test_the_marker_survives_the_round_trip_through_a_file(raw):
    """Frontmatter is text. ``handover: true`` off disk is a string."""
    assert resource_hold.is_handover({"handover": raw})


@pytest.mark.parametrize("raw", [False, "false", "no", "", None])
def test_only_a_real_marker_is_a_handover(raw):
    assert not resource_hold.is_handover({"handover": raw})


# ── mechanism 2: identifiable without hijacking `source` ────────────


def _mint(tmp_path, monkeypatch, fm_text: str, *, waking: dict) -> dict:
    """Drive the real minter from *waking* and return the child it queued."""
    brr_dir = tmp_path / ".brr"
    inbox = brr_dir / "inbox"
    outbox = brr_dir / "outbox" / waking["id"]
    outbox.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(daemon.updates, "emit", lambda *_a, **_k: None)
    task = Run(
        id="run-seat", event_id=waking["id"], body="go",
        source=str(waking.get("source") or ""),
    )
    task.meta.update({
        k: v for k, v in waking.items()
        if k in ("repo_label", "trust_tier", "conversation_key")
    })
    emit = daemon._WorkerEmit(
        brr_dir=brr_dir, conversation_key="cloud:telegram:155783668:",
        event_id=waking["id"],
    )
    fm = protocol.parse_outbox_message(fm_text)[0]
    assert daemon._queue_respawn_request(
        emit, task, tmp_path, inbox, waking["id"], fm, "carry this forward", outbox,
    )
    children = [
        protocol._read_event(p) for p in inbox.glob("*.md")
        if p.stem != waking["id"]
    ]
    assert len(children) == 1
    return children[0]


def test_the_minter_reproduces_the_real_shape_and_marks_it(tmp_path, monkeypatch):
    """The minted child matches what the daemon really put on disk — plus the marker.

    Mints from the real event (a respawn is routinely the *waking* event of
    the seat that queues the next one), so the inheritance under test is the
    live one, not a reconstruction.
    """
    waking = _real_respawn_event(tmp_path / ".brr" / "inbox")

    child = _mint(
        tmp_path, monkeypatch,
        "---\nrespawn: true\ncore: opus\nreason: a fresh build\n---\ncarry this forward\n",
        waking=waking,
    )

    # The shape the real event has, reproduced: the source is inherited.
    assert child["source"] == "schedule"
    assert child["respawned_from_event"] == waking["id"]
    assert child["respawned_by_run"] == "run-seat"
    assert child["core"] == "opus"
    # The one fact #2016 needed and did not have.
    assert resource_hold.is_handover(child)


def test_the_mint_never_predicts_the_successors_scroll(tmp_path, monkeypatch):
    """Mechanism 2's quieter half: ``meta`` is a blanket copy of the waking event.

    The real event carries ``resume_native_session_id`` (it woke a seat by
    native resume). ``Run.from_event`` copies event frontmatter onto run meta
    just as blanketly, so a child that inherits that key resumes the very
    scroll the handover exists to end — hours before any releaser has had a
    say.
    """
    waking = _real_respawn_event(tmp_path / ".brr" / "inbox")
    assert waking["resume_native_session_id"]  # the leak's source material

    child = _mint(
        tmp_path, monkeypatch,
        "---\nrespawn: true\ncore: opus\n---\ncarry this forward\n",
        waking=waking,
    )

    assert "resume_native_session_id" not in child
    assert "resume_native_provider" not in child


def test_a_respawned_strand_is_not_a_handover(tmp_path, monkeypatch):
    """``respawn: true`` + ``strand: true`` mints a child, not a successor."""
    waking = _real_respawn_event(tmp_path / ".brr" / "inbox")

    child = _mint(
        tmp_path, monkeypatch,
        "---\nrespawn: true\nstrand: true\ncore: opus\n---\ngo\n",
        waking=waking,
    )

    assert child.get("strand")
    assert not resource_hold.is_handover(child)


# ── mechanism 1: the stamp sprayed across a hold's whole drawer ─────


class TestOnlyTheReleasingEventCarriesTheScroll:
    """``evt-…-2jb8``: a Sept-17 message carrying a session id minted Sept-18.

    ``_undefer_held_event`` stamped *every* accumulated event when a park
    released, on the theory that one of them might sort ahead of the trigger
    and lead the dispatch. One release therefore wrote N claims on one
    transcript, and each outlived the release on disk: a correspondent's
    message interrupted by a host suspend came out of the drawer a day later
    and was retried as a fresh ``claude --resume``.

    A message is mail, not a continuation request.
    """

    def _seat(self, tmp_path, **overrides) -> Run:
        runs_dir = tmp_path / ".brr" / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        # A wall, so a routine tick accumulates instead of releasing —
        # the drawer has to have something in it to be sprayed.
        meta = resource_hold.build(
            reason=resource_hold.REASON_QUOTA_EXHAUSTED, provider="codex",
            resume_condition=resource_hold.RESUME_OPERATOR,
            native_session_id="held-thread-1",
            resume_kind=resource_hold.RESUME_NATIVE,
        )
        meta.update(overrides)
        task = Run(
            id="run-held-1", event_id="evt-lead", body="",
            status=resource_hold.RUN_STATUS,
        )
        task.meta["resource_hold"] = meta
        task.save(runs_dir)
        return task

    def _target(self, tmp_path, *, source: str, eid: str, **fm):
        inbox_dir = tmp_path / ".brr" / "inbox"
        inbox_dir.mkdir(parents=True, exist_ok=True)
        path = inbox_dir / f"{eid}.md"
        extra = "".join(f"{k}: {v}\n" for k, v in fm.items())
        path.write_text(
            f"---\nid: {eid}\nsource: {source}\nstatus: pending\n{extra}---\nbody\n",
            encoding="utf-8",
        )
        return daemon._DispatchTarget(
            event=protocol._read_event(path), repo_root=tmp_path,
            inbox_dir=inbox_dir, responses_dir=tmp_path / ".brr" / "responses",
            repo_label="home",
        )

    def test_the_drawer_is_undeferred_without_the_scroll(self, tmp_path):
        self._seat(tmp_path)
        sibling = self._target(tmp_path, source="schedule", eid="evt-sibling")
        assert daemon._handle_resource_held_events([sibling], None) == []

        releaser = self._target(tmp_path, source="telegram", eid="evt-human")
        daemon._handle_resource_held_events([releaser], None)

        # brnrd#2023: nothing is the carrier. The seat holds one claim and
        # the event says nothing about a transcript at all.
        assert "resume_native_session_id" not in releaser.event
        assert pending_resume.peek(tmp_path / ".brr")["session_id"] == "held-thread-1"
        # The sibling is undeferred, re-keyed home, and carries nothing.
        reread = protocol._read_event(sibling.inbox_dir / "evt-sibling.md")
        assert reread.get("defer_until") is None
        assert reread.get("defer_reason") is None
        assert reread.get("resume_native_session_id") is None
        assert reread.get("resume_native_provider") is None

    def test_a_measured_refill_arms_the_claim_and_stamps_no_letter(self, tmp_path, monkeypatch):
        """The one release with no releasing event — and it no longer needs one.

        The reading is the releaser here; the drawer is all there is to wake
        on. Under the stamp that forced a choice between spraying the drawer
        and cooling every refill (#2022 took the middle: stamp exactly one).
        brnrd#2023 dissolves the choice — the claim is on the seat, and
        whichever letter leads picks it up.
        """
        held = self._seat(
            tmp_path,
            resume_condition=resource_hold.RESUME_RESET,
            reset_deadline=1000.0,
        )
        first = self._target(tmp_path, source="schedule", eid="evt-aaa")
        second = self._target(tmp_path, source="schedule", eid="evt-bbb")
        daemon._handle_resource_held_events([first, second], None)
        persisted = Run.from_file(tmp_path / ".brr" / "runs" / held.id / "run.md")
        assert persisted.meta["resource_hold"]["accumulated_event_ids"] == [
            "evt-aaa", "evt-bbb",
        ]
        monkeypatch.setattr(daemon.time, "time", lambda: 2000.0)

        assert daemon._release_reset_holds_due(None, tmp_path) == 1

        inbox = tmp_path / ".brr" / "inbox"
        lead = protocol._read_event(inbox / "evt-aaa.md")
        rest = protocol._read_event(inbox / "evt-bbb.md")
        assert lead.get("defer_until") is None
        assert rest.get("defer_until") is None
        assert lead.get("resume_native_session_id") is None
        assert rest.get("resume_native_session_id") is None
        assert pending_resume.peek(tmp_path / ".brr")["session_id"] == "held-thread-1"


# ── mechanism 3: the stamp is a cache, and it heals ─────────────────


class TestAHandoverReleaseCarriesNoScroll:
    """Deleting the stamp from the event does not work: it is re-derived.

    At 15:10Z on 2026-09-18 the two stamp lines were deleted from
    ``evt-…-udee`` by hand. At 15:14Z the run woke and the lines were back —
    ``_apply_resource_hold_resume`` re-derives them from
    ``resource_hold.native_session_id`` on the *parked run's* ``run.md`` at
    release time. So the enforcement has to live at the release, and it has
    to hold **whatever the hold record holds**.
    """

    def _parked_seat_with_a_live_scroll(self, tmp_path, **overrides) -> Run:
        runs_dir = tmp_path / ".brr" / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        meta = resource_hold.build(
            reason="turn_ended", provider="claude",
            resume_condition=resource_hold.RESUME_ANY,
            native_session_id="5196fef7-a011-4ebd-87ab-b70193b3ba44",
            resume_kind=resource_hold.RESUME_NATIVE,
        )
        meta.update(overrides)
        task = Run(
            id="run-held-1", event_id="evt-lead", body="",
            status=resource_hold.RUN_STATUS,
        )
        task.meta["resource_hold"] = meta
        task.save(runs_dir)
        # The premise of the proof, asserted rather than assumed.
        assert task.meta["resource_hold"]["native_session_id"]
        assert task.meta["resource_hold"]["resume_kind"] == resource_hold.RESUME_NATIVE
        return task

    def _handover_target(self, tmp_path, *, stamped: bool):
        """A handover event in the real shape — optionally already stamped."""
        inbox_dir = tmp_path / ".brr" / "inbox"
        event = _real_respawn_event(inbox_dir)
        protocol.update_event_meta(event, handover=True)
        if not stamped:
            protocol.update_event_meta(
                event, resume_native_session_id=None, resume_native_provider=None,
            )
        path = Path(event["_path"])
        return daemon._DispatchTarget(
            event=protocol._read_event(path), repo_root=tmp_path,
            inbox_dir=inbox_dir, responses_dir=tmp_path / ".brr" / "responses",
            repo_label="home",
        )

    @pytest.mark.parametrize("stamped", [False, True])
    def test_a_handover_release_leaves_no_stamp_anywhere(self, tmp_path, stamped):
        """Park a seat holding a live scroll, release it with a handover.

        Not "it started without one": the hold record *has* a session id and
        a ``RESUME_NATIVE`` kind, which is the exact state that re-derived
        the stamp in production — and the ``stamped=True`` leg additionally
        starts from an event that is already carrying one.
        """
        seat = self._parked_seat_with_a_live_scroll(tmp_path)
        target = self._handover_target(tmp_path, stamped=stamped)
        assert bool(target.event.get("resume_native_session_id")) is stamped

        survivors = daemon._handle_resource_held_events([target], None)

        assert len(survivors) == 1
        assert "resume_native_session_id" not in target.event
        assert "resume_native_provider" not in target.event
        on_disk = protocol._read_event(Path(target.event["_path"]))
        assert on_disk.get("resume_native_session_id") is None
        assert on_disk.get("resume_native_provider") is None
        persisted = Run.from_file(tmp_path / ".brr" / "runs" / seat.id / "run.md")
        assert persisted.meta["resource_hold"]["released"] is True

    def test_an_ordinary_correspondent_still_wakes_warm(self, tmp_path):
        """The control arm — the fix must not cool every other release."""
        self._parked_seat_with_a_live_scroll(tmp_path)
        inbox_dir = tmp_path / ".brr" / "inbox"
        inbox_dir.mkdir(parents=True, exist_ok=True)
        path = inbox_dir / "evt-human.md"
        path.write_text(
            "---\nid: evt-human\nsource: telegram\nstatus: pending\n---\nhi\n",
            encoding="utf-8",
        )
        target = daemon._DispatchTarget(
            event=protocol._read_event(path), repo_root=tmp_path,
            inbox_dir=inbox_dir, responses_dir=tmp_path / ".brr" / "responses",
            repo_label="home",
        )

        daemon._handle_resource_held_events([target], None)

        assert pending_resume.peek(tmp_path / ".brr")["session_id"] == (
            "5196fef7-a011-4ebd-87ab-b70193b3ba44"
        )


# ── mechanism 1's other face: the park that swallowed the succession ─


class TestAHandoverSurvivesTheParkOnEitherSide:
    """#2016 fixed the arm-time defer and believed the post-arm filter was fine.

    It was not. The post-arm filter bails on
    ``_HOLD_ACCUMULATE_ONLY_SOURCES`` — which contains ``schedule``, which is
    what a real handover's inherited source actually says. So a handover
    queued *after* a park was armed was deferred as routine mail, exactly
    like the one queued before it.
    """

    def _meta(self, cond, reason="turn_ended"):
        return resource_hold.build(
            reason=reason, provider="claude", resume_condition=cond,
        )

    def test_the_predicate_passes_every_hold_including_the_walls(self):
        handover = {"source": "schedule", "handover": True}
        for cond in (
            resource_hold.RESUME_ANY,
            resource_hold.RESUME_STRANDS,
            resource_hold.RESUME_OPERATOR,
            resource_hold.RESUME_REFILL,
            resource_hold.RESUME_RESET,
        ):
            assert resource_hold.handover_event_releases(self._meta(cond), handover)
        assert resource_hold.handover_event_releases(
            self._meta(
                resource_hold.RESUME_OPERATOR,
                resource_hold.REASON_QUOTA_EXHAUSTED,
            ),
            handover,
        )
        # A plain tick in the same clothes is still a tick.
        assert not resource_hold.handover_event_releases(
            self._meta(resource_hold.RESUME_ANY), {"source": "schedule"},
        )

    def test_a_handover_queued_before_the_park_is_not_deferred_at_arm_time(self):
        """``_finalize_resource_hold``'s ``keep_pending`` — #2016's half, rekeyed."""
        meta = self._meta(resource_hold.RESUME_ANY)
        assert resource_hold.handover_event_releases(
            meta, {"source": "telegram", "handover": "true"},
        )

    def test_a_handover_arriving_after_the_park_releases_it(self, tmp_path):
        runs_dir = tmp_path / ".brr" / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        task = Run(
            id="run-held-1", event_id="evt-lead", body="",
            status=resource_hold.RUN_STATUS,
        )
        task.meta["resource_hold"] = resource_hold.build(
            reason=resource_hold.REASON_QUOTA_EXHAUSTED, provider="claude",
            resume_condition=resource_hold.RESUME_OPERATOR,
            native_session_id="held-thread-1",
            resume_kind=resource_hold.RESUME_NATIVE,
        )
        task.save(runs_dir)
        inbox_dir = tmp_path / ".brr" / "inbox"
        event = _real_respawn_event(inbox_dir)
        protocol.update_event_meta(event, handover=True)
        target = daemon._DispatchTarget(
            event=protocol._read_event(Path(event["_path"])), repo_root=tmp_path,
            inbox_dir=inbox_dir, responses_dir=tmp_path / ".brr" / "responses",
            repo_label="home",
        )

        survivors = daemon._handle_resource_held_events([target], None)

        # Not accumulated — dispatched, through a wall, with no scroll.
        assert len(survivors) == 1
        assert survivors[0] is target
        reread = protocol._read_event(Path(event["_path"]))
        assert reread.get("defer_reason") is None
        assert reread.get("resume_native_session_id") is None
        persisted = Run.from_file(runs_dir / "run-held-1" / "run.md")
        assert persisted.meta["resource_hold"]["released"] is True


# ── brnrd#2023: the claim the daemon owns ───────────────────────────


class TestTheScrollIsUnspeakableFromAnEvent:
    """The structural half. Four defects, one surface.

    M1/M3 and the mint leak were three *writers* of one frontmatter key. The
    surface was that the key could be written onto an event at all:
    ``Run.from_event`` copies every unreserved key onto run meta, so every
    event-minting path in the daemon was a fresh candidate for the same bug,
    and the enumeration that closes them is a grep. It had already failed
    twice — #2016 closed one, #2022 found three more.

    So the claim moved off the event: one record per seat, armed by a
    release, consumed once by the dispatch that leads.
    """

    def test_a_forged_event_cannot_claim_a_transcript(self):
        """The whole point, in one assertion.

        Anything with write access to `.brr/inbox` — which is every run
        environment — could previously drop a file naming a session id and be
        handed that Shell transcript. The key does not survive
        `Run.from_event` any more.
        """
        forged = {
            "id": "evt-forged", "source": "telegram", "body": "hi",
            "resume_native_session_id": "someone-elses-thread",
            "resume_native_provider": "claude",
        }

        task = Run.from_event(forged, {})

        assert "resume_native_session_id" not in task.meta
        assert "resume_native_provider" not in task.meta

    def test_the_claim_is_one_shot(self, tmp_path):
        pending_resume.arm(
            tmp_path, session_id="sess-1", provider="claude",
            conversation_key="cloud:telegram:1:", from_run="run-a",
        )

        first = pending_resume.consume(tmp_path, conversation_key="cloud:telegram:1:")
        second = pending_resume.consume(tmp_path, conversation_key="cloud:telegram:1:")

        assert first["session_id"] == "sess-1"
        assert second is None
        assert pending_resume.peek(tmp_path) is None

    def test_a_second_arm_replaces_rather_than_queues(self, tmp_path):
        """One seat, one claim — the invariant the spray could not express."""
        pending_resume.arm(
            tmp_path, session_id="old", provider="claude",
            conversation_key="seat",
        )
        pending_resume.arm(
            tmp_path, session_id="new", provider="claude",
            conversation_key="seat",
        )

        assert pending_resume.consume(tmp_path, conversation_key="seat")["session_id"] == "new"
        assert pending_resume.consume(tmp_path, conversation_key="seat") is None

    def test_a_strand_cannot_take_the_seats_scroll(self, tmp_path):
        """A child shares the repo's `.brr`; it does not share the thread.

        Its dispatch runs under `run:<parent>`, so the guard is the
        conversation key — and the claim is *left armed*, because taking it
        away from the seat that is owed it would turn a mis-route into a
        silent cold boot for somebody else.
        """
        pending_resume.arm(
            tmp_path, session_id="sess-seat", provider="claude",
            conversation_key="cloud:telegram:1:", from_run="run-seat",
        )

        stolen = pending_resume.consume(tmp_path, conversation_key="run:run-seat")

        assert stolen is None
        assert pending_resume.peek(tmp_path)["session_id"] == "sess-seat"
        assert pending_resume.consume(
            tmp_path, conversation_key="cloud:telegram:1:",
        )["session_id"] == "sess-seat"

    def test_an_unconsumed_claim_expires(self, tmp_path, monkeypatch):
        """A claim nobody took names a process that is long gone.

        This is `evt-…-2jb8`'s shape with the event removed: the stamp that
        sat in a drawer through a host suspend and fired a day later. A claim
        cannot sit that long.
        """
        pending_resume.arm(
            tmp_path, session_id="sess-old", provider="claude",
            conversation_key="seat",
        )
        later = time.time() + pending_resume.MAX_AGE_SECONDS + 1
        monkeypatch.setattr(pending_resume.time, "time", lambda: later)

        assert pending_resume.consume(tmp_path, conversation_key="seat") is None

    def test_nothing_to_arm_is_not_an_error(self, tmp_path):
        """No session id is an honest cold boot, and needs no record."""
        assert pending_resume.arm(
            tmp_path, session_id="", provider="claude", conversation_key="seat",
        ) is None
        assert pending_resume.peek(tmp_path) is None
        assert pending_resume.clear(tmp_path) is False

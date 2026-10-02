"""Event writer/reader seams: real producers driven into real readers.

Each test names the existing scaffold it reuses and what it adds.
Zero here means zero model tokens; every case does file I/O.
"""

from __future__ import annotations

from pathlib import Path

from brr import account, daemon, protocol
from brr.gates import runtime
from brr.run import Run


# ── 1. cross-repo: person mail reaches the seat, the child edge does not ──
# Reuses the _ctx scaffold of tests/test_the_room_next_door.py (which drives
# schedule and cloud events). Adds the spawn-family sources.

def _ctx(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    return account.resolve_context(
        repo, {"home.path": str(tmp_path / "home"), "repo.label": "o/seat"},
    )


def test_foreign_person_event_reaches_seat_but_foreign_child_edge_does_not(tmp_path):
    ctx = _ctx(tmp_path)
    inbox = tmp_path / ".brr" / "inbox"
    own = protocol.create_event(inbox, "schedule", "wire", status="processing")
    person = protocol.create_event(inbox, "cloud", "hi", repo_label="o/elsewhere")
    done = protocol.create_event(
        inbox, "spawn_completed", "child done", repo_label="o/elsewhere",
    )
    steer = protocol.create_event(
        inbox, "dispatch_message", "steer", repo_label="o/elsewhere",
    )
    view = {
        e["id"]: e for e in daemon._pending_events_for_agent(
            inbox, own.stem, account_context=ctx, repo_label="o/seat",
        )
    }
    assert view[person.stem]["foreign_repo"] == "o/elsewhere"
    assert done.stem not in view
    assert steer.stem not in view
    # Reading must not retire or stamp anything: all three stay pending on disk.
    assert {e["id"] for e in protocol.list_pending(inbox)} >= {
        person.stem, done.stem, steer.stem,
    }


# ── 2. a park accumulates mail without retiring it ───────────────────────
# Replaces the first draft of this case (hand-built hold meta). Reuses the
# _park/_target/_runs_dir scaffold of tests/test_the_seat_that_stays.py, which
# already drives accumulate-then-release for a *tick* (and asserts only the
# defer keys). Missing corner added here: the accumulated letter's `status`
# stays `pending` the whole way, and after the release the *fresh seat's own
# selector and dispatch list* both see it. Producer: _handle_resource_held_events
# -> _apply_resource_hold_resume. Not driven: the arming path
# (_finalize_resource_hold) and a real await/wake.

def test_park_keeps_mail_pending_until_a_successor_seat_can_see_it(tmp_path):
    from test_the_seat_that_stays import _hold, _park, _runs_dir, _target
    from brr import resource_hold

    _park(tmp_path, condition=resource_hold.RESUME_REFILL,
          reason=resource_hold.REASON_QUOTA_STARVED)
    tick = _target(tmp_path, eid="evt-tick", source="schedule",
                   conversation_key="schedule:the-wire-round")
    inbox = tick.inbox_dir
    assert daemon._handle_resource_held_events([tick], None) == []

    held = protocol._read_event(inbox / "evt-tick.md")
    assert _hold(tmp_path)["accumulated_event_ids"] == ["evt-tick"]
    assert held["status"] == "pending"            # parked, never retired
    assert held["defer_reason"] == "resource_hold"
    assert "evt-tick" in {e["id"] for e in protocol.list_pending(inbox)}
    assert "evt-tick" not in {e["id"] for e in protocol.list_dispatchable(inbox)}

    msg = _target(tmp_path, eid="evt-msg", source="cloud",
                  conversation_key="cloud:telegram:1:")
    seat = Run.from_file(_runs_dir(tmp_path) / "run-seat-A" / "run.md")
    daemon._apply_resource_hold_resume(
        _runs_dir(tmp_path), inbox, seat, msg.event, by="refill",
    )

    after = protocol._read_event(inbox / "evt-tick.md")
    assert after["status"] == "pending" and "defer_reason" not in after
    assert "evt-tick" in {e["id"] for e in protocol.list_dispatchable(inbox)}
    # The successor's own selector, from the release trigger's point of view.
    view = {e["id"] for e in daemon._pending_events_for_agent(inbox, "evt-msg")}
    assert "evt-tick" in view


# ── 3. an invalid also: burst changes nothing and sends nothing ──────────
# Reuses the _burst_fixture/_burst_event scaffold of tests/test_outbox.py
# (copied shape: telegram events, one correspondent). Replaces its
# single-bad-id check with a valid-then-invalid list, and adds a gate-call
# recorder: gates.runtime.deliver_stream is the one reader that sends.

def test_invalid_also_burst_retires_nothing_and_the_gate_sends_nothing(
    tmp_path, monkeypatch,
):
    brr_dir = tmp_path / ".brr"
    inbox, responses = brr_dir / "inbox", brr_dir / "responses"
    inbox.mkdir(parents=True)
    own = protocol.create_event(
        inbox, "telegram", "task", status="processing",
        telegram_user_id="42", telegram_chat_id="42",
    )
    outbox = brr_dir / "outbox" / own.stem
    outbox.mkdir(parents=True)

    def mk(body, **kw):
        return protocol.create_event(
            inbox, "telegram", body, status="processing",
            telegram_user_id="42", telegram_chat_id="42", **kw,
        )

    lead, good = mk("one"), mk("two")
    (outbox / "reply.md").write_text(
        f"---\nevent: {lead.stem}\nalso: {good.stem}, evt-missing\n---\nack\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(daemon.updates, "emit", lambda brr, pkt: None)
    emit = daemon._WorkerEmit(brr_dir=brr_dir, conversation_key="", event_id=own.stem)
    task = Run(id="run-1", event_id=own.stem, body="task", source="telegram")

    assert daemon._drain_outbox(emit, task, responses, own.stem, outbox, inbox) == 0

    for path in (lead, good):                       # valid sibling untouched too
        assert protocol._read_event(path)["status"] == "processing"
        assert protocol.list_partials(responses, path.stem) == []
    notices = daemon._read_outbox_notices(outbox)
    assert [n["kind"] for n in notices] == ["refused"]

    sent: list[tuple[str, str]] = []
    runtime.deliver_stream(
        inbox, responses, "telegram",
        lambda ev, body: sent.append((ev["id"], body)), brr_dir=brr_dir,
    )
    assert sent == []                               # recorder: no gate call


# ── 4. mail arriving during finalization stays eligible ──────────────────
# Producer: daemon._set_event_run_outcome (the finalization write). Reader:
# protocol.list_dispatchable. Fixture limit: the worker tail is not run; the
# interleaving is a create_event between the run and its outcome write.

def test_event_arriving_before_the_outcome_write_stays_dispatchable(tmp_path):
    inbox = tmp_path / ".brr" / "inbox"
    lead = protocol.create_event(inbox, "cloud", "lead", status="processing")
    arrived = protocol.create_event(inbox, "cloud", "arrived mid-finalize")

    assert daemon._set_event_run_outcome(protocol._read_event(lead), "done")

    ev = protocol._read_event(lead)
    assert (ev["status"], ev["run_outcome"]) == ("done", "done")
    assert [e["id"] for e in protocol.list_dispatchable(inbox)] == [arrived.stem]


# ── set_status writes the disk's status line, not the caller's cache ─────

def test_set_status_survives_a_stale_cached_status(tmp_path):
    inbox = tmp_path / "inbox"
    path = protocol.create_event(inbox, "cloud", "x", run_status="pending")
    fresh = protocol._read_event(path)
    stale = dict(fresh)
    protocol.set_status(fresh, "processing")
    protocol.set_status(stale, "done")              # cache still says pending
    assert protocol._read_event(path)["status"] == "done"
    assert "run_status: pending" in path.read_text(encoding="utf-8")


# ── same-repo and targeted child traffic is not suppressed by F1 ─────────

def test_same_repo_and_edge_targeted_internal_traffic_stays_visible(tmp_path):
    ctx = _ctx(tmp_path)
    inbox = tmp_path / ".brr" / "inbox"
    own = protocol.create_event(inbox, "schedule", "wire", status="processing")
    mine = protocol.create_event(inbox, "spawn_completed", "done", repo_label="o/seat")
    bare = protocol.create_event(inbox, "spawn_completed", "done, unlabelled")
    view = {
        e["id"] for e in daemon._pending_events_for_agent(
            inbox, own.stem, account_context=ctx, repo_label="o/seat",
        )
    }
    assert {mine.stem, bare.stem} <= view
    # A strand still reads its parent's `to:` steer (same repo label).
    child = protocol.create_event(inbox, "spawn", "child", status="processing")
    steer = protocol.create_event(
        inbox, "dispatch_message", "steer", repo_label="o/seat",
        spawn_message_for_event=child.stem,
    )
    sview = {
        e["id"] for e in daemon._pending_events_for_agent(
            inbox, child.stem, strand=True, account_context=ctx, repo_label="o/seat",
        )
    }
    assert sview == {steer.stem}


def test_every_internal_source_stays_in_its_own_room(tmp_path):
    ctx = _ctx(tmp_path)
    inbox = tmp_path / ".brr" / "inbox"
    own = protocol.create_event(inbox, "schedule", "wire", status="processing")
    ids = {
        src: protocol.create_event(inbox, src, "x", repo_label="o/elsewhere").stem
        for src in sorted(protocol.INTERNAL_SOURCES)
    }
    view = {
        e["id"] for e in daemon._pending_events_for_agent(
            inbox, own.stem, account_context=ctx, repo_label="o/seat",
        )
    }
    assert view.isdisjoint(ids.values())


# ── observed_by: rendered ≠ handled ≠ retired ────────────────────────────
# Producer: _pending_events_for_agent (stamp). Reader: _retire_internal_event.

def test_only_a_rendered_completion_is_retired_with_its_parent(tmp_path):
    inbox = tmp_path / ".brr" / "inbox"
    responses = tmp_path / ".brr" / "responses"
    lead = protocol.create_event(inbox, "cloud", "lead", status="processing")
    seen = protocol.create_event(
        inbox, "spawn_completed", "a", spawn_parent_run_id="run-P",
    )
    unseen = protocol.create_event(
        inbox, "spawn_completed", "b", spawn_parent_run_id="run-P",
    )
    # Render only `seen`: stamp it the way the selector does, by running the
    # selector before `unseen` exists on disk is not possible, so render both
    # and then clear the stamp on `unseen` to model a tail-of-run arrival.
    daemon._pending_events_for_agent(inbox, lead.stem, observer_run_id="run-P")
    assert protocol._read_event(seen)["observed_by"] == "run-P"
    protocol.update_event_meta(protocol._read_event(unseen), observed_by=None)

    daemon._retire_internal_event(
        protocol._read_event(lead), responses, inbox_dir=inbox, run_id="run-P",
    )
    assert protocol._read_event(seen)["status"] == "delivered"
    assert protocol._read_event(unseen)["status"] == "pending"   # survives

    # A parked run retires nothing, even a rendered completion.
    again = protocol.create_event(
        inbox, "spawn_completed", "c", spawn_parent_run_id="run-P",
    )
    daemon._pending_events_for_agent(inbox, lead.stem, observer_run_id="run-P")
    daemon._retire_internal_event(
        protocol._read_event(lead), responses, inbox_dir=inbox,
        run_id="run-P", parked=True,
    )
    assert protocol._read_event(again)["status"] == "pending"


# ── set_status compatibility: missing / malformed status ─────────────────

def test_set_status_on_missing_or_odd_status_lines(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    bare = inbox / "evt-bare.md"
    bare.write_text("---\nid: evt-bare\nsource: cloud\n---\nbody status: x\n")
    ev = protocol._read_event(bare)
    protocol.set_status(ev, "done")                 # no status line: no write
    assert bare.read_text() == (
        "---\nid: evt-bare\nsource: cloud\n---\nbody status: x\n"
    )
    odd = inbox / "evt-odd.md"
    odd.write_text("---\nid: evt-odd\nsource: cloud\nstatus: weird\n---\nstatus: body\n")
    protocol.set_status(protocol._read_event(odd), "noted")
    assert protocol._read_event(odd)["status"] == "noted"
    assert odd.read_text().endswith("---\nstatus: body\n")   # body untouched

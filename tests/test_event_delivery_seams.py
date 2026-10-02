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
# Producer: daemon._defer_pending_siblings_after_failure (the call
# _finalize_resource_hold makes) + resource_hold.accumulate_event. Reader:
# protocol.list_dispatchable and daemon._undefer_held_event. Fixture limit:
# the hold meta is built directly; the arming path itself is not run.

def test_park_defers_mail_but_never_retires_it_until_a_successor_handles_it(tmp_path):
    from brr import resource_hold

    inbox = tmp_path / ".brr" / "inbox"
    lead = protocol.create_event(inbox, "cloud", "lead", status="processing")
    late = protocol.create_event(inbox, "cloud", "late mail")

    deferred = daemon._defer_pending_siblings_after_failure(
        inbox, lead_event_id=lead.stem, run_id="run-1", seconds=3600,
        reason="resource_hold",
    )
    assert deferred == [late.stem]
    meta = resource_hold.accumulate_event({}, late.stem)
    assert meta["accumulated_event_ids"] == [late.stem]
    # De-duplicated across ticks.
    assert resource_hold.accumulate_event(meta, late.stem) == meta

    ev = protocol._read_event(late)
    assert ev["status"] == "pending"          # not done, not noted
    assert ev["defer_reason"] == "resource_hold"
    assert late.stem in {e["id"] for e in protocol.list_pending(inbox)}
    assert late.stem not in {e["id"] for e in protocol.list_dispatchable(inbox)}

    daemon._undefer_held_event(inbox, late.stem)
    ev = protocol._read_event(late)
    assert ev["status"] == "pending"
    assert "defer_until" not in ev and "defer_reason" not in ev
    assert late.stem in {e["id"] for e in protocol.list_dispatchable(inbox)}


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

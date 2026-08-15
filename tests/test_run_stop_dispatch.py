"""The daemon half of the user-side stop affordance (#476 wyrd §3).

The load-bearing claim under test: a stop tapped in a browser reaches the
*same* kill path the ``stop:`` outbox verb reaches (PR #461), rather than a
second one written alongside it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from brr import daemon, protocol, run_stop_request, runner
from brr.gates import cloud


@pytest.fixture(autouse=True)
def _clean_registries():
    with daemon._run_controls_lock:
        daemon._run_controls.clear()
    with runner._proc_lock:
        runner._active_procs.clear()
    yield
    with daemon._run_controls_lock:
        daemon._run_controls.clear()
    with runner._proc_lock:
        runner._active_procs.clear()


# ── the local ack ledger ────────────────────────────────────────────


def test_unhandled_filters_out_already_dispatched_stops(tmp_path):
    """Delivery and acknowledgement ride the same endpoint one tick apart, so
    the server re-serves a stop we already killed. Acting on it twice would
    kill a second run that inherited the handle."""
    served = [
        {"request_id": "stopreq-1", "run_id": "run-a"},
        {"request_id": "stopreq-2", "run_id": "run-b"},
    ]
    assert len(run_stop_request.unhandled(tmp_path, served)) == 2

    run_stop_request.record_consumed(tmp_path, "stopreq-1")
    remaining = run_stop_request.unhandled(tmp_path, served)
    assert [row["request_id"] for row in remaining] == ["stopreq-2"]


def test_consumed_ledger_clears_only_what_the_server_acked(tmp_path):
    run_stop_request.record_consumed(tmp_path, "stopreq-1")
    run_stop_request.record_consumed(tmp_path, "stopreq-2")
    run_stop_request.clear_consumed(tmp_path, ["stopreq-1"])
    assert run_stop_request.consumed_ids(tmp_path) == ["stopreq-2"]


def test_unhandled_ignores_malformed_rows(tmp_path):
    served = [{"request_id": "", "run_id": "run-a"}, {"run_id": "run-b"}, "junk"]
    assert run_stop_request.unhandled(tmp_path, served) == []


# ── the dispatch reaches the shared kill path ───────────────────────


def test_dispatch_reaches_the_same_kill_path_as_the_stop_verb(tmp_path, monkeypatch):
    """A user stop kills by invocation-label prefix, exactly as `stop:` does —
    `runner.kill_matching`, not a second mechanism."""
    daemon._register_run_control("evt-resident", None)
    daemon._bind_run_control("evt-resident", "run-resident")

    killed: list[str] = []
    monkeypatch.setattr(runner, "kill_matching", lambda prefix: killed.append(prefix) or True)

    cloud._dispatch_run_stops(
        tmp_path, None, [{"request_id": "stopreq-1", "run_id": "run-resident"}]
    )

    assert killed == ["evt-resident-attempt-"]
    assert daemon._stopped_run_control("evt-resident") is not None
    assert run_stop_request.consumed_ids(tmp_path) == ["stopreq-1"]


def test_dispatch_resolves_a_run_by_either_handle(tmp_path, monkeypatch):
    """The live-runs view names runs by run id; the registry is keyed by event
    id. `_find_run_control` already bridges both — the user path reuses it."""
    daemon._register_run_control("evt-child", "run-parent")
    daemon._bind_run_control("evt-child", "run-child")
    monkeypatch.setattr(runner, "kill_matching", lambda prefix: True)

    cloud._dispatch_run_stops(
        tmp_path, None, [{"request_id": "stopreq-1", "run_id": "evt-child"}]
    )
    assert daemon._stopped_run_control("evt-child") is not None


def test_dispatch_acks_a_stop_for_a_run_that_already_finished(tmp_path, monkeypatch):
    """Nothing to kill is not a failure. Leaving it pending would re-serve the
    stop every tick until its TTL."""
    monkeypatch.setattr(runner, "kill_matching", lambda prefix: True)

    cloud._dispatch_run_stops(
        tmp_path, None, [{"request_id": "stopreq-1", "run_id": "run-long-gone"}]
    )
    assert run_stop_request.consumed_ids(tmp_path) == ["stopreq-1"]


def test_user_stop_records_who_stopped_it(tmp_path, monkeypatch):
    """`stopped_by` is what `_finalize_stopped_run` writes onto the run, so a
    dashboard kill reads as one rather than as an anonymous death."""
    daemon._register_run_control("evt-resident", None)
    daemon._bind_run_control("evt-resident", "run-resident")
    monkeypatch.setattr(runner, "kill_matching", lambda prefix: True)

    cloud._dispatch_run_stops(
        tmp_path, None, [{"request_id": "stopreq-1", "run_id": "run-resident"}]
    )
    control = daemon._find_run_control("run-resident")
    assert control["stopped_by"] == "user"
    assert control["stop_reason"] == "stopped from the dashboard"


# ── authority: the two principals differ ────────────────────────────


def test_a_run_still_cannot_stop_a_run_it_did_not_dispatch():
    """The dispatch-edge rule is untouched by #476. Widening the registry to
    hold resident thoughts must not hand runs a way to kill each other."""
    daemon._register_run_control("evt-resident", None)
    daemon._bind_run_control("evt-resident", "run-resident")

    control = daemon._find_run_control("run-resident")
    # What `_queue_stop_request` checks: a resident thought's control carries
    # no parent run id, so no run's id can ever match it.
    assert control["parent_run_id"] is None


# ── #1389: cancel sweeps the utterance's siblings ───────────────────


_BURST_START = datetime(2026, 8, 15, 10, 38, 0, tzinfo=timezone.utc)


def _write_event(inbox_dir, *, conversation_key, offset_s, **meta):
    """One inbox event, its ``created`` stamp overridden to a precise offset
    from ``_BURST_START`` so the sweep's ±3s window is testable exactly."""
    path = protocol.create_event(
        inbox_dir, "cloud", "hi", conversation_key=conversation_key, **meta,
    )
    ev = protocol._read_event(path)
    created = (_BURST_START + timedelta(seconds=offset_s)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    protocol.update_event_meta(ev, created=created)
    return protocol._read_event(path)


def _status_of(inbox_dir, event_id):
    return protocol._read_event(inbox_dir / f"{event_id}.md")["status"]


def test_user_stop_sweeps_utterance_siblings_and_replies_once(tmp_path, monkeypatch):
    """#1389: the measured pain — a text, five photos, a follow-up text,
    7 events in 3s. Cancelling the run that woke on the lead event should
    retire the other pending events from the same burst, not leave them to
    wake five more runs one at a time. A pending event from a *different*
    conversation, however close in time, is the fluke guardrail — it must
    survive untouched."""
    inbox_dir = tmp_path / "inbox"
    monkeypatch.setattr(runner, "kill_matching", lambda prefix: True)

    anchor = _write_event(inbox_dir, conversation_key="telegram:chat:555", offset_s=0)
    siblings = [
        _write_event(inbox_dir, conversation_key="telegram:chat:555", offset_s=s)
        for s in (0.5, 1.0, 2.0, 2.9)
    ]
    too_late = _write_event(
        inbox_dir, conversation_key="telegram:chat:555", offset_s=6.5
    )
    other_thread = _write_event(
        inbox_dir, conversation_key="telegram:chat:999", offset_s=0.2
    )

    anchor_id = anchor["id"]
    daemon._register_run_control(
        anchor_id, None, parent_conversation_key="telegram:chat:555",
    )
    daemon._bind_run_control(anchor_id, "run-resident")

    cloud._dispatch_run_stops(
        tmp_path, inbox_dir, [{"request_id": "stopreq-1", "run_id": "run-resident"}],
    )

    for ev in siblings:
        assert _status_of(inbox_dir, ev["id"]) == "cancelled"
        swept = protocol._read_event(inbox_dir / f"{ev['id']}.md")
        assert swept["swept_by_run"] == "run-resident"
        assert swept["swept_with_event"] == anchor_id
    assert _status_of(inbox_dir, too_late["id"]) == "pending"
    assert _status_of(inbox_dir, other_thread["id"]) == "pending"
    assert _status_of(inbox_dir, anchor_id) == "done"

    body = protocol.read_response(tmp_path / "responses", anchor_id)
    assert body is not None
    assert "4 sibling events" in body
    assert "resend anything still wanted" in body


def test_no_siblings_no_reply(tmp_path, monkeypatch):
    """An idle conversation's cancel gets no aggregate line — nothing was
    swept, so nothing needs naming."""
    inbox_dir = tmp_path / "inbox"
    monkeypatch.setattr(runner, "kill_matching", lambda prefix: True)

    anchor = _write_event(inbox_dir, conversation_key="telegram:chat:1", offset_s=0)
    anchor_id = anchor["id"]
    daemon._register_run_control(
        anchor_id, None, parent_conversation_key="telegram:chat:1",
    )
    daemon._bind_run_control(anchor_id, "run-resident")

    cloud._dispatch_run_stops(
        tmp_path, inbox_dir, [{"request_id": "stopreq-1", "run_id": "run-resident"}],
    )

    assert not protocol.response_exists(tmp_path / "responses", anchor_id)


def test_stop_verb_never_sweeps_siblings(tmp_path, monkeypatch):
    """The guardrail in test form: the sweep fires only on an explicit
    *user* cancel. A parent stopping its own strand (the ``stop:`` outbox
    verb — ``stopped_by`` is a run id, never the literal ``"user"``) must
    never touch a sibling event, or a strand superseding another would
    start silently eating a correspondent's other pending letters."""
    inbox_dir = tmp_path / "inbox"
    monkeypatch.setattr(runner, "kill_matching", lambda prefix: True)

    anchor = _write_event(inbox_dir, conversation_key="telegram:chat:555", offset_s=0)
    sibling = _write_event(
        inbox_dir, conversation_key="telegram:chat:555", offset_s=1.0,
    )
    anchor_id = anchor["id"]
    daemon._register_run_control(
        anchor_id, "run-parent", parent_conversation_key="telegram:chat:555",
    )
    daemon._bind_run_control(anchor_id, "run-child")
    control = daemon._find_run_control("run-child")

    daemon._apply_run_stop(
        control, inbox_dir, stopped_by="run-parent", reason="superseded",
    )

    assert _status_of(inbox_dir, sibling["id"]) == "pending"
    assert not protocol.response_exists(tmp_path / "responses", anchor_id)


def test_user_stop_of_a_spawned_child_never_sweeps(monkeypatch):
    """The resident-only half of the same guardrail: even a *user*-initiated
    stop does not sweep when the target is a spawned child, not the lead
    resident thought — a strand's own dispatch timestamp has nothing to do
    with when the original utterance arrived."""
    monkeypatch.setattr(runner, "kill_matching", lambda prefix: True)
    daemon._register_run_control(
        "evt-child", "run-parent", parent_conversation_key="telegram:chat:555",
    )
    daemon._bind_run_control("evt-child", "run-child")
    control = daemon._find_run_control("run-child")

    # `inbox_dir=None` proves the guard short-circuits before any inbox
    # lookup would happen for a non-resident target, even under `stopped_by=
    # "user"` — if the sweep guard were wrong, this would raise instead of
    # returning cleanly.
    stage = daemon._apply_run_stop(control, None, stopped_by="user")
    assert stage == "running"

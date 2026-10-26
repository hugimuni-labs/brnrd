"""The daemon half of the user-side stop affordance (#476 wyrd §3).

The load-bearing claim under test: a stop tapped in a browser reaches the
*same* kill path the ``stop:`` outbox verb reaches (PR #461), rather than a
second one written alongside it.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from brr import daemon, protocol, runner
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


# ── the dispatch reaches the shared kill path ───────────────────────


# ── authority: the two principals differ ────────────────────────────


# ── #1389: cancel sweeps the utterance's siblings ───────────────────


_BURST_START = datetime(2026, 8, 15, 10, 38, 0, tzinfo=timezone.utc)

# #1396 — the sweep reuses the weave's own
# `max(burst_window, burst_max_wait)` spread; #1626 moved its stable age to
# the event's whole-second `created` stamp. Read the spread from the daemon
# rather than hardcoding it, so a future retune doesn't quietly detune this
# test's own boundary coverage.
_MAX_SPREAD = max(daemon._BURST_WINDOW_DEFAULT, daemon._BURST_MAX_WAIT_DEFAULT)


def _write_event(inbox_dir, *, conversation_key, offset_s, **meta):
    """One inbox event with both stable age and mtime at the given offset.

    `created` is deliberately whole-second, matching production. Mtime is
    aligned only as the queue key's tiebreaker; the sweep's membership test
    reads the stable first component after #1626.
    """
    path = protocol.create_event(
        inbox_dir, "cloud", "hi", conversation_key=conversation_key, **meta,
    )
    created = _BURST_START + timedelta(seconds=offset_s)
    protocol.update_event_meta(
        protocol._read_event(path),
        created=created.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    epoch = created.timestamp()
    os.utime(path, (epoch, epoch))
    return protocol._read_event(path)


def _status_of(inbox_dir, event_id):
    return protocol._read_event(inbox_dir / f"{event_id}.md")["status"]


def test_user_stop_sweeps_utterance_siblings_and_replies_once(tmp_path, monkeypatch):
    """#1389: the measured pain — a text, five photos, a follow-up text,
    7 events in 3s. Cancelling the run that woke on the lead event should
    retire the other pending events from the same burst, not leave them to
    wake five more runs one at a time. A pending event from a *different*
    conversation, however close in time, is the fluke guardrail — it must
    survive untouched. Offsets straddle the real window boundary
    (`_MAX_SPREAD`, #1396's `max(burst_window, burst_max_wait)` — wider than
    the old private ±3s), including one just inside it, so this actually
    exercises the boundary instead of only its interior."""
    inbox_dir = tmp_path / "inbox"
    monkeypatch.setattr(runner, "kill_matching", lambda prefix: True)

    anchor = _write_event(inbox_dir, conversation_key="telegram:chat:555", offset_s=0)
    siblings = [
        _write_event(inbox_dir, conversation_key="telegram:chat:555", offset_s=s)
        for s in (0.5, 1.0, 5.0, _MAX_SPREAD - 1.0)
    ]
    too_late = _write_event(
        inbox_dir, conversation_key="telegram:chat:555", offset_s=_MAX_SPREAD + 1.0
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


def test_user_stop_of_a_spawned_child_never_sweeps(tmp_path, monkeypatch):
    """The resident-only half of the same guardrail: even a *user*-initiated
    stop does not sweep when the target is a spawned child, not the lead
    resident thought — a strand's own dispatch timestamp has nothing to do
    with when the original utterance arrived.

    #1396 review §6: the previous version of this test passed
    ``inbox_dir=None``, which made the *unrelated* ``inbox_dir is not None``
    clause in ``_apply_run_stop`` short-circuit before the
    ``control.get("parent_run_id") is None`` clause this docstring actually
    claims to guard ever ran — deleting that clause left the old test
    green. This version gives it a real ``inbox_dir`` with a real anchor and
    a real pending sibling in the same conversation, so the sibling
    surviving is genuine evidence the parent_run_id guard fired, not an
    artifact of the sweep never having anything to look at.
    """
    inbox_dir = tmp_path / "inbox"
    monkeypatch.setattr(runner, "kill_matching", lambda prefix: True)

    anchor = _write_event(inbox_dir, conversation_key="telegram:chat:555", offset_s=0)
    sibling = _write_event(
        inbox_dir, conversation_key="telegram:chat:555", offset_s=0.5,
    )
    daemon._register_run_control(
        anchor["id"], "run-parent", parent_conversation_key="telegram:chat:555",
    )
    daemon._bind_run_control(anchor["id"], "run-child")
    control = daemon._find_run_control("run-child")

    stage = daemon._apply_run_stop(control, inbox_dir, stopped_by="user")

    assert stage == "running"
    # The load-bearing assertions: had `parent_run_id is None` not guarded
    # the sweep, the sibling above (same conversation, well inside the
    # window) would have been retired to `cancelled` and the anchor would
    # carry the aggregate reply.
    assert _status_of(inbox_dir, sibling["id"]) == "pending"
    assert not protocol.response_exists(tmp_path / "responses", anchor["id"])

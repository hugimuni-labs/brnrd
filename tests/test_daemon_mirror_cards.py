"""Tests for ``daemon._emit_mirror_cards`` — correspondent mirror stubs (#341).

Card routing follows a run's origin thread; these tests pin the daemon-side
half of the fix: when a chat event from a *different* thread waits in the
inbox while a run holds the single-flight slot, the daemon emits
``mirror_card`` packets addressed to the correspondent's own thread —
first sight, narration change, resolution — and stays silent otherwise.
"""

from __future__ import annotations

from pathlib import Path

from brr import daemon, protocol, updates
from brr.run import Run


def _capture(monkeypatch) -> list[updates.UpdatePacket]:
    packets: list[updates.UpdatePacket] = []
    monkeypatch.setattr(
        updates, "emit", lambda brr_dir, packet: packets.append(packet),
    )
    return packets


def _task(
    conv: str = "schedule:director-tick:",
    source: str = "schedule",
) -> Run:
    return Run(
        id="run-x", event_id="evt-lead", body="x", env="host",
        status="running", source=source, conversation_key=conv,
    )


def _worker_emit(tmp_path: Path) -> daemon._WorkerEmit:
    return daemon._WorkerEmit(
        tmp_path / ".brr", "schedule:director-tick:", "evt-lead",
    )


def _seed_foreign(inbox: Path, **over: object) -> Path:
    meta: dict[str, object] = {
        "telegram_chat_id": 555,
        "telegram_message_id": 99,
    }
    meta.update(over)
    return protocol.create_event(inbox, source="telegram", body="hi", **meta)


def test_mirror_emitted_once_then_on_narration_change(tmp_path, monkeypatch):
    packets = _capture(monkeypatch)
    inbox = tmp_path / "inbox"
    _seed_foreign(inbox)
    task = _task()
    emit = _worker_emit(tmp_path)
    state: dict[str, object] = {"last": "working on it"}

    daemon._emit_mirror_cards(emit, task, "evt-lead", inbox, state)
    assert len(packets) == 1
    p = packets[0]
    assert p.type == "mirror_card"
    assert p.conversation_key == "telegram:555:"
    assert p.payload["status"] == "active"
    assert p.payload["agent_card_text"] == "working on it"
    assert p.payload["origin_conversation_key"] == "schedule:director-tick:"
    assert p.payload["event_meta"]["telegram_chat_id"] == 555
    assert p.payload["event_meta"]["telegram_message_id"] == 99

    # Same narration → no packet spam on the next heartbeat.
    daemon._emit_mirror_cards(emit, task, "evt-lead", inbox, state)
    assert len(packets) == 1

    # Narration change → exactly one update.
    state["last"] = "nearly done"
    daemon._emit_mirror_cards(emit, task, "evt-lead", inbox, state)
    assert len(packets) == 2
    assert packets[1].payload["agent_card_text"] == "nearly done"


def test_mirror_resolves_answered_when_event_folds_in(tmp_path, monkeypatch):
    packets = _capture(monkeypatch)
    inbox = tmp_path / "inbox"
    _seed_foreign(inbox)
    task = _task()
    emit = _worker_emit(tmp_path)
    state: dict[str, object] = {"last": "working"}

    daemon._emit_mirror_cards(emit, task, "evt-lead", inbox, state)
    assert len(packets) == 1

    # The resident folds the event in (event: frontmatter reply → done).
    ev = protocol.list_pending(inbox)[0]
    protocol.set_status(ev, "done")

    daemon._emit_mirror_cards(emit, task, "evt-lead", inbox, state)
    assert len(packets) == 2
    final = packets[1]
    assert final.type == "mirror_card"
    assert final.conversation_key == "telegram:555:"
    assert final.payload["status"] == "answered"
    # Resolved → dropped from tracking, no further packets.
    daemon._emit_mirror_cards(emit, task, "evt-lead", inbox, state)
    assert len(packets) == 2


def test_final_drain_marks_still_pending_event_queued(tmp_path, monkeypatch):
    packets = _capture(monkeypatch)
    inbox = tmp_path / "inbox"
    _seed_foreign(inbox)
    task = _task()
    emit = _worker_emit(tmp_path)
    state: dict[str, object] = {"last": "working"}

    daemon._emit_mirror_cards(emit, task, "evt-lead", inbox, state)
    daemon._emit_mirror_cards(
        emit, task, "evt-lead", inbox, state, final=True,
    )
    assert len(packets) == 2
    assert packets[1].payload["status"] == "queued"


def test_no_mirror_for_runs_own_thread(tmp_path, monkeypatch):
    packets = _capture(monkeypatch)
    inbox = tmp_path / "inbox"
    _seed_foreign(inbox)  # telegram:555:
    task = _task(conv="telegram:555:", source="telegram")
    emit = _worker_emit(tmp_path)

    daemon._emit_mirror_cards(emit, task, "evt-lead", inbox, {"last": "x"})
    assert packets == []  # origin chat already has the real card


def test_no_mirror_for_respawn_or_non_chat_events(tmp_path, monkeypatch):
    packets = _capture(monkeypatch)
    inbox = tmp_path / "inbox"
    # Respawn-origin handoff: becomes its own run, never folded in.
    _seed_foreign(inbox, respawned_by_run="run-y")
    # Non-chat gate source: no mirror rendering exists for it.
    protocol.create_event(inbox, source="github", body="issue comment")
    task = _task()
    emit = _worker_emit(tmp_path)

    daemon._emit_mirror_cards(emit, task, "evt-lead", inbox, {"last": "x"})
    assert packets == []

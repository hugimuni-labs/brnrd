"""Replay old outbox scenarios through daemon2's drain at the wire seam.

The old fixture tree includes legacy account setup and transport internals.
Compare the observable contract here: sends, projected letter states, and
refusal text. This harness can take more SCENARIOS as verbs are ported.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import pytest

from brr import protocol
from brr.daemon2.doors import FileDoor
from brr.daemon2.runtime import Daemon2
from brr.daemon2.seat import Seat
from test_outbox_drain_golden import GOLDEN_DIR, SCENARIOS, _base
from test_halt import _setup as halt_setup


def _notice_text(text: str) -> str:
    text = re.sub(r"evt-\d{10,}-[a-z0-9]{4}", "<EVENT>", text)
    return re.sub(r"<EVT\d+>", "<EVENT>", text)


@pytest.mark.parametrize("name", ["event_reply_with_also", "also_refused"])
def test_also_replays_existing_golden(tmp_path: Path, name: str) -> None:
    brr_dir, inbox, responses, outbox, own_id = _base(tmp_path)
    SCENARIOS[name]["stage"](inbox, outbox, own_id)
    golden = json.loads((GOLDEN_DIR / f"{name}.json").read_text())

    repo = tmp_path / "repo"
    repo.mkdir()
    runtime = Daemon2(repo, tmp_path / "home", runtime_dir=brr_dir)
    runtime.door = FileDoor(inbox, responses, runtime.letters)
    own = runtime.door.get(own_id)
    assert own is not None
    address = runtime.router.route_or_triage(own)
    state = {
        "event": own, "conversation": address.conversation,
        "ask": address.ask, "parent": None, "edge": None,
        "claim": None, "seat": None, "run_id": "run-parent", "outbox": outbox,
        "await": None, "notices": [], "answered": False,
        "runner_name": "fake", "is_child": False,
        "claims": {}, "claim_lock": threading.Lock(),
    }
    runtime._tick(state)

    # The new carrier is a terminal response, whereas the old drain staged
    # an interim partial. One user-visible delivery per accepted burst holds.
    assert len(list(responses.glob("*.md"))) == golden["promoted"]
    sibling_ids = [path.stem for path in inbox.glob("*.md") if path.stem != own_id]
    actual_statuses = sorted(runtime.door.get(eid)["status"] for eid in sibling_ids)
    expected_statuses = sorted(
        re.search(r"^status: (\w+)$", content, re.M).group(1)
        for key, content in golden["tree"].items()
        if key.startswith(".brr/inbox/") and "do the thing" not in content
    )
    assert actual_statuses == expected_statuses
    expected_notices = [row["text"]
                        for key, rows in golden["tree"].items()
                        if key.endswith("/.notices.jsonl") for row in rows]
    assert [_notice_text(row["text"]) for row in state["notices"]] == [
        _notice_text(text) for text in expected_notices]


def test_halt_replays_old_bounce_fixture(tmp_path: Path, monkeypatch) -> None:
    """The old TestDrain input bounces once, then the same file stands."""
    file, outbox, inbox, task, _ = halt_setup(tmp_path, monkeypatch)
    sibling = protocol.create_event(
        inbox, "telegram", "and also this", status="pending",
        telegram_user_id="42", telegram_chat_id="42")
    text = "---\nhalt: true\nreason: spent\nresumable: read the report\n---\nbye\n"
    file("0001-halt.md", text)

    repo = tmp_path / "repo"
    repo.mkdir()
    runtime = Daemon2(repo, tmp_path / "home", runtime_dir=tmp_path / ".brr")
    runtime.door = FileDoor(inbox, tmp_path / ".brr" / "responses", runtime.letters)
    seat = Seat(runtime.seats, task.conversation_key,
                authorize=runtime.authority.allowed)
    seat.start(0, run_id=task.id)
    runtime.letters.ingest(task.event_id, "pending")
    claim = runtime.letters.claim(task.event_id, task.id, 60, now=time.time())
    assert claim is not None
    own = runtime.door.get(task.event_id)
    state = {
        "event": own, "conversation": task.conversation_key,
        "ask": None, "parent": None, "edge": None,
        "claim": claim, "seat": seat, "run_id": task.id, "outbox": outbox,
        "await": None, "notices": [], "answered": False,
        "runner_name": "fake", "is_child": False,
        "claims": {task.event_id: claim}, "claim_lock": threading.Lock(),
        "halted": False,
    }
    runtime._tick(state)
    assert not state["answered"]
    assert seat.read().state == "running"
    assert "halt bounced" in state["notices"][0]["text"]
    assert sibling.stem.rsplit("-", 1)[-1] in state["notices"][0]["text"]

    file("0002-halt.md", text)
    runtime._tick(state)
    assert state["answered"]
    assert seat.read().state == "ended"
    assert protocol.read_response(runtime.door.responses, task.event_id) == "bye"


@pytest.mark.parametrize("name", [
    "cut_minimal_bolt", "cut_bounced_topicless", "cut_parse_error",
])
def test_cut_replays_existing_golden(tmp_path: Path, name: str) -> None:
    brr_dir, inbox, responses, outbox, own_id = _base(tmp_path)
    SCENARIOS[name]["stage"](inbox, outbox, own_id)
    golden = json.loads((GOLDEN_DIR / f"{name}.json").read_text())
    repo = tmp_path / "repo"
    repo.mkdir()
    runtime = Daemon2(repo, tmp_path / "home", runtime_dir=brr_dir)
    runtime.door = FileDoor(inbox, responses, runtime.letters)
    own = runtime.door.get(own_id)
    assert own is not None
    address = runtime.router.route_or_triage(own)
    seat = Seat(runtime.seats, address.conversation,
                authorize=runtime.authority.allowed)
    seat.start(0, run_id="run-parent")
    runtime.letters.ingest(own_id, "pending")
    claim = runtime.letters.claim(own_id, "run-parent", 60, now=time.time())
    assert claim is not None
    state = {
        "event": runtime.door.get(own_id), "conversation": address.conversation,
        "ask": address.ask, "parent": None, "edge": None,
        "claim": claim, "seat": seat, "run_id": "run-parent", "outbox": outbox,
        "await": None, "notices": [], "answered": False,
        "runner_name": "fake", "is_child": False,
        "claims": {own_id: claim}, "claim_lock": threading.Lock(), "cut": False,
    }
    runtime._tick(state)
    assert len(list(responses.glob("*.md"))) == golden["promoted"]
    expected = [row["text"] for key, rows in golden["tree"].items()
                if key.endswith("/.notices.jsonl") for row in rows]
    assert [row["text"] for row in state["notices"]] == expected

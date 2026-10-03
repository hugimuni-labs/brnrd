"""Replay old outbox scenarios through daemon2's drain at the wire seam.

The old fixture tree includes legacy account setup and transport internals.
Compare the observable contract here: sends, projected letter states, and
refusal text. This harness can take more SCENARIOS as verbs are ported.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import pytest

from brr import protocol
from brr.daemon2.doors import FileDoor
from brr.daemon2.runtime import Daemon2
from test_outbox_drain_golden import GOLDEN_DIR, SCENARIOS, _base


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

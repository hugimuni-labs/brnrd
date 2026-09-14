"""The await's resolution against a baseline captured on the unsplit tree.

Move 2c changed what *returns* from ``brnrd await`` (a lease, not a ten-minute
poll) and was forbidden from changing what *resolves* it. This module is that
promise as a file: every scenario below drives ``daemon._resolve_await_state``
— the heartbeat's evaluation of an armed wait — and freezes what it produced
(the portal projection on the resolving tick, the sticky re-projection on the
tick after, the armed record, the correspondent clock, the Shuttle row) under
``tests/fixtures/await_resolution_golden/``. The goldens were captured on
``1354fea5`` (the unsplit drain #1975's own goldens describe), before any of
move 2c existed; the same drive on this tree must reproduce each file exactly.

It deliberately calls ``_resolve_await_state`` rather than
``_write_live_portal_state``: move 5 is reshaping that writer's signature in
parallel, and the resolution is the half this contract is about.

Regenerating (``BRR_AWAIT_GOLDEN_WRITE=1``) is only honest on a tree whose
resolution is the one the golden claims to describe.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import pytest

from brr import daemon, shuttle
from brr.run import Run

GOLDEN_DIR = Path(
    os.environ.get("BRR_AWAIT_GOLDEN_DIR")
    or Path(__file__).parent / "fixtures" / "await_resolution_golden"
)
WRITE = os.environ.get("BRR_AWAIT_GOLDEN_WRITE") == "1"

_ISO_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?"
)


def _armed(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "file": None,
        "timeout_seconds": 1200.0,
        "armed_at": time.time() - 100,
        "generation": "gen-1",
        "resolved": False,
        "armed_pending_ids": [],
    }
    record.update(overrides)
    return record


def _event(event_id: str, source: str, **meta: Any) -> dict[str, Any]:
    return {"id": event_id, "source": source, "created": "2026-09-06T12:00:00Z", **meta}


def _scenario(tmp_path: Path, name: str) -> tuple[dict | None, list[dict]]:
    gate = tmp_path / "gate.log"
    if name == "event_from_a_correspondent":
        return _armed(), [_event("evt-2", "telegram")]
    if name == "event_from_a_strand_returning":
        return _armed(), [
            _event("evt-3", "spawn_completed", spawn_parent_run_id="run-1"),
        ]
    if name == "event_from_a_schedule_firing":
        return _armed(), [_event("evt-4", "schedule")]
    if name == "condition_file_appeared":
        gate.write_text("done\n", encoding="utf-8")
        return _armed(file=str(gate)), []
    if name == "event_outranks_the_file":
        gate.write_text("done\n", encoding="utf-8")
        return _armed(file=str(gate)), [_event("evt-2", "telegram")]
    if name == "timeout_passed":
        return _armed(timeout_seconds=1.0, armed_at=time.time() - 5), []
    if name == "quiet_inside_the_deadline":
        return _armed(), []
    if name == "open_ended_quiet":
        return _armed(timeout_seconds=None), []
    if name == "open_ended_resolves_on_an_event":
        return _armed(timeout_seconds=None), [_event("evt-2", "telegram")]
    if name == "arm_time_snapshot_is_excluded":
        return (
            _armed(armed_pending_ids=["evt-5"]),
            [_event("evt-5", "spawn_completed", spawn_parent_run_id="run-1")],
        )
    if name == "never_armed":
        return None, [_event("evt-2", "telegram")]
    raise AssertionError(name)


SCENARIOS = (
    "event_from_a_correspondent",
    "event_from_a_strand_returning",
    "event_from_a_schedule_firing",
    "condition_file_appeared",
    "event_outranks_the_file",
    "timeout_passed",
    "quiet_inside_the_deadline",
    "open_ended_quiet",
    "open_ended_resolves_on_an_event",
    "arm_time_snapshot_is_excluded",
    "never_armed",
)


def _norm(value: Any, root: str) -> Any:
    if isinstance(value, dict):
        return {k: _norm(v, root) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_norm(v, root) for v in value]
    if isinstance(value, float):
        return "<F>"
    if isinstance(value, str):
        return _ISO_RE.sub("<TS>", value.replace(root, "<TMP>"))
    return value


def _drive(tmp_path: Path, name: str) -> dict[str, Any]:
    armed, pending = _scenario(tmp_path, name)
    task = Run(id="run-1", event_id="evt-1", body="", source="telegram")
    if armed is not None:
        task.meta["await"] = armed
    task.meta["hold_correspondent_at"] = 42.0
    home = tmp_path / "home"
    entity = shuttle.Shuttle.load(home)
    entity.transition("awake", why="event_dispatched", run_id=task.id)
    if armed is not None:
        entity.transition("listening", why="await_armed", run_id=task.id)

    first = daemon._resolve_await_state(
        task, pending, outbox_dir=None, shuttle_home=home,
    )
    # The tick after: the same pending set, already answered or not.
    second = daemon._resolve_await_state(
        task, pending, outbox_dir=None, shuttle_home=home,
    )
    after = shuttle.Shuttle.load(home)
    captured = {
        "first_tick": first,
        "next_tick": second,
        "armed_record": task.meta.get("await"),
        "correspondent_clock_moved": task.meta.get("hold_correspondent_at") != 42.0,
        "shuttle": {
            "state": after.state,
            "whys": [row.get("why") for row in after.transitions],
        },
    }
    return _norm(json.loads(json.dumps(captured, default=str)), str(tmp_path))


@pytest.mark.parametrize("name", SCENARIOS)
def test_resolution_reproduces_the_unsplit_baseline(name, tmp_path):
    got = _drive(tmp_path, name)
    path = GOLDEN_DIR / f"{name}.json"
    rendered = json.dumps(got, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if WRITE:
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
        return
    assert path.exists(), f"no golden for {name} — capture on the unsplit tree"
    assert rendered == path.read_text(encoding="utf-8")

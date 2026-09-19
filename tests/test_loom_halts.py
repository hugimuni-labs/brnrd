"""The loom feed shows halts — design-the-four-stops.md.

The maintainer allowed a carry-less exit on one condition (2026-09-19):
*"as long as we clearly display it on the main dashboard."* #2030 shipped
the verb, the ledger, and its two readers; nothing rendered them. These
tests pin the display side, so the condition cannot quietly go unmet again.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from brr import halt_verb, halts
from brr.loom import state


def _halt(home: Path, run: str, *, kind: str, **kw):
    return halts.record(home, run_id=run, kind=kind, reason=kw.pop("reason", "spent"), **kw)


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    (tmp_path / "account").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_no_home_reads_empty_rather_than_raising(tmp_path):
    """The ledger is the display's dependency, not the verb's."""
    assert state.read_halts(None) == {
        "counts": {halt_verb.KIND_CARRIED: 0, halt_verb.KIND_STOPPED: 0, "total": 0},
        "queue": [],
        "queue_total": 0,
        "queue_limit": state.HALT_QUEUE_MAX,
    }


def test_the_two_counts_stay_two(home):
    """A body wearing out and work stopping are different events."""
    _halt(home, "run-a", kind=halt_verb.KIND_CARRIED, carry="keep going")
    _halt(home, "run-b", kind=halt_verb.KIND_CARRIED, carry="keep going")
    _halt(home, "run-c", kind=halt_verb.KIND_STOPPED, resumable="pick it up at the parser")
    facet = state.read_halts(home)
    assert facet["counts"] == {halt_verb.KIND_CARRIED: 2, halt_verb.KIND_STOPPED: 1, "total": 3}


def test_the_queue_is_carry_less_halts_that_left_work(home):
    """Abandoned work *with a stated revival path* is the whole return."""
    _halt(home, "run-carried", kind=halt_verb.KIND_CARRIED, carry="brief", open_items=["x"])
    _halt(home, "run-clean", kind=halt_verb.KIND_STOPPED, resumable="nothing left", open_items=[])
    _halt(home, "run-open", kind=halt_verb.KIND_STOPPED, resumable="start at the drain",
          open_items=["the drain", "the golden"])
    facet = state.read_halts(home)
    assert [row["run"] for row in facet["queue"]] == ["run-open"]
    assert facet["queue"][0]["resumable"] == "start at the drain"
    assert facet["queue"][0]["open_items"] == ["the drain", "the golden"]


def test_newest_first_because_a_reader_works_the_top(home):
    for n in range(3):
        _halt(home, f"run-{n}", kind=halt_verb.KIND_STOPPED, resumable="r", open_items=["i"])
    assert [row["run"] for row in state.read_halts(home)["queue"]] == ["run-2", "run-1", "run-0"]


def test_a_truncated_queue_reports_its_true_length(home):
    """A capped list must never be mistaken for a short one."""
    for n in range(state.HALT_QUEUE_MAX + 5):
        _halt(home, f"run-{n:03d}", kind=halt_verb.KIND_STOPPED, resumable="r", open_items=["i"])
    facet = state.read_halts(home)
    assert len(facet["queue"]) == state.HALT_QUEUE_MAX
    assert facet["queue_total"] == state.HALT_QUEUE_MAX + 5


def test_open_items_are_capped_per_row_and_counted(home):
    _halt(home, "run-many", kind=halt_verb.KIND_STOPPED, resumable="r",
          open_items=[f"item {n}" for n in range(state.HALT_ITEMS_MAX + 4)])
    row = state.read_halts(home)["queue"][0]
    assert len(row["open_items"]) == state.HALT_ITEMS_MAX
    assert row["open_items_total"] == state.HALT_ITEMS_MAX + 4


def test_halt_is_its_own_state_on_a_run_not_an_ended_timestamp(home):
    """Three outcomes share `ended`; only `halt` tells them apart."""
    _halt(home, "run-stopped", kind=halt_verb.KIND_STOPPED, reason="the work ends here",
          resumable="re-read the design", open_items=["one"])
    _halt(home, "run-carried", kind=halt_verb.KIND_CARRIED, reason="body spent", carry="brief")
    index = state._halts_by_run(home)
    assert index["run-stopped"]["kind"] == halt_verb.KIND_STOPPED
    assert index["run-stopped"]["carry"] is False
    assert index["run-stopped"]["resumable"] == "re-read the design"
    assert index["run-stopped"]["open_items"] == 1
    assert index["run-carried"]["kind"] == halt_verb.KIND_CARRIED
    assert index["run-carried"]["carry"] is True


def test_a_run_that_never_halted_reads_null_not_false(home):
    """`null` means no halt recorded — never "ended normally"."""
    _halt(home, "run-a", kind=halt_verb.KIND_STOPPED, resumable="r")
    assert state._halts_by_run(home).get("run-never") is None


def test_the_newest_halt_wins_for_a_resumed_run(home):
    _halt(home, "run-a", kind=halt_verb.KIND_STOPPED, reason="first", resumable="r")
    _halt(home, "run-a", kind=halt_verb.KIND_CARRIED, reason="second", carry="brief")
    assert state._halts_by_run(home)["run-a"]["reason"] == "second"


def test_the_facet_is_in_the_feed_contract(tmp_path):
    assert "halts" in state.KEYS
    built = state.build(tmp_path, None)
    assert tuple(built.keys()) == state.KEYS
    assert built["halts"]["counts"]["total"] == 0


def test_a_corrupt_ledger_line_does_not_take_the_dashboard_down(home):
    _halt(home, "run-a", kind=halt_verb.KIND_STOPPED, resumable="r", open_items=["i"])
    path = halts.ledger_path(home)
    path.write_text(path.read_text() + "{not json\n", encoding="utf-8")
    assert [row["run"] for row in state.read_halts(home)["queue"]] == ["run-a"]

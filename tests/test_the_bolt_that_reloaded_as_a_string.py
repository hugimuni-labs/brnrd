"""#2031 — `task.meta['bolt']` was dict-guarded but reloaded as a JSON string.

`outbox.verbs` writes `bolt` as a dict. Five call sites read it back with
`isinstance(..., dict)`. None of them runs in the process that wrote it —
the interesting reads happen *after* the run that wrote the bolt has ended
— and `bolt` was missing from `run._JSON_META_KEYS`, so `Run.from_file`
handed back a string and every guard took its empty branch.

Nothing raised, nothing logged. **A no-op and a silent failure were
byte-identical**, which is the house failure mode in the daemon's own
readers: a surface that narrows renders as if it hadn't.

These tests are written across a real write/reload boundary — the shape
production actually makes — rather than against an in-memory run, because
an in-memory run never had the bug.
"""

from __future__ import annotations

import pathlib

import pytest

from brr import run_ledger
from brr.run import Run, _JSON_META_KEYS


def _saved(tmp_path: pathlib.Path, bolt: object) -> Run:
    """Write a manifest in one step and read it back in another."""
    run = Run(id="run-x", event_id="evt-1", body="b", source="telegram",
              status="working", conversation_key="t:1:")
    if bolt is not None:
        run.meta["bolt"] = bolt
    run.save(tmp_path)
    reloaded = Run.from_file(next(tmp_path.rglob("*.md")))
    assert reloaded is not None
    return reloaded


CLEAN = {"n": 2, "accepted_at": "2026-09-19T10:00:00Z", "annotated": 0, "attempts": 1}


def test_a_bolt_survives_the_write_reload_boundary_as_a_dict(tmp_path):
    """The whole defect in one assertion."""
    assert isinstance(_saved(tmp_path, CLEAN).meta["bolt"], dict)


def test_the_ledger_reads_the_reloaded_bolt(tmp_path):
    """`_bolt_value` returned None for every cut run that had ended."""
    bolt = _saved(tmp_path, CLEAN).meta.get("bolt")
    assert run_ledger._bolt_value(bolt) == "accepted"


def test_an_annotated_bolt_keeps_its_label(tmp_path):
    bolt = _saved(tmp_path, {**CLEAN, "annotated": 1}).meta.get("bolt")
    assert run_ledger._bolt_value(bolt) == "annotated"


def test_a_clean_bolt_can_absolve_a_run_again(tmp_path):
    """The live consequence, not just the type.

    `daemon.py`'s branch verdict calls a run `absolved` when its bolt is
    clean and `none_unresolved` — *"published nothing through the lane the
    daemon owns"* — otherwise. With the bolt reloading as a string, that
    test could never pass, so runs whose clean bolt absolved them were
    accused instead. This pins the predicate itself.
    """
    bolt = _saved(tmp_path, CLEAN).meta.get("bolt")
    bolt_clean = (
        isinstance(bolt, dict)
        and bolt.get("accepted_at")
        and int(bolt.get("annotated") or 0) == 0
    )
    assert bolt_clean


def test_the_hud_guard_sees_a_dict(tmp_path):
    assert isinstance(_saved(tmp_path, CLEAN).meta.get("bolt"), dict)


def test_bolt_is_allowlisted_beside_the_keys_it_behaves_like(tmp_path):
    assert "bolt" in _JSON_META_KEYS


def test_a_run_with_no_bolt_still_has_none(tmp_path):
    """Absence must stay absence — the fix must not invent a bolt."""
    assert _saved(tmp_path, None).meta.get("bolt") is None


@pytest.mark.parametrize("value", ["not json", "", "cut at noon", "42x"])
def test_a_non_json_bolt_round_trips_unchanged(tmp_path, value):
    """The decode is double-gated: allowlisted key *and* parses to dict/list.

    A manifest carrying something else under `bolt` — handwritten, older,
    corrupt — must come back exactly as written rather than being coerced
    or dropped. This is the half of the fix that protects the readers the
    issue warned about, the ones that might cope with a string form.
    """
    assert _saved(tmp_path, value).meta.get("bolt") == value


def test_a_json_list_bolt_decodes_too(tmp_path):
    """The gate admits lists, so it must not surprise a reader with a string."""
    assert _saved(tmp_path, [1, 2]).meta.get("bolt") == [1, 2]


def test_the_other_allowlisted_keys_are_untouched(tmp_path):
    """A control: `stake` always worked, and must keep working."""
    run = Run(id="run-y", event_id="evt-2", body="b", source="telegram",
              status="working", conversation_key="t:1:")
    run.meta["stake"] = {"window_tokens": 5}
    run.meta["bolt"] = CLEAN
    run.save(tmp_path)
    back = Run.from_file(next(tmp_path.rglob("*.md")))
    assert back.meta["stake"] == {"window_tokens": 5}
    assert back.meta["bolt"] == CLEAN

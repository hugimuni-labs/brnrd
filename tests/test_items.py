"""The warp item space, daemon half (``items.py``) — grammar in lockstep
with the frontend's ``warpGraph.ts``, receipts as derived state, the wake
index, and the row-scoped file edits every verb rests on."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brr import items


def _warp(tmp_path: Path) -> Path:
    root = tmp_path / "surface" / "warp"
    root.mkdir(parents=True)
    return root


def _write(root: Path, item_id: str, text: str) -> Path:
    path = root / f"{item_id}.md"
    path.write_text(text, encoding="utf-8")
    return path


FULL = """# Ship the digest

type: action
topics: loom post
needs: w-3
refs: hugimuni-labs/brnrd#1256
prompt: Build the digest per the signed design.
taken: run-a

Body prose here.
"""


# ── parsing ───────────────────────────────────────────────────────────────


def test_parse_reads_the_full_row_block_and_body(tmp_path: Path):
    root = _warp(tmp_path)
    item = items.parse_item(_write(root, "w-7", FULL))
    assert item is not None
    assert item.id == "w-7"
    assert item.headline == "Ship the digest"
    assert item.type == "action"
    assert item.topics == ["loom", "post"]
    assert item.needs == ["w-3"]
    assert item.taken == ["run-a"]
    assert item.state == "open"
    assert item.prompt == "Build the digest per the signed design."
    assert item.body == "Body prose here."


def test_state_derives_from_receipt_rows(tmp_path: Path):
    root = _warp(tmp_path)
    done = items.parse_item(
        _write(root, "w-1", "# X\n\ntype: decision\ndone: 2026-08-11 run-z\n")
    )
    assert done is not None and done.state == "done"
    retired = items.parse_item(
        _write(root, "w-2", "# X\n\nretired: 2026-08-10 superseded\n")
    )
    assert retired is not None and retired.state == "retired"
    open_item = items.parse_item(_write(root, "w-3", "# X\n"))
    assert open_item is not None and open_item.state == "open"


def test_row_block_ends_at_the_first_unrecognized_line(tmp_path: Path):
    root = _warp(tmp_path)
    item = items.parse_item(
        _write(root, "w-4", "# X\n\ntype: action\nnot a row\nneeds: w-1\n")
    )
    assert item is not None
    assert item.type == "action"
    assert item.needs == []
    assert "not a row" in item.body


def test_illegal_id_and_index_are_skipped(tmp_path: Path):
    root = _warp(tmp_path)
    _write(root, "index", "# Index\n")
    (root / "UPPER.md").write_text("# Nope\n", encoding="utf-8")
    _write(root, "w-1", "# Real\n\ntype: action\n")
    assert [item.id for item in items.load_items(root)] == ["w-1"]


def test_load_items_orders_numerically_then_named(tmp_path: Path):
    root = _warp(tmp_path)
    for item_id in ("w-10", "w-2", "a-named-one"):
        _write(root, item_id, "# X\n\ntype: action\n")
    assert [item.id for item in items.load_items(root)] == ["w-2", "w-10", "a-named-one"]


# ── scanning + allocation ─────────────────────────────────────────────────


def test_scan_item_ids_two_doors():
    body = "do w-2 and w-10\nitem: hand-named\nprose one-spine-docs stays prose\n"
    assert items.scan_item_ids(body) == ["w-2", "w-10", "hand-named"]


# #1436 facet 2, in two passes:
#
# Pass 1 widened the enumeration separator set by dropping `/` from
# `_SCAN_TOKEN_RE`'s lookbehind — and, unreviewed, that also widened into
# every real path this system uses to refer to an item's own file
# (`surface/warp/w-45.md` — the schedule agenda and this very issue's spec
# both name items that way). A correspondent writing that path is exactly
# the body #1435's source guard leaves ignition-eligible, so the
# over-match was live, not theoretical. Caught in review, run against the
# actual scanner rather than read off the first pass's own report.
#
# Pass 2 (below): a leading `/` is a separator only when the id
# immediately before it was itself accepted (`w-45/w-46`); otherwise it's
# a path segment and the whole candidate is rejected regardless of what
# follows (`surface/warp/w-45.md`, `kb/w-45.md`, a bare URL). A trailing
# `.<ext>` shape disqualifies unconditionally — a filename is never an id,
# including the pre-existing `w-45.md` case, which matched before either
# pass touched this regex; kept `[]` here for one consistent rule rather
# than a path-prefix-dependent split. See `_SCAN_CANDIDATE_RE`'s docstring
# comment in `items.py` for the full discriminator.
@pytest.mark.parametrize(
    "body, expected",
    [
        # The original defect (facet 2's whole reason to exist): a
        # slash-enumeration must not lose everything after the first id.
        ("the w-14/w-45/w-46/w-47 cluster", ["w-14", "w-45", "w-46", "w-47"]),
        ("w-45/w-46", ["w-45", "w-46"]),
        # The rest of the separator set the issue named — none of these
        # were ever broken, but they're asserted alongside the slash so
        # the whole set lives in one table.
        ("w-1,w-2", ["w-1", "w-2"]),
        ("w-1·w-2", ["w-1", "w-2"]),
        ("w-1+w-2", ["w-1", "w-2"]),
        ("w-1&w-2", ["w-1", "w-2"]),
        ("w-1 w-2", ["w-1", "w-2"]),
        (
            "please look at w-45/w-46, w-47·w-48+w-49&w-50",
            ["w-45", "w-46", "w-47", "w-48", "w-49", "w-50"],
        ),
        # The over-match a follow-up review caught: pass 1's widened `/`
        # also matched inside a real path. Must all come back empty.
        ("surface/warp/w-45.md", []),
        ("kb/w-45.md", []),
        ("https://example.com/w-12", []),
        # The pre-existing (pass-1-independent) filename case — resolved
        # here as "never an id", not left as pass 1's inherited behavior.
        ("w-45.md", []),
        # Untouched negatives from the original spec: embedded in a
        # longer token or a hyphen-suffixed path segment.
        ("docs/w-45-notes.md", []),
        ("xw-45", []),
        ("w-450", ["w-450"]),
    ],
)
def test_scan_item_ids_enumeration_and_path_boundary(body, expected):
    assert items.scan_item_ids(body) == expected


def test_scan_item_ids_mixed_prose_with_embedded_negatives():
    """The negatives hold inside a full sentence too, not just in
    isolation — `docs/w-45-notes.md`, `xw-45`, and `w-450` each still
    resolve the way their standalone case does when surrounded by prose."""
    body = (
        "see docs/w-45-notes.md for background, not xw-45, "
        "and w-450 is a different item entirely"
    )
    assert items.scan_item_ids(body) == ["w-450"]


def test_allocate_id_never_reuses_even_done_items(tmp_path: Path):
    root = _warp(tmp_path)
    assert items.allocate_id(root) == "w-1"
    _write(root, "w-3", "# X\n\ndone: 2026-08-01\n")
    _write(root, "w-1", "# Y\n")
    assert items.allocate_id(root) == "w-4"


# ── edits ─────────────────────────────────────────────────────────────────


def test_mark_taken_idempotent_and_inserts_into_the_row_block(tmp_path: Path):
    root = _warp(tmp_path)
    path = _write(root, "w-1", "# X\n\ntype: action\n\nBody.\n")
    assert items.mark_taken(path, "run-a")
    assert not items.mark_taken(path, "run-a")
    assert items.mark_taken(path, "run-b")
    text = path.read_text(encoding="utf-8")
    assert "taken: run-a run-b" in text
    assert text.index("taken:") < text.index("Body.")


def test_mark_done_refuses_a_second_receipt(tmp_path: Path):
    root = _warp(tmp_path)
    path = _write(root, "w-1", "# X\n\ntype: decision\n")
    assert items.mark_done(path, date="2026-08-11", run_id="run-z")
    assert "done: 2026-08-11 run-z" in path.read_text(encoding="utf-8")
    # A second receipt would silently rewrite history — refused.
    assert not items.mark_done(path, date="2026-08-12", run_id="run-q")
    assert not items.mark_retired(path, date="2026-08-12")


def test_mark_retired_records_the_why(tmp_path: Path):
    root = _warp(tmp_path)
    path = _write(root, "w-1", "# X\n\ntype: action\n")
    assert items.mark_retired(path, date="2026-08-11", why="superseded by w-2")
    item = items.parse_item(path)
    assert item is not None and item.state == "retired"
    assert item.retired == "2026-08-11 superseded by w-2"


def test_new_item_text_round_trips(tmp_path: Path):
    root = _warp(tmp_path)
    text = items.new_item_text(
        "Decide the storage shape",
        item_type="decision",
        topics=["loom"],
        needs=["w-2"],
        prompt="Decide.",
        body="The tree cannot hold multi-blockers.",
    )
    item = items.parse_item(_write(root, "w-9", text))
    assert item is not None
    assert item.headline == "Decide the storage shape"
    assert item.type == "decision"
    assert item.topics == ["loom"]
    assert item.needs == ["w-2"]
    assert item.prompt == "Decide."
    assert item.body == "The tree cannot hold multi-blockers."


# ── the wake index ────────────────────────────────────────────────────────


def test_render_index_bands_and_blockers(tmp_path: Path):
    root = _warp(tmp_path)
    _write(root, "w-1", "# Decide the shape\n\ntype: decision\ntopics: loom\n")
    _write(root, "w-2", "# Build it\n\ntype: action\nneeds: w-1\n")
    _write(root, "w-3", "# Done thing\n\ntype: action\ndone: 2026-08-10 run-z\n")
    _write(root, "w-4", "# Dangling\n\ntype: action\nneeds: w-99\n")
    index = items.render_index(root)
    assert index is not None
    lines = index.split("\n")
    assert lines[0] == "ready:"
    # Decisions lead the ready band; the dangling edge never blocks.
    assert "w-1" in lines[1] and "decision" in lines[1]
    assert any("w-4" in line for line in lines[: lines.index("held:")])
    held = lines[lines.index("held:") + 1]
    assert "w-2" in held and "needs w-1" in held
    assert lines[-1].startswith("done recently: w-3 (2026-08-10)")


def test_render_index_empty_warp_is_none(tmp_path: Path):
    assert items.render_index(None) is None
    root = _warp(tmp_path)
    assert items.render_index(root) is None


def test_a_done_blocker_frees_its_dependent(tmp_path: Path):
    root = _warp(tmp_path)
    _write(root, "w-1", "# A\n\ntype: decision\ndone: 2026-08-01\n")
    _write(root, "w-2", "# B\n\ntype: action\nneeds: w-1\n")
    index = items.render_index(root)
    assert index is not None
    assert "held:" not in index
    assert "needs" not in index.split("done recently")[0]


# ── goal node kind (design-goal-oriented-engineering.md) ───────────────────


def test_parse_goal_reads_the_three_free_text_rows(tmp_path: Path):
    root = _warp(tmp_path)
    item = items.parse_item(
        _write(
            root,
            "g-1",
            "# Grow attention\n\ntype: goal\nmetric: tickets bought\n"
            "target: 1000/mo\nhorizon: Q4 2026\n",
        )
    )
    assert item is not None
    assert item.type == "goal"
    assert item.metric == "tickets bought"
    assert item.target == "1000/mo"
    assert item.horizon == "Q4 2026"
    assert item.state == "open"


def test_advances_row_same_list_grammar_as_needs(tmp_path: Path):
    root = _warp(tmp_path)
    item = items.parse_item(
        _write(root, "w-1", "# Ship it\n\ntype: action\nadvances: g-1 g-2\n")
    )
    assert item is not None
    assert item.advances == ["g-1", "g-2"]


def test_advances_row_legal_on_a_goal_itself(tmp_path: Path):
    """Sub-goal edges parse; the module gives them no special treatment
    beyond storage — see the module docstring."""
    root = _warp(tmp_path)
    item = items.parse_item(
        _write(root, "g-2", "# Sub-goal\n\ntype: goal\nadvances: g-1\n")
    )
    assert item is not None
    assert item.advances == ["g-1"]


def test_allocate_id_goal_counter_is_separate_from_item_counter(tmp_path: Path):
    root = _warp(tmp_path)
    assert items.allocate_id(root, "goal") == "g-1"
    _write(root, "g-1", "# X\n\ntype: goal\n")
    _write(root, "w-1", "# Y\n\ntype: action\n")
    # The two counters never collide regardless of mint order.
    assert items.allocate_id(root) == "w-2"
    assert items.allocate_id(root, "goal") == "g-2"


def test_load_items_orders_goals_numerically_among_themselves(tmp_path: Path):
    root = _warp(tmp_path)
    for item_id in ("g-10", "g-2", "w-1"):
        _write(root, item_id, "# X\n\ntype: goal\n" if item_id.startswith("g") else "# X\n\ntype: action\n")
    # Bucketed by prefix then number ("g" sorts before "w" lexically) —
    # the load-bearing property this test guards is numeric-aware order
    # *within* the goal prefix (g-2 before g-10), not cross-prefix order.
    assert [i.id for i in items.load_items(root)] == ["g-2", "g-10", "w-1"]


def test_new_item_text_round_trips_goal_rows(tmp_path: Path):
    root = _warp(tmp_path)
    text = items.new_item_text(
        "Grow attention on the account",
        item_type="goal",
        metric="tickets bought",
        target="exponential",
        horizon="ongoing",
    )
    item = items.parse_item(_write(root, "g-1", text))
    assert item is not None
    assert item.type == "goal"
    assert item.metric == "tickets bought"
    assert item.target == "exponential"
    assert item.horizon == "ongoing"


def test_new_item_text_round_trips_advances(tmp_path: Path):
    root = _warp(tmp_path)
    text = items.new_item_text("Ship it", item_type="action", advances=["g-1"])
    item = items.parse_item(_write(root, "w-1", text))
    assert item is not None
    assert item.advances == ["g-1"]


def test_contributing_cone_is_direct_advancers_plus_needs_closure(tmp_path: Path):
    root = _warp(tmp_path)
    _write(root, "g-1", "# Grow attention\n\ntype: goal\n")
    _write(root, "w-1", "# Ship the digest\n\ntype: action\nadvances: g-1\nneeds: w-2\n")
    _write(root, "w-2", "# Instrument analytics\n\ntype: preparation\n")
    _write(root, "w-3", "# Unrelated\n\ntype: action\n")
    all_items = items.load_items(root)
    cone_ids = {i.id for i in items.contributing_cone("g-1", all_items)}
    assert cone_ids == {"w-1", "w-2"}


def test_contributing_cone_does_not_recurse_through_sub_goal_advances(tmp_path: Path):
    """A sub-goal's *own* advancers are not pulled into the parent's cone —
    the design gives ``advances:`` on a goal no special recursive
    treatment yet."""
    root = _warp(tmp_path)
    _write(root, "g-1", "# Parent goal\n\ntype: goal\n")
    _write(root, "g-2", "# Sub-goal\n\ntype: goal\nadvances: g-1\n")
    _write(root, "w-1", "# Work on the sub-goal\n\ntype: action\nadvances: g-2\n")
    all_items = items.load_items(root)
    cone_ids = {i.id for i in items.contributing_cone("g-1", all_items)}
    assert cone_ids == {"g-2"}
    assert "w-1" not in cone_ids


def test_blockers_on_you_is_open_decisions_and_preparations_in_the_cone(
    tmp_path: Path,
):
    root = _warp(tmp_path)
    _write(root, "g-1", "# Grow attention\n\ntype: goal\n")
    _write(root, "w-1", "# Ship\n\ntype: action\nadvances: g-1\nneeds: w-2 w-3\n")
    _write(root, "w-2", "# Pick the metric\n\ntype: decision\n")
    _write(root, "w-3", "# Already decided\n\ntype: decision\ndone: 2026-08-11\n")
    all_items = items.load_items(root)
    callback = items.blockers_on_you("g-1", all_items)
    assert [i.id for i in callback] == ["w-2"]


def test_render_index_lists_goals_first_with_metric_spine_and_callback(
    tmp_path: Path,
):
    root = _warp(tmp_path)
    _write(
        root,
        "g-1",
        "# Grow attention\n\ntype: goal\nmetric: tickets\ntarget: 1000\nhorizon: Q4\n",
    )
    _write(root, "w-1", "# Ship\n\ntype: action\nadvances: g-1\nneeds: w-2\n")
    _write(root, "w-2", "# Decide\n\ntype: decision\n")
    index = items.render_index(root)
    assert index is not None
    lines = index.split("\n")
    assert lines[0] == "goals:"
    assert "g-1" in lines[1] and "goal" in lines[1]
    assert "metric: tickets" in lines[1]
    assert "target: 1000" in lines[1]
    assert "horizon: Q4" in lines[1]
    assert "needs-you w-2" in lines[1]
    # Goals never fold into the ready/held dispatchable bands.
    assert "ready:" in lines
    assert not any(line.startswith("- g-1") and "ready" in line for line in lines)


def test_render_index_open_goal_with_no_callback_has_no_needs_you(tmp_path: Path):
    root = _warp(tmp_path)
    _write(root, "g-1", "# Solo goal\n\ntype: goal\n")
    index = items.render_index(root)
    assert index is not None
    assert "needs-you" not in index


def test_render_index_none_when_only_a_done_goal_exists(tmp_path: Path):
    root = _warp(tmp_path)
    _write(root, "g-1", "# Retired goal\n\ntype: goal\ndone: 2026-08-01\n")
    # A done goal renders in no band today (goals-band is open-only, and
    # the done-tail excludes goals) — documented via a non-crashing None,
    # not a silent surprise.
    assert items.render_index(root) is None


# ── goal readings store ──────────────────────────────────────────────────


def test_append_reading_writes_one_jsonl_line_and_returns_it(tmp_path: Path):
    root = _warp(tmp_path)
    reading = items.append_reading(
        root, "g-1", "tickets", 10, source="manual", ts="2026-08-01T00:00:00Z"
    )
    assert reading.key == "tickets"
    assert reading.value == 10.0
    path = items.readings_path(root, "g-1")
    assert path.name == "g-1.readings.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record == {
        "ts": "2026-08-01T00:00:00Z",
        "key": "tickets",
        "value": 10.0,
        "source": "manual",
    }


def test_append_reading_includes_note_only_when_given(tmp_path: Path):
    root = _warp(tmp_path)
    items.append_reading(root, "g-1", "tickets", 1, ts="2026-08-01T00:00:00Z")
    items.append_reading(
        root, "g-1", "tickets", 2, note="second one", ts="2026-08-02T00:00:00Z"
    )
    lines = items.readings_path(root, "g-1").read_text(encoding="utf-8").splitlines()
    assert "note" not in json.loads(lines[0])
    assert json.loads(lines[1])["note"] == "second one"


def test_load_readings_is_empty_for_an_unread_goal(tmp_path: Path):
    root = _warp(tmp_path)
    assert items.load_readings(root, "g-1") == []


def test_load_readings_skips_malformed_lines(tmp_path: Path):
    root = _warp(tmp_path)
    path = items.readings_path(root, "g-1")
    path.write_text(
        '{"ts": "2026-08-01T00:00:00Z", "key": "tickets", "value": 1.0, "source": "m"}\n'
        "not json at all\n"
        '{"ts": "2026-08-02T00:00:00Z", "key": "tickets"}\n'  # missing value
        '{"ts": "2026-08-03T00:00:00Z", "key": "tickets", "value": 2.0, "source": "m"}\n',
        encoding="utf-8",
    )
    readings = items.load_readings(root, "g-1")
    assert [r.value for r in readings] == [1.0, 2.0]


def test_reading_summary_delta_previous_and_bounds(tmp_path: Path):
    root = _warp(tmp_path)
    items.append_reading(root, "g-1", "tickets", 10, ts="2026-08-01T00:00:00Z")
    items.append_reading(root, "g-1", "tickets", 15, ts="2026-08-03T00:00:00Z")
    items.append_reading(root, "g-1", "conversion", 0.5, ts="2026-08-02T00:00:00Z")
    summary = items.reading_summary(items.load_readings(root, "g-1"))
    assert summary["tickets"].latest.value == 15
    assert summary["tickets"].previous.value == 10
    assert summary["tickets"].delta == 5
    assert summary["tickets"].count == 2
    assert summary["tickets"].min == 10
    assert summary["tickets"].max == 15
    assert summary["conversion"].previous is None
    assert summary["conversion"].delta is None
    assert summary["conversion"].count == 1


def test_reading_summary_orders_by_ts_not_append_order(tmp_path: Path):
    root = _warp(tmp_path)
    # Appended out of chronological order — the summary sorts by ts.
    items.append_reading(root, "g-1", "tickets", 15, ts="2026-08-03T00:00:00Z")
    items.append_reading(root, "g-1", "tickets", 10, ts="2026-08-01T00:00:00Z")
    summary = items.reading_summary(items.load_readings(root, "g-1"))
    assert summary["tickets"].latest.value == 15
    assert summary["tickets"].previous.value == 10
    assert summary["tickets"].delta == 5


def test_withdrawn_reading_is_excluded_from_bounds_count_and_delta(tmp_path: Path):
    root = _warp(tmp_path)
    items.append_reading(
        root, "g-1", "tickets", 10, basis="daily", ts="2026-08-01T00:00:00Z"
    )
    items.append_reading(
        root, "g-1", "tickets", 999, basis="lifetime", ts="2026-08-02T00:00:00Z"
    )
    items.append_reading_withdrawal(
        root,
        "g-1",
        "tickets",
        "2026-08-02T00:00:00Z",
        why="wrong measurement population",
        ts="2026-08-02T01:00:00Z",
    )
    items.append_reading(
        root, "g-1", "tickets", 15, basis="daily", ts="2026-08-03T00:00:00Z"
    )

    rows = items.load_readings(root, "g-1")
    info = items.reading_summary(rows)["tickets"]
    assert info.latest.value == 15
    assert info.previous.value == 10
    assert info.delta == 5
    assert info.basis_mismatch is False
    assert info.count == 2
    assert info.min == 10
    assert info.max == 15
    assert items.reading_withdrawal_counts(rows) == {"tickets": 1}

    records = [json.loads(line) for line in items.readings_path(root, "g-1").read_text().splitlines()]
    assert records[2]["withdrawn_ts"] == "2026-08-02T00:00:00Z"
    assert records[2]["why"] == "wrong measurement population"


def test_append_reading_includes_basis_only_when_given(tmp_path: Path):
    root = _warp(tmp_path)
    items.append_reading(root, "g-1", "impressions", 333, ts="2026-08-15T13:18:00Z")
    items.append_reading(
        root, "g-1", "impressions", 147, basis="window5", ts="2026-08-16T20:50:00Z"
    )
    lines = items.readings_path(root, "g-1").read_text(encoding="utf-8").splitlines()
    assert "basis" not in json.loads(lines[0])
    assert json.loads(lines[1])["basis"] == "window5"


def test_reading_summary_same_basis_renders_delta(tmp_path: Path):
    # Same key, explicit matching basis on both samples — a real Δ.
    root = _warp(tmp_path)
    items.append_reading(
        root, "g-1", "impressions", 100, basis="window5", ts="2026-08-01T00:00:00Z"
    )
    items.append_reading(
        root, "g-1", "impressions", 150, basis="window5", ts="2026-08-02T00:00:00Z"
    )
    summary = items.reading_summary(items.load_readings(root, "g-1"))
    info = summary["impressions"]
    assert info.delta == 50
    assert info.basis_mismatch is False


def test_reading_summary_cross_basis_suppresses_delta(tmp_path: Path):
    # The bug report's live case: same key, same `source`, incompatible
    # denominators — a lifetime sum then a 5-item window sum. Distinct
    # `basis` values must gate the Δ even though `source` alone doesn't.
    root = _warp(tmp_path)
    items.append_reading(
        root,
        "g-1",
        "impressions",
        333,
        source="x-api",
        basis="lifetime",
        ts="2026-08-15T13:18:00Z",
    )
    items.append_reading(
        root,
        "g-1",
        "impressions",
        147,
        source="x-api",
        basis="window5",
        ts="2026-08-16T20:50:00Z",
    )
    summary = items.reading_summary(items.load_readings(root, "g-1"))
    info = summary["impressions"]
    assert info.delta is None
    assert info.basis_mismatch is True
    # The refused comparison still surfaces both endpoints — nothing about
    # this suppresses the data, only the arithmetic across it.
    assert info.latest.value == 147
    assert info.previous.value == 333


def test_reading_summary_no_previous_is_not_a_basis_mismatch(tmp_path: Path):
    # A single sample has nothing to diff against — that's the ordinary
    # "no previous" case, distinct from a refused cross-basis comparison.
    root = _warp(tmp_path)
    items.append_reading(root, "g-1", "conversion", 0.5, ts="2026-08-01T00:00:00Z")
    summary = items.reading_summary(items.load_readings(root, "g-1"))
    info = summary["conversion"]
    assert info.delta is None
    assert info.basis_mismatch is False


def test_reading_summary_no_basis_falls_back_to_source(tmp_path: Path):
    # Old rows written before `basis` existed carry none — comparisons
    # keep working exactly as before (gated on `source`), a strict
    # superset of the pre-fix behaviour rather than a new restriction.
    root = _warp(tmp_path)
    items.append_reading(
        root, "g-1", "posts", 14, source="x-api", ts="2026-08-01T00:00:00Z"
    )
    items.append_reading(
        root, "g-1", "posts", 17, source="x-api", ts="2026-08-02T00:00:00Z"
    )
    summary = items.reading_summary(items.load_readings(root, "g-1"))
    assert summary["posts"].delta == 3
    assert summary["posts"].basis_mismatch is False

    # And the second live-bug shape: no explicit basis, but `source`
    # crosses a collector boundary — still caught via the source fallback.
    items.append_reading(
        root, "g-1", "posts", 19, source="local-post-log", ts="2026-08-03T00:00:00Z"
    )
    summary = items.reading_summary(items.load_readings(root, "g-1"))
    assert summary["posts"].delta is None
    assert summary["posts"].basis_mismatch is True


def test_load_readings_parses_rows_with_no_basis_field(tmp_path: Path):
    root = _warp(tmp_path)
    path = items.readings_path(root, "g-1")
    path.write_text(
        '{"ts": "2026-08-01T00:00:00Z", "key": "tickets", "value": 1.0, "source": "m"}\n',
        encoding="utf-8",
    )
    readings = items.load_readings(root, "g-1")
    assert len(readings) == 1
    assert readings[0].basis is None


def test_readings_index_line_marks_a_refused_cross_basis_delta(tmp_path: Path):
    root = _warp(tmp_path)
    items.append_reading(
        root, "g-1", "impressions", 333, basis="lifetime", ts="2026-08-15T13:18:00Z"
    )
    items.append_reading(
        root, "g-1", "impressions", 147, basis="window5", ts="2026-08-16T20:50:00Z"
    )
    line = items.readings_index_line("g-1", root)
    assert line is not None
    assert "impressions 147 (Δ refused: basis differs)" in line
    assert "Δ-186" not in line


def test_format_value_trims_integers_and_trailing_zeros():
    assert items.format_value(10.0) == "10"
    assert items.format_value(12.5) == "12.5"
    assert items.format_value(0.1000) == "0.1"


def test_format_delta_signs_positive_and_negative():
    assert items.format_delta(5) == "+5"
    assert items.format_delta(-5) == "-5"
    assert items.format_delta(0) == "+0"


def test_readings_index_line_none_without_readings(tmp_path: Path):
    root = _warp(tmp_path)
    _write(root, "g-1", "# Grow\n\ntype: goal\n")
    assert items.readings_index_line("g-1", root) is None
    assert items.readings_index_line("g-1", None) is None


def test_readings_index_line_latest_per_key_with_delta(tmp_path: Path):
    root = _warp(tmp_path)
    items.append_reading(root, "g-1", "tickets", 10, ts="2026-08-01T00:00:00Z")
    items.append_reading(root, "g-1", "tickets", 15, ts="2026-08-03T00:00:00Z")
    items.append_reading(root, "g-1", "conversion", 0.5, ts="2026-08-02T00:00:00Z")
    line = items.readings_index_line("g-1", root)
    assert line is not None
    assert line.startswith("readings: ")
    assert "conversion 0.5" in line
    assert "tickets 15 (Δ+5 since 2d)" in line


def test_readings_index_line_capped_around_200_bytes(tmp_path: Path):
    root = _warp(tmp_path)
    for i in range(40):
        items.append_reading(root, "g-1", f"metric-{i:02d}", i, ts=f"2026-08-01T00:00:{i:02d}Z")
    line = items.readings_index_line("g-1", root)
    assert line is not None
    # First key always renders even if a cap-crossing later key gets
    # dropped — the budget bounds growth, it never empties the line.
    assert len(line.encode("utf-8")) < 260
    assert "metric-00" in line


def test_render_index_appends_readings_as_a_second_line_under_the_goal(
    tmp_path: Path,
):
    root = _warp(tmp_path)
    _write(root, "g-1", "# Grow attention\n\ntype: goal\nmetric: tickets\n")
    items.append_reading(root, "g-1", "tickets", 10, ts="2026-08-01T00:00:00Z")
    index = items.render_index(root)
    assert index is not None
    lines = index.split("\n")
    goal_idx = next(i for i, line in enumerate(lines) if line.startswith("- g-1"))
    assert lines[goal_idx + 1].strip().startswith("readings: tickets 10")


def test_render_index_no_readings_line_when_goal_unread(tmp_path: Path):
    root = _warp(tmp_path)
    _write(root, "g-1", "# Grow attention\n\ntype: goal\n")
    index = items.render_index(root)
    assert index is not None
    assert "readings:" not in index


# ``tests/fixtures/goal_readings_sample.jsonl`` is real ``brnrd goal record``
# output (captured from a scratch account, three calls: two `tickets`
# samples one `conversion` sample), not hand-written — the same file also
# round-trips through `parseGoalReadings` in
# `src/frontend/src/lib/warpGraph.test.ts`, so the Python writer and the TS
# reader are checked against one grammar instead of two independently
# plausible ones.
FIXTURE_READINGS = Path(__file__).parent / "fixtures" / "goal_readings_sample.jsonl"


def test_fixture_readings_load_and_summarize_as_recorded(tmp_path: Path):
    root = _warp(tmp_path)
    root.joinpath("g-1.readings.jsonl").write_bytes(FIXTURE_READINGS.read_bytes())
    readings = items.load_readings(root, "g-1")
    assert len(readings) == 3
    summary = items.reading_summary(readings)
    assert summary["tickets"].latest.value == 18
    assert summary["tickets"].previous.value == 12
    assert summary["tickets"].delta == 6
    assert summary["tickets"].count == 2
    assert summary["conversion"].latest.value == 0.42
    assert summary["conversion"].count == 1
    # The first tickets sample carries the recorded note; the second doesn't.
    assert readings[0].note == "first count"
    assert readings[1].note is None


def test_reading_summary_orders_mixed_width_stamps_chronologically(tmp_path):
    """A whole-second and a microsecond stamp in the SAME second must order by time.

    Readings were whole-second before withdrawal handles needed microsecond
    precision and are microsecond after, so any goal recorded across that
    change can hold both widths. A raw string sort puts `…56.400000Z`
    before `…56Z` (`.` < `Z`), which makes the *earlier* row the `latest`
    one — and `latest` is the number every goal surface publishes.
    """
    warp = tmp_path / "warp"
    warp.mkdir()
    path = items.readings_path(warp, "g-1")
    path.write_text(
        '{"ts": "2026-08-20T18:10:56Z", "key": "followers", "value": 2, "basis": "b"}\n'
        '{"ts": "2026-08-20T18:10:56.400000Z", "key": "followers", "value": 3, "basis": "b"}\n',
        encoding="utf-8",
    )
    summary = items.reading_summary(items.load_readings(warp, "g-1"))["followers"]
    assert summary.latest.value == 3
    assert summary.previous is not None and summary.previous.value == 2
    assert summary.delta == 1


def test_reading_ts_order_key_normalises_both_widths():
    key = items.reading_ts_order_key
    assert key("2026-08-20T18:10:56Z") == "2026-08-20T18:10:56.000000"
    assert key("2026-08-20T18:10:56.4Z") == "2026-08-20T18:10:56.400000"
    assert key("2026-08-20T18:10:56.123456Z") == "2026-08-20T18:10:56.123456"
    # the ordering the raw string sort got wrong
    assert key("2026-08-20T18:10:56Z") < key("2026-08-20T18:10:56.400000Z")

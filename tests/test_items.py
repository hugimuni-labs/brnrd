"""The warp item space, daemon half (``items.py``) — grammar in lockstep
with the frontend's ``warpGraph.ts``, receipts as derived state, the wake
index, and the row-scoped file edits every verb rests on."""

from __future__ import annotations

from pathlib import Path

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

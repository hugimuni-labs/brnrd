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

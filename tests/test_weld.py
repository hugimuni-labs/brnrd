"""THE WELD, re-grounded on the item space (2026-08-11).

The weld used to write into layer files (``surface/layers/<layer>.md``,
addressed ``layer#slug``); it now writes into one file per item
(``surface/warp/<id>.md``, addressed by the id alone). ``items.py`` owns
the grammar; these tests drive the run-lifecycle glue — ignition and
capture — plus the id scan/resolve gates the glue rests on.
"""

from __future__ import annotations

from pathlib import Path

from brr import items
from brr import relics
from brr import weld


def _warp(tmp_path: Path) -> Path:
    root = tmp_path / "surface" / "warp"
    root.mkdir(parents=True)
    return root


def _item(root: Path, item_id: str, text: str) -> Path:
    path = root / f"{item_id}.md"
    path.write_text(text, encoding="utf-8")
    return path


ITEM_TEXT = """# Gate chips row on repos

type: action
topics: loom
refs: hugimuni-labs/brnrd#971
prompt: Implement the per-repo gate chips row.

Slack has no door today; the chips row is the open-ended shape.
"""


# ── grammar gates ─────────────────────────────────────────────────────────


def test_is_item_address_accepts_slug_ids():
    assert weld.is_item_address("w-42")
    assert weld.is_item_address("one-spine-docs")
    assert weld.is_item_address("a1")


def test_is_item_address_refuses_everything_else():
    for bad in ("", "-lead", "W-42", "the loom", "owner/repo#1", "loom#slug", None):
        assert not weld.is_item_address(bad)  # type: ignore[arg-type]


def test_scan_finds_allocated_ids_and_explicit_item_lines_once():
    body = (
        "Take w-42 and w-7, then w-42 again.\n"
        "item: one-spine-docs\n"
        "not-an-address: bare-slug prose stays prose\n"
        "a forge ref hugimuni-labs/brnrd#971 never scans, nor run-w-1's tail\n"
    )
    assert weld.scan_item_addresses(body) == ["w-42", "w-7", "one-spine-docs"]


def test_scan_never_reads_prose_slugs_as_items():
    # Bare slugs are everywhere in English; only the allocated `w-<N>` shape
    # is safe as a free token — a named item needs its explicit line.
    assert weld.scan_item_addresses("the one-spine-docs plan is ready") == []


def test_resolve_requires_the_item_file(tmp_path: Path):
    root = _warp(tmp_path)
    assert weld.resolve_address(root, "w-1") is None
    path = _item(root, "w-1", ITEM_TEXT)
    assert weld.resolve_address(root, "w-1") == path
    assert weld.resolve_address(None, "w-1") is None
    assert weld.resolve_address(root, "not/legal") is None


# ── ignition ──────────────────────────────────────────────────────────────


def test_annotate_ignition_appends_relic_and_taken_row(tmp_path: Path, capsys):
    root = _warp(tmp_path)
    path = _item(root, "w-1", ITEM_TEXT)
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    resolved = weld.annotate_ignition(
        root, outbox, run_id="run-260811-0001-aaaa", body="please do w-1 now"
    )
    assert resolved == ["w-1"]
    text = path.read_text(encoding="utf-8")
    assert "taken: run-260811-0001-aaaa" in text
    # The row lands inside the recognized block, before the body.
    assert text.index("taken:") < text.index("Slack has no door")
    reported = relics.read_reported(outbox)
    assert [(r["kind"], r["address"]) for r in reported] == [("item", "w-1")]


def test_annotate_ignition_is_idempotent_per_run(tmp_path: Path):
    root = _warp(tmp_path)
    path = _item(root, "w-1", ITEM_TEXT)
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    for _ in range(2):
        weld.annotate_ignition(root, outbox, run_id="run-a", body="w-1")
    assert path.read_text(encoding="utf-8").count("taken:") == 1
    assert len(relics.read_reported(outbox)) == 1


def test_annotate_ignition_unresolvable_id_writes_nothing(tmp_path: Path, capsys):
    root = _warp(tmp_path)
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    assert weld.annotate_ignition(root, outbox, run_id="run-a", body="w-99") == []
    assert relics.read_reported(outbox) == []
    assert "does not resolve" in capsys.readouterr().out


def test_annotate_ignition_without_warp_dir_is_a_no_op(tmp_path: Path):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    assert weld.annotate_ignition(None, outbox, run_id="run-a", body="w-1") == []


def test_second_run_appends_to_the_taken_row(tmp_path: Path):
    root = _warp(tmp_path)
    path = _item(root, "w-1", ITEM_TEXT)
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    weld.annotate_ignition(root, outbox, run_id="run-a", body="w-1")
    weld.annotate_ignition(root, outbox, run_id="run-b", body="w-1")
    text = path.read_text(encoding="utf-8")
    assert "taken: run-a run-b" in text


# ── capture ───────────────────────────────────────────────────────────────


def test_capture_refs_lands_produce_on_the_item(tmp_path: Path):
    root = _warp(tmp_path)
    path = _item(root, "w-1", ITEM_TEXT)
    records = [
        {"kind": "item", "address": "w-1"},
        {"kind": "pr", "number": 1234, "repo": "hugimuni-labs/brnrd"},
        {"kind": "issue", "number": 88},
    ]
    welded = weld.capture_refs(root, records=records, origin_repo="hugimuni-labs/brnrd")
    assert welded == {"w-1": ["hugimuni-labs/brnrd#1234", "hugimuni-labs/brnrd#88"]}
    text = path.read_text(encoding="utf-8")
    # Existing ref kept, new ones appended, deduped.
    assert "hugimuni-labs/brnrd#971 · hugimuni-labs/brnrd#1234" in text
    # Re-run: nothing added twice.
    assert weld.capture_refs(root, records=records, origin_repo="hugimuni-labs/brnrd") == {}


def test_capture_refs_skips_an_id_that_stopped_resolving(tmp_path: Path, capsys):
    root = _warp(tmp_path)
    records = [
        {"kind": "item", "address": "w-9"},
        {"kind": "pr", "number": 5, "repo": "o/r"},
    ]
    assert weld.capture_refs(root, records=records, origin_repo="o/r") == {}
    assert "no longer resolves" in capsys.readouterr().out


def test_capture_refs_without_forge_produce_touches_nothing(tmp_path: Path):
    root = _warp(tmp_path)
    path = _item(root, "w-1", ITEM_TEXT)
    before = path.read_text(encoding="utf-8")
    records = [{"kind": "item", "address": "w-1"}, {"kind": "commit", "sha": "abc"}]
    assert weld.capture_refs(root, records=records, origin_repo="o/r") == {}
    assert path.read_text(encoding="utf-8") == before


def test_qualified_forge_refs_covers_pr_issue_and_merge():
    records = [
        {"kind": "pr", "number": 1, "repo": "a/b"},
        {"kind": "issue", "number": 2},
        {"kind": "merge", "pr": 3, "repo": "c/d"},
        {"kind": "pr", "number": 1, "repo": "a/b"},  # dupe collapses
    ]
    assert weld.qualified_forge_refs(records, "e/f") == ["a/b#1", "e/f#2", "c/d#3"]


def test_qualified_forge_refs_never_guesses_a_repo():
    records = [{"kind": "pr", "number": 1}, {"kind": "issue", "number": "x"}]
    assert weld.qualified_forge_refs(records, None) == []
    assert weld.qualified_forge_refs(records, "not-a-repo") == []

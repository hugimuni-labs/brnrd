"""Tests for :mod:`brr.weld` — THE WELD, machinery half (#972).

Warp items and the runs they ignite reference each other through resolver
addresses, riding the existing relic manifest: ignition annotates the item
(``taken: run-…`` + an ``item`` relic in ``.relics.jsonl``), capture lands
the run's forge produce back on the item's ``refs:`` row as qualified
``owner/repo#N`` refs. Nothing lists the other side's content.
"""

from __future__ import annotations

from pathlib import Path

from brr import relics
from brr import weld


LAYER_TEXT = """# the-loom — the machine

The band above the first heading is the layer definition.

## THE WELD: items and runs reference each other, never re-list

state: banked
refs: hugimuni-labs/brnrd#972 · design-work-layers.md
prompt: Implement the weld per #972.

Free markdown body under the item.

## Gate chips row on /repos

state: ember
refs: [#971](https://github.com/hugimuni-labs/brnrd/issues/971)

## Bare item with no rows

Just prose, no recognized rows at all.
"""

WELD_SLUG = "the-weld-items-and-runs-reference-each-other-never-re-list"
WELD_ADDRESS = f"the-loom#{WELD_SLUG}"


def _layers(tmp_path: Path, text: str = LAYER_TEXT, name: str = "the-loom") -> Path:
    root = tmp_path / "surface" / "layers"
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.md").write_text(text, encoding="utf-8")
    return root


def _outbox(tmp_path: Path) -> Path:
    outbox = tmp_path / "outbox"
    outbox.mkdir(exist_ok=True)
    return outbox


# ── slug computation mirrors the frontend ────────────────────────────

def test_heading_anchor_matches_frontend_algorithm():
    """Expected values computed with the literal ``surface.ts`` regexes under
    node — the two implementations are one address computation and must not
    drift (unicode strips like JS's ASCII ``\\w``, punctuation drops, repeated
    words keep every occurrence, inner hyphens/underscores survive)."""
    cases = {
        "Gate chips row on /repos": "gate-chips-row-on-repos",
        "THE WELD: items and runs reference each other, never re-list": WELD_SLUG,
        "Café · déjà vu": "caf-dj-vu",
        "The raw-403 wart on the pairing card": "the-raw-403-wart-on-the-pairing-card",
        "weld weld weld": "weld-weld-weld",
        "  spaced   out  ": "spaced-out",
        "under_score kept": "under_score-kept",
        "100% done (really!)": "100-done-really",
        "naïve — em-dash case": "nave-em-dash-case",
    }
    for heading, slug in cases.items():
        assert weld.heading_anchor(heading) == slug


# ── address validation ───────────────────────────────────────────────

def test_is_item_address_accepts_the_grammar():
    assert weld.is_item_address("the-loom#gate-chips-row-on-repos")
    assert weld.is_item_address("a#b")
    assert weld.is_item_address("layer2#raw-403-wart")


def test_is_item_address_refuses_everything_else():
    for bad in [
        "", "#", "layer#", "#slug", "Layer#slug", "layer#Slug",
        "-layer#slug", "layer#-slug", "owner/repo#12", "layer#slug extra",
        "layer##slug", "layer#slug#more", "under_score#slug", "layer#a_b",
    ]:
        assert not weld.is_item_address(bad), bad


def test_scan_finds_addresses_once_and_never_a_forge_ref_tail():
    body = (
        "Take the-loom#the-weld (see hugimuni-labs/brnrd#972 and bare #12); "
        "the-loom#the-weld again, plus the-post#hooks."
    )
    assert weld.scan_item_addresses(body) == [
        "the-loom#the-weld", "the-post#hooks",
    ]


# ── resolution: file AND heading, never guessed ──────────────────────

def test_resolve_address_requires_layer_file_and_heading(tmp_path: Path):
    layers = _layers(tmp_path)
    assert weld.resolve_address(layers, WELD_ADDRESS) == layers / "the-loom.md"
    # Right file, no such heading.
    assert weld.resolve_address(layers, "the-loom#no-such-item") is None
    # No such layer file.
    assert weld.resolve_address(layers, f"the-shed#{WELD_SLUG}") is None
    # Grammar-invalid never resolves.
    assert weld.resolve_address(layers, "The-Loom#weld") is None
    assert weld.resolve_address(None, WELD_ADDRESS) is None


# ── taken rows: insertion + idempotency ──────────────────────────────

def test_mark_taken_inserts_after_the_recognized_rows(tmp_path: Path):
    layers = _layers(tmp_path)
    path = layers / "the-loom.md"

    assert weld.mark_taken(path, WELD_SLUG, "run-260802-0001-aaaa") is True

    lines = path.read_text(encoding="utf-8").split("\n")
    at = lines.index("taken: run-260802-0001-aaaa")
    # After the contiguous row block (prompt: is the section's last row),
    # before the free-markdown body — the frontend's recognized block ends at
    # the first unrecognized row, so `taken:` must never sit between rows.
    assert lines[at - 1].startswith("prompt:")
    assert lines[at + 1] == ""


def test_mark_taken_is_idempotent_and_appends_space_separated(tmp_path: Path):
    layers = _layers(tmp_path)
    path = layers / "the-loom.md"

    assert weld.mark_taken(path, WELD_SLUG, "run-1") is True
    before = path.read_text(encoding="utf-8")
    # Same run id again: no write at all.
    assert weld.mark_taken(path, WELD_SLUG, "run-1") is False
    assert path.read_text(encoding="utf-8") == before
    # A second run appends to the existing row, space-separated.
    assert weld.mark_taken(path, WELD_SLUG, "run-2") is True
    text = path.read_text(encoding="utf-8")
    assert "taken: run-1 run-2" in text
    assert text.count("taken:") == 1


def test_mark_taken_on_rowless_item_lands_after_the_heading(tmp_path: Path):
    layers = _layers(tmp_path)
    path = layers / "the-loom.md"

    assert weld.mark_taken(path, "bare-item-with-no-rows", "run-3") is True

    lines = path.read_text(encoding="utf-8").split("\n")
    at = lines.index("taken: run-3")
    assert lines[at - 2] == "## Bare item with no rows"
    # Separated from the body it was inserted in front of.
    assert lines[at + 1] == ""
    assert lines[at + 2].startswith("Just prose")


def test_mark_taken_unresolvable_section_writes_nothing(tmp_path: Path):
    layers = _layers(tmp_path)
    path = layers / "the-loom.md"
    before = path.read_text(encoding="utf-8")
    assert weld.mark_taken(path, "no-such-item", "run-1") is False
    assert path.read_text(encoding="utf-8") == before


# ── refs: append + dedupe ────────────────────────────────────────────

def test_append_item_refs_appends_and_dedupes(tmp_path: Path):
    layers = _layers(tmp_path)
    path = layers / "the-loom.md"

    added = weld.append_item_refs(
        path, WELD_SLUG,
        ["hugimuni-labs/brnrd#978", "hugimuni-labs/brnrd#972"],
    )
    # #972 is already on the row in the qualified grammar; only #978 lands.
    assert added == ["hugimuni-labs/brnrd#978"]
    text = path.read_text(encoding="utf-8")
    assert (
        "refs: hugimuni-labs/brnrd#972 · design-work-layers.md · "
        "hugimuni-labs/brnrd#978" in text
    )
    # Re-running finalize appends nothing twice.
    assert weld.append_item_refs(
        path, WELD_SLUG,
        ["hugimuni-labs/brnrd#978", "hugimuni-labs/brnrd#972"],
    ) == []
    assert path.read_text(encoding="utf-8") == text


def test_append_item_refs_sees_a_markdown_link_as_present(tmp_path: Path):
    """An authored `[#971](…/issues/971)` link names the same thread as the
    qualified `hugimuni-labs/brnrd#971` — appending it anyway would be the
    duplicate the weld exists to end."""
    layers = _layers(tmp_path)
    path = layers / "the-loom.md"

    added = weld.append_item_refs(
        path, "gate-chips-row-on-repos",
        ["hugimuni-labs/brnrd#971", "hugimuni-labs/brnrd#9710"],
    )
    assert added == ["hugimuni-labs/brnrd#9710"]


def test_append_item_refs_creates_a_row_on_a_rowless_item(tmp_path: Path):
    layers = _layers(tmp_path)
    path = layers / "the-loom.md"

    added = weld.append_item_refs(
        path, "bare-item-with-no-rows", ["hugimuni-labs/brnrd#978"],
    )
    assert added == ["hugimuni-labs/brnrd#978"]
    lines = path.read_text(encoding="utf-8").split("\n")
    at = lines.index("refs: hugimuni-labs/brnrd#978")
    assert lines[at - 2] == "## Bare item with no rows"


def test_new_refs_row_lands_before_an_existing_taken_row(tmp_path: Path):
    """`taken:` is not a frontend-recognized row — it ends the parsed block —
    so a refs row created later must sit above it to stay schema."""
    layers = _layers(tmp_path)
    path = layers / "the-loom.md"
    weld.mark_taken(path, "bare-item-with-no-rows", "run-1")

    weld.append_item_refs(
        path, "bare-item-with-no-rows", ["hugimuni-labs/brnrd#978"],
    )
    lines = path.read_text(encoding="utf-8").split("\n")
    refs_at = lines.index("refs: hugimuni-labs/brnrd#978")
    taken_at = lines.index("taken: run-1")
    assert refs_at < taken_at


# ── qualified forge refs from the relic manifest ─────────────────────

def test_qualified_forge_refs_covers_pr_issue_and_merge():
    records = [
        {"kind": "pr", "number": 978},
        {"kind": "issue", "number": 972, "repo": "other/project"},
        {"kind": "merge", "pr": 978},
        {"kind": "commit", "sha": "abc1234"},
        {"kind": "item", "address": "the-loom#x"},
    ]
    assert weld.qualified_forge_refs(records, "hugimuni-labs/brnrd") == [
        "hugimuni-labs/brnrd#978", "other/project#972",
    ]


def test_qualified_forge_refs_never_guesses_a_repo():
    # No origin and no record repo: the number alone names no thread on an
    # account-global surface (the bare-#N rule), so nothing is produced.
    assert weld.qualified_forge_refs([{"kind": "pr", "number": 7}], None) == []
    assert weld.qualified_forge_refs(
        [{"kind": "merge", "sha": "abc", "subject": "Merge branch 'x'"}],
        "hugimuni-labs/brnrd",
    ) == []


# ── ignition ─────────────────────────────────────────────────────────

def test_annotate_ignition_appends_relic_and_taken_row(tmp_path: Path, capsys):
    layers = _layers(tmp_path)
    outbox = _outbox(tmp_path)
    body = (
        f"Please take {WELD_ADDRESS} and also the-loom#no-such-item "
        "and the-shed#nothing."
    )

    resolved = weld.annotate_ignition(
        layers, outbox, run_id="run-260802-0002-bbbb", body=body,
    )

    assert resolved == [WELD_ADDRESS]
    assert relics.read_reported(outbox) == [
        {"kind": "item", "address": WELD_ADDRESS},
    ]
    text = (layers / "the-loom.md").read_text(encoding="utf-8")
    assert "taken: run-260802-0002-bbbb" in text
    # The unresolvable addresses were named in a log line and never written.
    out = capsys.readouterr().out
    assert "the-loom#no-such-item" in out and "skipped" in out
    assert "the-shed#nothing" in out
    assert "no-such-item" not in text


def test_annotate_ignition_is_idempotent_per_run(tmp_path: Path):
    layers = _layers(tmp_path)
    outbox = _outbox(tmp_path)
    body = f"take {WELD_ADDRESS}"

    weld.annotate_ignition(layers, outbox, run_id="run-a", body=body)
    weld.annotate_ignition(layers, outbox, run_id="run-a", body=body)

    assert relics.read_reported(outbox) == [
        {"kind": "item", "address": WELD_ADDRESS},
    ]
    text = (layers / "the-loom.md").read_text(encoding="utf-8")
    assert text.count("taken:") == 1
    assert "taken: run-a" in text


def test_annotate_ignition_unresolvable_body_writes_nothing(tmp_path: Path):
    layers = _layers(tmp_path)
    outbox = _outbox(tmp_path)
    before = (layers / "the-loom.md").read_text(encoding="utf-8")

    resolved = weld.annotate_ignition(
        layers, outbox, run_id="run-a", body="only the-loom#nope here",
    )

    assert resolved == []
    assert relics.read_reported(outbox) == []
    assert (layers / "the-loom.md").read_text(encoding="utf-8") == before


def test_annotate_ignition_without_layers_dir_is_a_no_op(tmp_path: Path):
    outbox = _outbox(tmp_path)
    assert weld.annotate_ignition(
        None, outbox, run_id="run-a", body="the-loom#the-weld",
    ) == []
    assert relics.read_reported(outbox) == []


# ── capture ──────────────────────────────────────────────────────────

def test_capture_refs_lands_produce_on_the_item(tmp_path: Path):
    layers = _layers(tmp_path)
    records = [
        {"kind": "item", "address": WELD_ADDRESS},
        {"kind": "pr", "number": 978},
        {"kind": "merge", "pr": 978},
        {"kind": "issue", "number": 999, "repo": "other/project"},
    ]

    welded = weld.capture_refs(
        layers, records=records, origin_repo="hugimuni-labs/brnrd",
    )

    assert welded == {
        WELD_ADDRESS: ["hugimuni-labs/brnrd#978", "other/project#999"],
    }
    text = (layers / "the-loom.md").read_text(encoding="utf-8")
    assert (
        "refs: hugimuni-labs/brnrd#972 · design-work-layers.md · "
        "hugimuni-labs/brnrd#978 · other/project#999" in text
    )
    # Re-running finalize is a no-op — same records, nothing duplicated.
    assert weld.capture_refs(
        layers, records=records, origin_repo="hugimuni-labs/brnrd",
    ) == {}
    assert (layers / "the-loom.md").read_text(encoding="utf-8") == text


def test_capture_refs_skips_an_address_that_stopped_resolving(
    tmp_path: Path, capsys,
):
    layers = _layers(tmp_path)
    before = (layers / "the-loom.md").read_text(encoding="utf-8")
    records = [
        {"kind": "item", "address": "the-loom#retired-item"},
        {"kind": "pr", "number": 978},
    ]

    assert weld.capture_refs(
        layers, records=records, origin_repo="hugimuni-labs/brnrd",
    ) == {}
    assert (layers / "the-loom.md").read_text(encoding="utf-8") == before
    assert "the-loom#retired-item" in capsys.readouterr().out


def test_capture_refs_without_forge_produce_touches_nothing(tmp_path: Path):
    layers = _layers(tmp_path)
    before = (layers / "the-loom.md").read_text(encoding="utf-8")
    records = [
        {"kind": "item", "address": WELD_ADDRESS},
        {"kind": "commit", "sha": "abc1234", "subject": "feat: x"},
    ]
    assert weld.capture_refs(
        layers, records=records, origin_repo="hugimuni-labs/brnrd",
    ) == {}
    assert (layers / "the-loom.md").read_text(encoding="utf-8") == before


# ── the manifest carries the address into the ledger row ─────────────

def test_item_relic_flows_through_collect_once(tmp_path: Path):
    """`relics.collect` is what `run_ledger.build_closed_run_row` records as
    the row's `external_refs` — an item relic reaches the dashboard through
    it with no new field, and a doubled report collapses to one record."""
    outbox = _outbox(tmp_path)
    relics.append(outbox, "item", address=WELD_ADDRESS)
    relics.append(outbox, "item", address=WELD_ADDRESS)
    relics.append(outbox, "item", address="the-loom#another")

    collected = relics.collect(
        None, branch=None, seed_ref=None, outbox_dir=outbox,
    )

    assert collected == [
        {"kind": "item", "address": WELD_ADDRESS},
        {"kind": "item", "address": "the-loom#another"},
    ]

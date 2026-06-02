"""Tests for the diffense pack -> Markdown PR-body projection.

The projection is the *lossy fallback* surface (design → "PR body as a
lossy projection"): a forge-only reviewer should get orientation, the
flagged doubts, the touched surface, and a reading order — all derived
mechanically from the pack. These tests pin which cards feed which
section, the concern/deferred split by subkind, and the embed round-trip.
"""

from brr.diffense import prbody


def _pack() -> dict:
    """A pack exercising every projected section."""
    return {
        "schema_version": "0.1-test",
        "metadata": {"generated_at": "2026-06-01"},
        "reading_order": ["summary:x", "unc:doubt", "unc:later", "walk:flow", "item:y"],
        "cards": [
            {
                "id": "summary:x",
                "kind": "summary",
                "identity": {"label": "the change in shape"},
                "lore": {"descriptive": "Three arcs braided into one change."},
                "shape": {
                    "arcs": [
                        {"theme": "fix", "what": "the round-trip now closes"},
                        {"theme": "refactor", "what": "split the monolith"},
                    ],
                    "surface_area": ["src/x.py", "kb/"],
                },
                "provenance": {},
            },
            {
                "id": "unc:doubt",
                "kind": "uncertainty",
                "subkind": "concern",
                "severity": "med",
                "headline": "the seen-id cap can drop an edited comment on a very busy PR",
                "proposed_resolution": "raise the cap or switch to a timestamp window",
                "identity": {"label": "trigger: dedup cap"},
                "provenance": {},
            },
            {
                "id": "unc:later",
                "kind": "uncertainty",
                "subkind": "follow-up",
                "severity": "low",
                "headline": "reactions-as-approve would make the loop feel native",
                "identity": {"label": "near-future: reactions"},
                "provenance": {},
            },
            {
                "id": "walk:flow",
                "kind": "walkthrough",
                "identity": {"label": "the round-trip"},
                "lore": {"descriptive": "poll -> task -> in-thread reply, end to end."},
                "provenance": {},
            },
            {
                "id": "item:y",
                "kind": "code-fn-edit",
                "identity": {"label": "f()", "file": "src/x.py", "symbol": "f"},
                "locator": {"local": "src/x.py:1"},
                "lore": {"descriptive": "edits f to close the loop. More detail here."},
                "provenance": {},
            },
        ],
    }


def test_title_prefers_metadata_then_summary_label():
    pack = _pack()
    # No metadata.pr.title -> summary label.
    assert prbody.pr_title(pack) == "the change in shape"
    pack["metadata"]["pr"] = {"title": "fix: close the loop"}
    assert prbody.pr_title(pack) == "fix: close the loop"


def test_title_falls_back_when_no_summary():
    assert prbody.pr_title({"cards": []}, fallback="brr/task-x") == "brr/task-x"


def test_body_has_summary_with_shape_and_surface():
    body = prbody.project_pr_body(_pack())
    assert "## Summary" in body
    assert "Three arcs braided into one change." in body
    assert "**Shape**" in body
    assert "_fix_ — the round-trip now closes" in body
    assert "**Surface**" in body and "`src/x.py`" in body


def test_concerns_and_deferred_split_by_subkind():
    body = prbody.project_pr_body(_pack())
    # concern subkind -> ⚠ Concerns; follow-up -> Deferred / open.
    assert "## ⚠ Concerns" in body
    assert "**[concern · med]** the seen-id cap" in body
    assert "_resolution:_ raise the cap" in body
    assert "## Deferred / open" in body
    assert "**[follow-up · low]** reactions-as-approve" in body
    # The concern count line orients the reader to read doubts first.
    assert "1 concern(s) (1 med) flagged below" in body


def test_narrative_from_walkthrough_card():
    body = prbody.project_pr_body(_pack())
    assert "## Narrative" in body
    assert "poll -> task -> in-thread reply" in body


def test_touched_lists_change_cards_not_doubts():
    # Project without the embed so the assertion sees only the prose, not
    # the verbatim pack JSON in the trailing marker.
    body = prbody.project_pr_body(_pack(), embed_pack=False)
    assert "## Touched" in body
    assert "- `src/x.py` — edits f to close the loop" in body
    # The gloss is trimmed to its first sentence in Touched.
    assert "More detail here" not in body


def test_reading_order_maps_ids_to_labels():
    body = prbody.project_pr_body(_pack())
    assert "## Reading order" in body
    assert "1. the change in shape" in body
    assert "5. f()" in body


def test_sections_absent_when_pack_lacks_material():
    minimal = {
        "schema_version": "0.1-test",
        "metadata": {},
        "cards": [
            {
                "id": "summary:x",
                "kind": "summary",
                "identity": {"label": "tiny"},
                "lore": {"descriptive": "a tiny change"},
                "provenance": {},
            }
        ],
    }
    body = prbody.project_pr_body(minimal)
    assert "## Summary" in body
    assert "## ⚠ Concerns" not in body
    assert "## Deferred / open" not in body
    assert "## Narrative" not in body
    assert "## Touched" not in body


def test_render_banner_present_when_url_given():
    body = prbody.project_pr_body(_pack(), render_url="https://brnrd.example/r/abc")
    assert "**Interactive review:** https://brnrd.example/r/abc" in body
    # The banner sits above the Summary so it's the first thing a reviewer sees.
    assert body.index("Interactive review") < body.index("## Summary")


def test_render_banner_absent_without_url():
    body = prbody.project_pr_body(_pack())
    assert "Interactive review" not in body


def test_embed_round_trips_through_extract():
    pack = _pack()
    body = prbody.project_pr_body(pack, embed_pack=True)
    assert prbody.PACK_MARKER_BEGIN in body
    assert prbody.extract_pack(body) == pack


def test_embed_omitted_when_disabled():
    body = prbody.project_pr_body(_pack(), embed_pack=False)
    assert prbody.PACK_MARKER_BEGIN not in body
    assert prbody.extract_pack(body) is None


def test_embed_dropped_when_oversized():
    pack = _pack()
    # Inflate the pack past the body budget; the prose must still render
    # and the embed must degrade to a pointer rather than blow the limit.
    pack["cards"][0]["lore"]["descriptive"] = "x" * 70000
    body = prbody.project_pr_body(pack, embed_pack=True)
    assert prbody.PACK_MARKER_BEGIN not in body
    assert "Full pack omitted" in body
    assert "## Summary" in body

"""Tests for the diffense review-pack contract (schema + --check engine).

Two layers: structural/graph/clamp checks run without a repo (so they
don't couple to the working tree), and locator resolution runs against a
synthetic tmp repo. A separate test pins that the real hand-authored
prototype pack stays structurally valid as the schema evolves.
"""

import json
from pathlib import Path

import pytest

from brr.diffense import pack as P


def _min_pack() -> dict:
    """A minimal pack that is clean under structure/graph/clamp checks."""
    return {
        "schema_version": "0.1-test",
        "metadata": {"generated_at": "2026-06-01"},
        "reading_order": ["summary:x", "item:y"],
        "cards": [
            {
                "id": "summary:x",
                "kind": "summary",
                "identity": {"label": "the change in shape"},
                "lore": {"descriptive": "a short honest summary"},
                "provenance": {},
            },
            {
                "id": "item:y",
                "kind": "code-fn-edit",
                "identity": {
                    "label": "f()",
                    "file": "src/x.py",
                    "symbol": "f",
                    "lines": [1, 2],
                },
                "locator": {"local": "src/x.py:1", "forge": "https://example/x"},
                "lore": {"descriptive": "edits f to do the thing"},
                "provenance": {"commit": "abc1234"},
            },
        ],
    }


def _codes(issues, level=None):
    return {i.code for i in issues if level is None or i.level == level}


# ── load_pack ────────────────────────────────────────────────────────


def test_load_pack_rejects_bad_json(tmp_path):
    bad = tmp_path / "pack.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(P.PackError):
        P.load_pack(bad)


def test_load_pack_rejects_non_object(tmp_path):
    arr = tmp_path / "pack.json"
    arr.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(P.PackError):
        P.load_pack(arr)


def test_load_pack_missing_file_raises(tmp_path):
    with pytest.raises(P.PackError):
        P.load_pack(tmp_path / "nope.json")


# ── structure ────────────────────────────────────────────────────────


def test_min_pack_is_clean_without_repo():
    issues = P.check_pack(_min_pack(), repo_root=None)
    assert not P.has_errors(issues)
    assert issues == []


def test_missing_schema_version_and_metadata_are_errors():
    pack = _min_pack()
    del pack["schema_version"]
    del pack["metadata"]
    codes = _codes(P.validate_structure(pack), "error")
    assert "pack.schema-version" in codes
    assert "pack.metadata" in codes


def test_empty_cards_is_error():
    pack = _min_pack()
    pack["cards"] = []
    assert "pack.cards" in _codes(P.validate_structure(pack), "error")


def test_duplicate_card_id_is_error():
    pack = _min_pack()
    pack["cards"][1]["id"] = "summary:x"
    assert "card.id-duplicate" in _codes(P.check_pack(pack), "error")


def test_missing_gloss_is_error():
    pack = _min_pack()
    pack["cards"][1]["lore"] = {}
    assert "card.gloss" in _codes(P.check_pack(pack), "error")


def test_card_naming_a_file_requires_locator():
    pack = _min_pack()
    del pack["cards"][1]["locator"]
    assert "card.locator" in _codes(P.check_pack(pack), "error")


def test_summary_without_file_does_not_need_locator():
    pack = _min_pack()
    # summary card has no identity.file -> no locator demanded
    assert "card.locator" not in _codes(P.check_pack(pack, repo_root=None))


def test_multiple_summary_cards_is_error():
    pack = _min_pack()
    pack["cards"][1]["kind"] = "summary"
    assert "pack.multi-summary" in _codes(P.check_pack(pack), "error")


def test_no_summary_card_is_warning_not_error():
    pack = _min_pack()
    pack["cards"][0]["kind"] = "code-fn-new"
    pack["cards"][0]["identity"] = {"label": "g()", "file": "src/g.py"}
    pack["cards"][0]["locator"] = {"local": "src/g.py"}
    issues = P.check_pack(pack, repo_root=None)
    assert "pack.no-summary" in _codes(issues, "warning")
    assert not P.has_errors(issues)


# ── open taxonomy ────────────────────────────────────────────────────


def test_unknown_kind_is_warning_not_error():
    pack = _min_pack()
    pack["cards"][1]["kind"] = "code-telepathy"
    issues = P.check_pack(pack, repo_root=None)
    assert "card.kind-unknown" in _codes(issues, "warning")
    assert not P.has_errors(issues)


def test_custom_kind_is_accepted_clean():
    pack = _min_pack()
    pack["cards"][1]["kind"] = "custom"
    assert not P.has_errors(P.check_pack(pack, repo_root=None))


# ── uncertainty ──────────────────────────────────────────────────────


def test_uncertainty_requires_subkind_and_severity():
    pack = _min_pack()
    pack["cards"][1] = {
        "id": "unc:z",
        "kind": "uncertainty",
        "identity": {"label": "a worry"},
        "lore": {"descriptive": "something might be off"},
        "provenance": {},
    }
    pack["reading_order"] = ["summary:x", "unc:z"]
    codes = _codes(P.check_pack(pack, repo_root=None), "error")
    assert "uncertainty.subkind" in codes
    assert "uncertainty.severity" in codes


def test_uncertainty_unusual_severity_is_warning():
    pack = _min_pack()
    pack["cards"][1] = {
        "id": "unc:z",
        "kind": "uncertainty",
        "subkind": "concern",
        "severity": "catastrophic",
        "identity": {"label": "a worry"},
        "headline": "the gloss as a headline",
        "provenance": {},
    }
    pack["reading_order"] = ["summary:x", "unc:z"]
    issues = P.check_pack(pack, repo_root=None)
    assert "uncertainty.severity-unknown" in _codes(issues, "warning")
    assert not P.has_errors(issues)


def test_uncertainty_headline_counts_as_gloss():
    pack = _min_pack()
    pack["cards"][1] = {
        "id": "unc:z",
        "kind": "uncertainty",
        "subkind": "assumption",
        "severity": "low",
        "identity": {"label": "an assumption"},
        "headline": "I assumed X because the prompt didn't say",
        "provenance": {},
    }
    pack["reading_order"] = ["summary:x", "unc:z"]
    assert "card.gloss" not in _codes(P.check_pack(pack, repo_root=None))


# ── card graph ───────────────────────────────────────────────────────


def test_dangling_card_edge_is_error():
    pack = _min_pack()
    pack["cards"][1]["lateral_edges"] = [{"type": "calls", "target": "item:ghost"}]
    assert "edge.unknown" in _codes(P.check_pack(pack), "error")


def test_free_reference_edge_is_not_an_error():
    pack = _min_pack()
    # a bare symbol target (not a card namespace) is a legitimate free ref
    pack["cards"][1]["lateral_edges"] = [
        {"type": "called-by", "target": "some_module._helper"}
    ]
    assert not P.has_errors(P.check_pack(pack, repo_root=None))


def test_resolving_card_edge_is_clean():
    pack = _min_pack()
    pack["cards"][1]["lateral_edges"] = [{"type": "part-of", "target": "summary:x"}]
    assert not P.has_errors(P.check_pack(pack, repo_root=None))


def test_reading_order_unknown_card_is_error():
    pack = _min_pack()
    pack["reading_order"] = ["summary:x", "item:y", "item:ghost"]
    assert "reading-order.unknown" in _codes(P.check_pack(pack), "error")


def test_card_absent_from_reading_order_warns():
    pack = _min_pack()
    pack["reading_order"] = ["summary:x"]
    assert "reading-order.missing" in _codes(P.check_pack(pack), "warning")


def test_summary_should_read_first_warning():
    pack = _min_pack()
    pack["reading_order"] = ["item:y", "summary:x"]
    assert "reading-order.summary-first" in _codes(P.check_pack(pack), "warning")


def test_dangling_walkthrough_member_is_error():
    pack = _min_pack()
    pack["cards"][1]["members"] = [{"order": 1, "card": "item:ghost"}]
    assert "member.unknown" in _codes(P.check_pack(pack), "error")


# ── locator resolution (against a synthetic repo) ────────────────────


def _repo_with(tmp_path: Path, rel: str, body: str) -> Path:
    f = tmp_path / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(body, encoding="utf-8")
    return tmp_path


def test_locator_unresolved_file_is_error(tmp_path):
    pack = _min_pack()
    pack["cards"][1]["locator"] = {"local": "src/missing.py:1"}
    pack["cards"][1]["identity"]["file"] = "src/missing.py"
    issues = P.resolve_locators(pack, tmp_path)
    assert "locator.unresolved" in _codes(issues, "error")


def test_locator_line_out_of_range_is_error(tmp_path):
    repo = _repo_with(tmp_path, "src/x.py", "def f():\n    return 1\n")
    pack = _min_pack()
    pack["cards"][1]["locator"] = {"local": "src/x.py:99"}
    assert "locator.line-out-of-range" in _codes(P.resolve_locators(pack, repo), "error")


def test_locator_symbol_not_found_warns(tmp_path):
    repo = _repo_with(tmp_path, "src/x.py", "def f():\n    return 1\n")
    pack = _min_pack()
    pack["cards"][1]["identity"]["symbol"] = "get_with_etag"
    pack["cards"][1]["locator"] = {"local": "src/x.py:1"}
    assert "locator.symbol-not-found" in _codes(P.resolve_locators(pack, repo), "warning")


def test_valid_locator_with_present_symbol_is_clean(tmp_path):
    repo = _repo_with(tmp_path, "src/x.py", "def f():\n    return 1\n")
    pack = _min_pack()  # symbol "f", line 1, file exists
    assert P.resolve_locators(pack, repo) == []


def test_dotted_symbol_matches_on_leaf(tmp_path):
    repo = _repo_with(tmp_path, "src/x.py", "def helper():\n    return 1\n")
    pack = _min_pack()
    pack["cards"][1]["identity"]["symbol"] = "mod.helper"
    pack["cards"][1]["locator"] = {"local": "src/x.py:1"}
    assert P.resolve_locators(pack, repo) == []


def test_prose_symbol_is_not_symbol_checked(tmp_path):
    repo = _repo_with(tmp_path, "src/x.py", "def f():\n    return 1\n")
    pack = _min_pack()
    pack["cards"][1]["identity"]["symbol"] = "whole package split"
    pack["cards"][1]["locator"] = {"local": "src/x.py:1"}
    assert P.resolve_locators(pack, repo) == []


def test_locator_escaping_repo_is_error(tmp_path):
    pack = _min_pack()
    pack["cards"][1]["locator"] = {"local": "../../etc/passwd:1"}
    pack["cards"][1]["identity"]["file"] = "../../etc/passwd"
    assert "locator.escapes-repo" in _codes(P.resolve_locators(pack, tmp_path), "error")


# ── clamp lints ──────────────────────────────────────────────────────


def test_empty_conditional_axis_warns():
    pack = _min_pack()
    pack["cards"][1]["lateral_edges"] = []
    assert "clamp.emit-iff-honest" in _codes(P.clamp_lints(pack), "warning")


def test_oversized_gloss_warns():
    pack = _min_pack()
    pack["cards"][1]["lore"]["descriptive"] = "x" * (P._GLOSS_CHAR_BUDGET + 1)
    assert "clamp.sharp" in _codes(P.clamp_lints(pack), "warning")


def test_prescriptive_phrase_warns():
    pack = _min_pack()
    pack["cards"][1]["lore"]["descriptive"] = "You should always use this pattern."
    assert "clamp.non-prescriptive" in _codes(P.clamp_lints(pack), "warning")


# ── the real prototype pack ──────────────────────────────────────────


def _prototype_path() -> Path:
    # Was kb/diffense-prototype-pr64-pack.json; kb/ moved out of the repo
    # (2026-07-09, plan-kb-out-of-repo-migration.md) so the test fixture
    # keeps its own copy here rather than depending on it.
    return Path(__file__).resolve().parent / "diffense-prototype-pr64-pack.json"


def test_prototype_pack_structure_is_valid():
    pack = P.load_pack(_prototype_path())
    issues = P.check_pack(pack, repo_root=None)
    assert not P.has_errors(issues), [i.format() for i in issues if i.level == "error"]

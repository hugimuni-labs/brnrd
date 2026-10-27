"""``cut:`` parsing — the bolt's declared half (design-the-bolt.md)."""

from __future__ import annotations

from brr import cut_verb, protocol


# ── the marker ───────────────────────────────────────────────────────


# ── unknown keys (#1187's typo-safety lesson) ───────────────────────


# ── asks ─────────────────────────────────────────────────────────────


def test_parse_cut_asks_nested_dict_form_round_trips_through_the_real_parser():
    """The prior test only constructs the already-parsed ``fm`` dict by
    hand; this one drives actual frontmatter *text* through
    ``protocol.parse_frontmatter`` first, confirming the docstring's "both
    round-trip" claim against the real grammar rather than a hand-built
    stand-in — indented nesting is the one syntax that reaches
    ``parse_cut`` as a genuine one-key dict (#1219)."""
    text = (
        "---\n"
        "cut: true\n"
        "asks:\n"
        "  evt-1-txwl:\n"
        "    disposition: answered\n"
        "---\n"
        "Done.\n"
    )
    fm = protocol.parse_frontmatter(text)
    assert fm["asks"] == {"evt-1-txwl": {"disposition": "answered"}}
    declaration, error = cut_verb.parse_cut(fm)
    assert error is None
    assert declaration.asks[0] == cut_verb.AskDisposition(
        event="evt-1-txwl", disposition="answered",
    )


def test_parse_cut_asks_inline_flow_mapping_syntax_is_not_accepted():
    """The docstring used to show ``evt-...: {disposition: answered}`` as
    literal syntax to write — but ``protocol._parse_block`` has no brace
    grammar, so that line parses as a plain string, not a dict, and the
    declaration is refused. This is the exact drop #1219 measured live
    (three grammar-refused ``cut:`` directives in three minutes, the first
    naming this precise disposition string)."""
    text = (
        "---\n"
        "cut: true\n"
        "asks:\n"
        "  evt-1-txwl: {disposition: answered}\n"
        "---\n"
        "Done.\n"
    )
    fm = protocol.parse_frontmatter(text)
    # Confirm the shape that actually reaches the parser: a literal string,
    # not a nested dict — the root cause, not just its symptom.
    assert fm["asks"] == {"evt-1-txwl": "{disposition: answered}"}
    declaration, error = cut_verb.parse_cut(fm)
    assert declaration is None
    assert "unrecognised disposition" in error
    assert "{disposition: answered}" in error


# ── produce ──────────────────────────────────────────────────────────


# ── owed ─────────────────────────────────────────────────────────────


# ── decisions / spend / next — never validated, carried as-is ──────


# ── strands (#1197) ──────────────────────────────────────────────────



"""``cut:`` parsing — the bolt's declared half (design-the-bolt.md)."""

from __future__ import annotations

from brr import cut_verb, protocol


# ── the marker ───────────────────────────────────────────────────────


def test_parse_cut_bare_marker_is_a_legal_minimal_bolt():
    """Stopping is a result: a bare ``cut: true`` with nothing else declared
    parses cleanly, no fields required."""
    declaration, error = cut_verb.parse_cut({"cut": "true"})
    assert error is None
    assert declaration.asks == ()
    assert declaration.decisions == ()
    assert declaration.produce is None
    assert declaration.owed_none is True
    assert declaration.owed == ()
    assert declaration.spend is None
    assert declaration.next is None


def test_parse_cut_accepts_an_empty_marker():
    declaration, error = cut_verb.parse_cut({"cut": ""})
    assert error is None
    assert declaration is not None


def test_parse_cut_rejects_an_unrecognised_marker():
    declaration, error = cut_verb.parse_cut({"cut": "maybe"})
    assert declaration is None
    assert "not a recognised marker" in error


# ── unknown keys (#1187's typo-safety lesson) ───────────────────────


def test_parse_cut_refuses_an_unknown_key_by_name():
    declaration, error = cut_verb.parse_cut({"cut": "true", "decision": "kept"})
    assert declaration is None
    assert "decision" in error
    assert "unrecognised field" in error


def test_parse_cut_refuses_several_unknown_keys_all_named():
    declaration, error = cut_verb.parse_cut(
        {"cut": "true", "foo": "1", "bar": "2"}
    )
    assert declaration is None
    assert "foo" in error and "bar" in error


# ── asks ─────────────────────────────────────────────────────────────


def test_parse_cut_asks_flow_scalar_mapping_form():
    declaration, error = cut_verb.parse_cut(
        {"cut": "true", "asks": {"evt-1-txwl": "answered"}}
    )
    assert error is None
    assert declaration.asks == (
        cut_verb.AskDisposition(event="evt-1-txwl", disposition="answered"),
    )


def test_parse_cut_asks_accepts_deferred_and_noted_with_a_tail():
    declaration, error = cut_verb.parse_cut(
        {
            "cut": "true",
            "asks": {
                "evt-1": "deferred:schedule.md",
                "evt-2": "noted:duplicate ask",
            },
        }
    )
    assert error is None
    dispositions = {row.event: row.disposition for row in declaration.asks}
    assert dispositions == {
        "evt-1": "deferred:schedule.md",
        "evt-2": "noted:duplicate ask",
    }


def test_parse_cut_asks_rejects_a_disposition_with_no_recognised_shape():
    declaration, error = cut_verb.parse_cut(
        {"cut": "true", "asks": {"evt-1": "sort-of"}}
    )
    assert declaration is None
    assert "unrecognised disposition" in error


def test_parse_cut_asks_rejects_a_bare_deferred_with_no_tail():
    declaration, error = cut_verb.parse_cut(
        {"cut": "true", "asks": {"evt-1": "deferred:"}}
    )
    assert declaration is None
    assert "unrecognised disposition" in error


def test_parse_cut_asks_nested_dict_form_also_works():
    """A value may be a one-key dict (``{disposition: answered}``) as well
    as the bare flow-scalar string — both round-trip through
    ``protocol.parse_frontmatter``'s nested-dict grammar."""
    declaration, error = cut_verb.parse_cut(
        {"cut": "true", "asks": {"evt-1": {"disposition": "answered"}}}
    )
    assert error is None
    assert declaration.asks[0].disposition == "answered"


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


def test_parse_cut_asks_list_of_dicts_form_is_forward_compatible():
    declaration, error = cut_verb.parse_cut(
        {
            "cut": "true",
            "asks": [{"event": "evt-1", "disposition": "answered"}],
        }
    )
    assert error is None
    assert declaration.asks[0] == cut_verb.AskDisposition(
        event="evt-1", disposition="answered"
    )


def test_parse_cut_asks_list_entry_missing_event_is_refused():
    declaration, error = cut_verb.parse_cut(
        {"cut": "true", "asks": [{"disposition": "answered"}]}
    )
    assert declaration is None
    assert "missing its event" in error


def test_parse_cut_asks_rejects_a_non_mapping_non_list():
    declaration, error = cut_verb.parse_cut({"cut": "true", "asks": "evt-1"})
    assert declaration is None
    assert "neither a mapping nor a list" in error


# ── produce ──────────────────────────────────────────────────────────


def test_parse_cut_produce_attested():
    declaration, error = cut_verb.parse_cut({"cut": "true", "produce": "attested"})
    assert error is None
    assert declaration.produce == "attested"


def test_parse_cut_produce_none():
    declaration, error = cut_verb.parse_cut({"cut": "true", "produce": "none"})
    assert error is None
    assert declaration.produce == "none"


def test_parse_cut_produce_rejects_an_unrecognised_value():
    declaration, error = cut_verb.parse_cut({"cut": "true", "produce": "maybe"})
    assert declaration is None
    assert "must be 'attested' or 'none'" in error


# ── owed ─────────────────────────────────────────────────────────────


def test_parse_cut_owed_none_is_the_default():
    declaration, error = cut_verb.parse_cut({"cut": "true"})
    assert error is None
    assert declaration.owed_none is True
    assert declaration.owed == ()


def test_parse_cut_owed_none_string_is_accepted():
    declaration, error = cut_verb.parse_cut({"cut": "true", "owed": "none"})
    assert error is None
    assert declaration.owed_none is True


def test_parse_cut_owed_carried_row_dict_form():
    declaration, error = cut_verb.parse_cut(
        {
            "cut": "true",
            "owed": {
                "the-notes-repair": {
                    "ref": "the notes-health repair",
                    "why": "ran out of budget",
                    "where": "schedule.md",
                }
            },
        }
    )
    assert error is None
    assert declaration.owed_none is False
    [row] = declaration.owed
    assert row.label == "the-notes-repair"
    assert row.ref == "the notes-health repair"
    assert row.why == "ran out of budget"
    assert row.where == "schedule.md"


def test_parse_cut_owed_row_missing_why_is_refused():
    declaration, error = cut_verb.parse_cut(
        {"cut": "true", "owed": {"x": {"ref": "the thing"}}}
    )
    assert declaration is None
    assert "missing ref or why" in error


def test_parse_cut_owed_row_not_a_mapping_is_refused():
    declaration, error = cut_verb.parse_cut(
        {"cut": "true", "owed": {"x": "just a string"}}
    )
    assert declaration is None
    assert "must carry a ref and a why" in error


def test_parse_cut_owed_rejects_a_non_none_non_mapping():
    declaration, error = cut_verb.parse_cut({"cut": "true", "owed": "later"})
    assert declaration is None
    assert "neither 'none' nor a mapping" in error


def test_parse_cut_owed_list_of_dicts_form_is_forward_compatible():
    declaration, error = cut_verb.parse_cut(
        {
            "cut": "true",
            "owed": [{"ref": "the rollout", "why": "still open", "where": "next run"}],
        }
    )
    assert error is None
    [row] = declaration.owed
    assert row.ref == "the rollout"
    assert row.where == "next run"


# ── decisions / spend / next — never validated, carried as-is ──────


def test_parse_cut_decisions_dict_form_is_rendered_as_label_value_pairs():
    declaration, error = cut_verb.parse_cut(
        {"cut": "true", "decisions": {"notify.gate stopgap": "extended"}}
    )
    assert error is None
    assert declaration.decisions == ("notify.gate stopgap: extended",)


def test_parse_cut_decisions_accepts_anything_unvalidated():
    declaration, error = cut_verb.parse_cut(
        {"cut": "true", "decisions": "kept the fallback"}
    )
    assert error is None
    assert declaration.decisions == ("kept the fallback",)


def test_parse_cut_spend_and_next_are_carried_verbatim():
    declaration, error = cut_verb.parse_cut(
        {"cut": "true", "spend": "~$12, 56m", "next": "schedule entry ref"}
    )
    assert error is None
    assert declaration.spend == "~$12, 56m"
    assert declaration.next == "schedule entry ref"


def test_parse_cut_empty_spend_and_next_are_none_not_empty_string():
    declaration, error = cut_verb.parse_cut({"cut": "true", "spend": "", "next": ""})
    assert error is None
    assert declaration.spend is None
    assert declaration.next is None

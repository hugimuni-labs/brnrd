"""A port field with a newline survives the trip the loom makes: write, drain, fact."""
import pytest

from brr.loom.runtime.ledger import read_facts
from brr.loom.runtime.loom import _ingest
from brr.loom.runtime.port import _front, parse_frontmatter
from test_wishes import command, ledger  # noqa: F401  (ledger is a fixture)

# A colon on the second line made it a field of its own and truncated the
# value in silence; without one the loom refused the whole port file.
SECOND_LINES = ["Part 1 merged.\nkey: next is (c), not a field", 'Part 1 merged.\n"next" is (c)']


@pytest.mark.parametrize("text", SECOND_LINES)
def test_a_two_line_molt_why_and_note_reach_the_ledger_whole(ledger, text):
    home, seat = ledger
    command(home, seat, "molt", "--why", text)
    command(home, seat, "send", "--re", "p-test/none", "--to", "thread:work", "--note", text)
    _ingest(home)
    facts = read_facts(home)
    assert not list((seat / "port" / "out").glob("*.md"))
    assert [f.data["why"] for f in facts if f.kind == "molt"] == [text]
    assert text in [f.data.get("body") for f in facts if f.kind == "letter"]
    assert [f.data["why"] for f in facts if f.kind == "note"] == [text]


def test_a_single_line_is_written_byte_for_byte_as_before():
    # The loaded loom may be older than the body writing the port file.
    assert _front({"kind": "molt", "why": "plain: one line"}) == "---\nkind: molt\nwhy: plain: one line\n---\n"


@pytest.mark.parametrize("value", ['"quoted"', "a\n---\nb", "x\\ny"])
def test_a_value_that_would_break_the_frontmatter_round_trips(value):
    assert parse_frontmatter(_front({"kind": "molt", "why": value}))["why"] == value

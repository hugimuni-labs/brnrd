"""Two tails the first line-by-line read of step 1 found (#2223)."""

from brr.daemon2.facts import Fact
from brr.loom.runtime import ledger
from brr.loom.runtime.home import Home
from brr.loom.runtime.port import parse_boundary, render_boundary


def test_a_blank_line_inside_a_letter_survives_the_boundary():
    letter = Fact(kind="letter", by="person:p-a", id="p-a/x1", data={
        "id": "p-a/x1", "to": "thread:t", "from": "p-a",
        "body": "para one\n\npara two"})
    text = render_boundary("s-1", 1, "t", [letter], set(), {})
    assert parse_boundary(text).letters[0].body == "para one\n\npara two"


def test_append_after_a_torn_tail_keeps_the_ledger_readable(tmp_path):
    home = Home(tmp_path)
    ledger.append(home, Fact(kind="lease", by="loom", id="l1",
                             data={"thread": "t", "strand": "s-1", "gen": 1}))
    with open(home.facts_dir() / "loom.jsonl", "a") as handle:
        handle.write('{"kind":"lea')  # a writer killed mid-line
    ledger.append(home, Fact(kind="lease", by="loom", id="l2",
                             data={"thread": "u", "strand": "s-2", "gen": 1}))
    assert [fact.id for fact in ledger.read_facts(home)] == ["l1", "l2"]

"""Gen fencing, identity, and a torn last line."""

from brr.daemon2.facts import Fact

from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import LedgerConflict, append, read_facts
from brr.loom.runtime.project import attempts, holder, owed

STRAND = "s-ab12-aaaaaa"
OTHER = "s-ab12-bbbbbb"
LETTER = "p-test/ppppp"


def _fact(kind, by, data, ident, n):
    return Fact(kind=kind, by=by, data=data, id=ident, hlc=(n, 0, "ab12"),
                at="2026-10-08T00:00:00+00:00")


def _lease(strand, thread, gen, n, kind="lease"):
    return _fact(
        kind, "loom:ab12",
        {"thread": thread, "strand": strand, "gen": gen, "why": "nothing owed"}
        if kind == "released" else
        {"thread": thread, "strand": strand, "gen": gen},
        f"{kind}:{thread}:{gen}", n,
    )


def _letter():
    return _fact(
        "letter", "person:p-test",
        {"id": LETTER, "to": "thread:ta", "body": "ping", "from": "p-test"},
        LETTER, 2,
    )


def test_answer_from_a_stale_gen_leaves_the_letter_owed():
    facts = [
        _lease(STRAND, "ta", 1, 1),
        _letter(),
        _lease(STRAND, "ta", 1, 3, kind="released"),
        _fact("note", f"strand:{STRAND}",
              {"re": LETTER, "why": "late", "gen": 1}, f"{STRAND}/late", 4),
    ]
    assert [fact.data["id"] for fact in owed(facts, "ta")] == [LETTER]
    assert holder(facts, "ta") is None


def test_answer_during_the_live_gen_stays_handled_after_release():
    facts = [
        _lease(STRAND, "ta", 1, 1),
        _letter(),
        _fact("note", f"strand:{STRAND}",
              {"re": LETTER, "why": "done", "gen": 1}, f"{STRAND}/done", 3),
        _lease(STRAND, "ta", 1, 4, kind="released"),
    ]
    assert owed(facts, "ta") == []
    assert holder(facts, "ta") is None


def test_answer_from_the_wrong_holder_does_not_count():
    facts = [
        _lease(STRAND, "ta", 1, 1),
        _lease(OTHER, "tb", 1, 2),
        _letter(),
        _fact("note", f"strand:{OTHER}",
              {"re": LETTER, "why": "not mine", "gen": 1}, f"{OTHER}/nope", 4),
    ]
    assert [fact.data["id"] for fact in owed(facts, "ta")] == [LETTER]


def test_attempts_count_a_shown_death_and_ignore_one_that_was_not_shown():
    facts = [
        _lease(STRAND, "ta", 1, 1),
        _fact("body.started", "loom:ab12",
              {"strand": STRAND, "gen": 1, "pid": 11, "adapter": "fake"},
              "body.started:1", 2),
        _fact("shown", f"strand:{STRAND}",
              {"strand": STRAND, "gen": 1, "ids": [LETTER]}, f"{STRAND}/shown", 3),
        _fact("body.died", "loom:ab12",
              {"strand": STRAND, "gen": 1, "code": -9}, "body.died:1", 4),
        _fact("body.started", "loom:ab12",
              {"strand": STRAND, "gen": 1, "pid": 22, "adapter": "fake"},
              "body.started:2", 5),
        _fact("body.died", "loom:ab12",
              {"strand": STRAND, "gen": 1, "code": 1}, "body.died:2", 6),
    ]
    assert attempts(facts, LETTER) == 1


def test_same_id_different_content_is_rejected(tmp_path):
    home = Home(tmp_path)
    first = Fact(kind="attention", by="loom:ab12", id="attention:one", data={"why": "a"})
    append(home, first)
    clash = Fact(kind="attention", by="loom:ab12", id="attention:one", data={"why": "b"})
    try:
        append(home, clash)
    except LedgerConflict:
        pass
    else:
        raise AssertionError("different content was accepted")
    append(home, Fact(kind="attention", by="loom:ab12", id="attention:one", data={"why": "a"}))
    text = (home.facts_dir() / "loom.jsonl").read_text()
    assert text.count("\n") == 1
    assert [fact.data["why"] for fact in read_facts(home)] == ["a"]


def test_unterminated_last_line_is_skipped(tmp_path):
    home = Home(tmp_path)
    append(home, Fact(kind="attention", by="loom:ab12", id="attention:one",
                      data={"why": "kept"}))
    path = home.facts_dir() / "loom.jsonl"
    with path.open("ab") as handle:
        handle.write(b'{"v":1,"id":"cut"')
    assert [fact.id for fact in read_facts(home)] == ["attention:one"]

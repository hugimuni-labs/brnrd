"""Exercise wish states through real CLI processes and the runtime wake."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from io import StringIO

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import append, inject_letter, read_facts, fact_filename
from brr.loom.runtime.registry import UndeclaredFact
from brr.loom.runtime.selfrepo import init_self, room, run_wake
from brr.loom.runtime.wishes import wishes
from test_wake import cli, commit, write_thread


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.delenv("BRNRD_ROOM", raising=False)
    root = tmp_path / "home"
    init_self(root, person="test")
    write_thread(root / "self", "work", "Wish work\n\n## Plan\n\n- [ ] Build\n\n## Notes\nKeep notes")
    write_thread(root / "self", "other", "Another thread")
    commit(root / "self", "threads")
    clone = room(root, "s-aaaa-test")
    home = Home(root, install="aaaa")
    append(home, Fact(kind="router", by="loom:aaaa", data={"gen": 1}))
    append(home, Fact(kind="lease", by="loom:aaaa", data={
        "thread": "work", "strand": "s-aaaa-test", "gen": 1,
        "install": "aaaa", "router_gen": 1, "shell": "fake", "core": "fake", "repos": [],
    }))
    return home, clone.parent


def command(home, seat, *args, person=False, ok=True):
    result = cli([args[0], "--home" if person else "--room", str(home.root if person else seat), *args[1:]])
    assert result.returncode == (0 if ok else 1), result.stderr
    return result.stdout.strip() if ok else result.stderr


def view(home, ident):
    return wishes(read_facts(home))[ident]


def test_full_lifecycle_and_wake(ledger):
    home, seat = ledger
    letter = inject_letter(home.root, to="thread:work", body="Please build a thing")
    ident = command(home, seat, "wish", "new", "--line", "Build a thing", "--say", letter)
    assert view(home, ident).state == "heard"
    assert view(home, ident).origin == "said"
    command(home, seat, "wish", ident, "--read", "Implement it")
    assert view(home, ident).state == "read"
    command(home, seat, "wish", ident, "--read", "Only the first part", person=True)
    assert view(home, ident).state == "scoped"
    command(home, seat, "wish", ident, "--result", "pr:https://example.com/1", "--line", "First part")
    assert view(home, ident).state == "granting"
    command(home, seat, "wish", ident, "--result", "pr:https://example.com/1", "--line", "First part", "--deliver")
    assert view(home, ident).state == "delivered"
    output = StringIO()
    assert run_wake(seat, "work", stdout=output) == 0
    text = output.getvalue()
    assert text.index("## Plan") < text.index("## Wishes") < text.index("## Notes")
    assert ident + " · delivered" in text
    command(home, seat, "wish", ident, "--accept", "act", "--ref", "merged", person=True)
    assert view(home, ident).state == "accepted"
    assert ident not in command(home, seat, "wishes")
    assert ident in command(home, seat, "wishes", "--all")
    output = StringIO()
    assert run_wake(seat, "work", stdout=output) == 0
    assert ident not in output.getvalue()
    command(home, seat, "wish", ident, "--say", letter)
    assert view(home, ident).state == "delivered"
    command(home, seat, "wish", ident, "--drop", "No longer needed")
    assert view(home, ident).state == "dropped"
    assert view(home, ident).verdict["ref"] == "No longer needed"
    command(home, seat, "wish", ident, "--line", "Try again", "--thread", "other")
    assert view(home, ident).state == "delivered"
    assert ident not in command(home, seat, "wishes", "--thread", "work")
    for f in read_facts(home):
        if f.kind.startswith("wish"):
            assert fact_filename(f) == ("loom.jsonl" if f.by.startswith("person:") else "s-aaaa-test.jsonl")


def test_says_asks_and_send_stamp(ledger):
    home, seat = ledger
    letter = inject_letter(home.root, to="thread:work", body="A long enough letter")
    said = command(home, seat, "wish", "new", "--line", "said", "--say", letter + "@2+4")
    derived = command(home, seat, "wish", "new", "--line", "derived")
    ask = command(home, seat, "wish", "new", "--from", "resident", "--line", "Need your decision")
    assert view(home, derived).derived_unconfirmed
    assert view(home, said).says == [[letter, 2, 4]]
    assert view(home, said).origin == "said"
    command(home, seat, "wish", derived, "--read", "confirmed", person=True)
    assert not view(home, derived).derived_unconfirmed
    out = StringIO()
    assert run_wake(seat, "work", stdout=out) == 0
    section = out.getvalue().split("## Wishes\n")[1].split("## Notes")[0]
    assert section.splitlines()[0].startswith(ask)
    sent = command(home, seat, "send", "--to", "thread:other", "--re", letter, "--wish", "new:Stamped reply", "A reply")
    stamped = next(w for w in wishes(read_facts(home)).values() if w.line == "Stamped reply")
    assert stamped.says == [[letter, 0, None]]
    assert (seat / "port/out" / (sent.split("/")[1] + ".md")).exists()
    command(home, seat, "send", "--to", "thread:other", "--re", letter, "--wish", said, "Followup")
    assert view(home, said).says == [[letter, 2, 4], [letter, 0, None]]
    assert "asks (yours)" in command(home, seat, "wishes")
    append(home, Fact(kind="letter", by="strand:s-aaaa-test", id="s-aaaa-test/source", data={
        "id": "s-aaaa-test/source", "from": "s-aaaa-test", "to": "thread:other", "body": "Inference", "gen": 1}))
    command(home, seat, "wish", said, "--say", "s-aaaa-test/source")
    assert view(home, said).origin == "derived"


def test_refused_writes_have_no_effect(ledger):
    home, seat = ledger
    letter = inject_letter(home.root, to="thread:work", body="four")
    before = read_facts(home)
    assert "span past" in command(home, seat, "wish", "new", "--line", "Bad", "--say", letter + "@2+3", ok=False)
    assert "no letter" in command(home, seat, "wish", "new", "--line", "Bad", "--say", "missing", ok=False)
    assert "no wish" in command(home, seat, "send", "--to", "thread:other", "--re", letter, "--wish", "absent", "reply", ok=False)
    assert read_facts(home) == before
    assert not (seat / "port/out").exists()
    with pytest.raises(UndeclaredFact, match="does not declare stored_state"):
        append(home, Fact(kind="wish", by="person:test", data={"wish": "w-bad", "says": [],
            "from": "person", "line": "Bad", "thread": "work", "stored_state": "heard"}))
    ident = command(home, seat, "wish", "new", "--line", "Fine")
    assert "written by strand" in command(home, seat, "wish", ident, "--result", "file:x", "--line", "x", person=True, ok=False)


def test_unconfirmed_is_computed_at_read_time_and_fold_orders_facts(ledger):
    home, seat = ledger
    ident = command(home, seat, "wish", "new", "--line", "Old derived wish")
    fact = next(f for f in read_facts(home) if f.kind == "wish")
    old = replace(fact, at=(datetime.now(timezone.utc) - timedelta(days=61)).isoformat())
    w = wishes([old])[ident]
    assert w.unconfirmed()
    assert not w.unconfirmed(datetime.fromisoformat(old.at) + timedelta(days=59))
    command(home, seat, "wish", ident, "--read", "Read")
    command(home, seat, "wish", ident, "--accept", "word")
    command(home, seat, "wish", ident, "--line", "Reopen")
    facts = read_facts(home)
    assert wishes(reversed(facts))[ident].state == "read"
    assert wishes(facts)[ident].line == "Reopen"


def test_wake_bounds_each_line_and_reserves_its_budget(ledger, monkeypatch):
    home, seat = ledger
    ident = command(home, seat, "wish", "new", "--line", "recall\n" + "x" * 2000)
    command(home, seat, "wish", ident, "--result", "file:" + "r" * 1000,
            "--line", "Result", "--deliver")
    output = StringIO()
    assert run_wake(seat, "work", stdout=output) == 0
    prompt = output.getvalue()
    line = prompt.split("## Wishes\n")[1].splitlines()[0]
    assert len(line) < 400
    assert "recall x" in line
    monkeypatch.setenv("LOOM_BUDGET_BYTES", str(len(prompt.encode()) + 100))
    output = StringIO()
    assert run_wake(seat, "work", stdout=output) == 0
    assert len(output.getvalue().encode()) <= len(prompt.encode()) + 100
    assert ident in output.getvalue()

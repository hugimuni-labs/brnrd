"""An ask names the wish it unblocks, and waits on its thread's opener: real verbs."""
import json
import shutil
from pathlib import Path

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime.config import load_config
from brr.loom.runtime.ledger import append, inject_letter, read_facts
from brr.loom.runtime.lifecycle import main_snapshot
from brr.loom.runtime.registry import UndeclaredFact
from brr.loom.runtime.selfrepo import room
from brr.loom.runtime import snapshot
from brr.loom.runtime.wishes import Wish, waits_on, wishes
from test_wake import cli
from test_wishes import command, ledger, view  # noqa: F401  (ledger is a fixture)

FIXTURE = Path(__file__).parent / "fixtures" / "live-wishes-2026-10-20.jsonl"


def turns(home):
    doc = snapshot.assemble(home, read_facts(home), load_config(home.root))
    return {t["id"]: (t["turn"], t.get("waits_on")) for t in doc["threads"]}


def child(home, seat, thread, strand):
    """Open <thread> from the seat's thread for a wish, and lease a strand on it."""
    line = f"Split out {thread}"
    command(home, seat, "send", "--to", f"thread:{thread}", "--wish", f"new:{line}", "Fresh")
    wish = next(w.id for w in wishes(read_facts(home)).values() if w.line == line)
    append(home, Fact(kind="lease", by="loom:aaaa", data={
        "thread": thread, "strand": strand, "gen": 1,
        "install": "aaaa", "router_gen": 1, "shell": "fake", "core": "fake", "repos": [],
    }))
    return wish, room(home.root, strand).parent


def test_an_ask_without_the_wish_it_unblocks_is_refused(ledger):
    home, seat = ledger
    letter = inject_letter(home.root, to="thread:work", body="Please decide")
    root = command(home, seat, "wish", "new", "--line", "Decide", "--say", letter)
    stderr = command(home, seat, "wish", "new", "--from", "resident", "--line", "Need you", "--say", letter, ok=False)
    assert "ask --for <wish>" in stderr
    raw = Fact(kind="wish", by="strand:s-aaaa-test", id="w-rawask", data={
        "wish": "w-rawask", "says": [[letter, 0, None]], "from": "resident", "line": "x", "thread": "work"})
    with pytest.raises(UndeclaredFact, match="ask --for <wish>"):
        append(home, raw)
    # A touch cannot turn a parentless wish into an ask either.
    assert "ask --for <wish>" in command(home, seat, "wish", root, "--from", "resident", ok=False)
    assert view(home, root).sender == "person"
    ask = command(home, seat, "wish", "new", "--from", "resident", "--line", "Need you", "--parent", root)
    assert view(home, ask).parent == root
    assert set(wishes(read_facts(home))) == {root, ask}


def test_ask_writes_the_wish_and_its_recommendation(ledger):
    home, seat = ledger
    letter = inject_letter(home.root, to="thread:work", body="Please decide")
    root = command(home, seat, "wish", "new", "--line", "Decide", "--say", letter)
    ask = command(home, seat, "ask", "Pick a name", "--for", root, "--rec", "Call it loom")
    made = view(home, ask)
    assert (made.sender, made.parent, made.thread, made.line) == ("resident", root, "work", "Pick a name")
    assert (made.state, made.reading, made.reads[-1]["by"]) == ("read", "Call it loom", "resident")
    assert made.origin == "said"
    bare = command(home, seat, "ask", "Pick a colour", "--for", root)
    assert view(home, bare).state == "heard"
    assert "no wish w-missing" in command(home, seat, "ask", "x", "--for", "w-missing", ok=False)
    assert "--home" in command(home, seat, "ask", "x", "--for", root, "--home", str(home.root), ok=False)
    outside = cli(["ask", "x", "--for", root, "--home", str(home.root)])
    assert outside.returncode == 1 and "only a strand asks" in outside.stderr
    assert cli(["ask", "x", "--room", str(seat)]).returncode == 2
    assert len(wishes(read_facts(home))) == 3


def mail(seat, ident):
    """The letters a seat sent that carry an ask id."""
    out = seat / "port" / "out"
    return [p.read_text() for p in sorted(out.iterdir()) if ident in p.read_text()] if out.exists() else []


def test_an_ask_waits_on_the_opener_until_it_asks_onward(ledger):
    home, seat = ledger
    wish, mid = child(home, seat, "mid", "s-aaaa-mid")
    ask = command(home, mid, "ask", "Which half first?", "--for", wish)
    assert view(home, ask).thread == "mid"
    # The wait wakes its head: one letter to work, carrying the ask id.
    [letter] = mail(mid, ask)
    assert "to: thread:work" in letter and "Which half first?" in letter
    # mid was opened by work, and work has not asked anyone: work's turn, not the person's.
    assert turns(home)["mid"][0] != "yours"
    assert turns(home)["mid"][1] == ["thread:work"]
    assert f"{ask} · heard · Which half first? · - · waits on thread:work" in command(home, seat, "asks")
    assert "waits on thread:work" in command(home, seat, "wishes")
    onward = command(home, seat, "ask", "Which half first?", "--for", ask, "--rec", "the parser")
    assert mail(seat, onward) == []  # work's head is the operator: no thread to wake.
    # work asked onward for mid's ask; work has no opener on record, so both wait on the operator.
    assert turns(home)["mid"] == ("yours", ["operator"])
    assert turns(home)["work"] == ("yours", ["operator"])
    lines = command(home, seat, "asks").splitlines()
    assert any(line.startswith(ask) and line.endswith("waits on operator") for line in lines)
    assert any(line.startswith(onward) and line.endswith("waits on operator") for line in lines)
    # Once the person answers work's ask, the wait falls back to work.
    command(home, seat, "wish", onward, "--accept", "word", "--ref", "parser first", person=True)
    assert turns(home)["mid"][1] == ["thread:work"]
    assert "work" not in {t for t, (turn, _w) in turns(home).items() if turn == "yours"}


def test_an_ask_under_a_settled_opener_waits_on_the_operator_and_mails_nobody(ledger):
    home, seat = ledger
    _wish, mid = child(home, seat, "mid", "s-aaaa-mid")
    wish, leaf = child(home, mid, "leaf", "s-aaaa-leaf")
    command(home, seat, "settle", "mid", "--why", "split done")
    assert main_snapshot(home)["mid"]["status"] == "settled"
    ask = command(home, leaf, "ask", "Keep going?", "--for", wish)
    assert mail(leaf, ask) == []
    assert turns(home)["leaf"] == ("yours", ["operator"])


def test_the_chain_reaches_a_person_and_survives_a_loop():
    def ask(ident, thread, parent):
        return Wish(ident, sender="resident", thread=thread, parent=parent, line="x")
    found = {w.id: w for w in (ask("a", "leaf", "w-root"), ask("b", "mid", "a"), ask("c", "top", "b"),
                               Wish("w-root", thread="leaf"))}
    main = {"leaf": {"opened-by": "thread:mid"}, "mid": {"opened-by": "thread:top"},
            "top": {"opened-by": "person:arseni"}}
    assert waits_on(found, main) == {"a": "person:arseni", "b": "person:arseni", "c": "person:arseni"}
    # An ask for a different wish does not carry another thread's wait.
    found["b"] = ask("b", "mid", "w-root")
    assert waits_on(found, main)["a"] == "thread:mid"
    # Two threads that opened each other and asked each other: the wait stops, it does not spin.
    loop = {w.id: w for w in (ask("x", "p", "y"), ask("y", "q", "x"))}
    heads = waits_on(loop, {"p": {"opened-by": "thread:q"}, "q": {"opened-by": "thread:p"}})
    assert heads == {"x": "thread:p", "y": "thread:q"}
    # An opener that is settled, or not on main, will never wake for it: the operator's.
    main["mid"]["status"] = "settled"
    assert waits_on(found, main)["a"] == "operator"
    assert waits_on(found, {"leaf": {"opened-by": "thread:gone"}})["a"] == "operator"


def test_the_live_ledger_replays_with_no_parentless_ask(ledger):
    home, seat = ledger
    shutil.copy(FIXTURE, home.facts_dir() / "live.jsonl")
    births = [json.loads(line) for line in FIXTURE.read_text().splitlines()]
    births = [b for b in births if b["kind"] == "wish" and b["id"] == b["data"]["wish"]]
    assert births and all(b["data"]["from"] == "person" for b in births)
    told = wishes(read_facts(home))
    assert {b["id"] for b in births} <= set(told)
    assert waits_on(told, {}) == {}



def test_a_retried_ask_finishes_the_one_that_half_happened(ledger):
    home, seat = ledger
    wish, mid = child(home, seat, "mid", "s-aaaa-mid")
    out = mid / "port" / "out"
    out.mkdir(parents=True, exist_ok=True)
    out.chmod(0o500)
    try:
        command(home, mid, "ask", "Which half first?", "--for", wish, "--rec", "the parser", ok=False)
    finally:
        out.chmod(0o700)
    [half] = [w for w in wishes(read_facts(home)).values() if w.sender == "resident"]
    assert mail(mid, half.id) == []
    # The retry mails the ask the failed try wrote, and writes no second ask.
    ask = command(home, mid, "ask", "Which half first?", "--for", wish, "--rec", "the parser")
    assert ask == half.id
    assert len(mail(mid, ask)) == 1
    # Once the mail has left, another retry sends nothing, also after the loom drained the port.
    command(home, mid, "ask", "Which half first?", "--for", wish, "--rec", "the parser")
    [sent] = mail(mid, ask)
    for path in out.iterdir():
        path.unlink()
    inject_letter(home.root, to="thread:work", body=sent.split("---\n", 2)[2])
    command(home, mid, "ask", "Which half first?", "--for", wish, "--rec", "the parser")
    assert mail(mid, ask) == []
    asks = [w for w in wishes(read_facts(home)).values() if w.sender == "resident"]
    assert [w.id for w in asks] == [ask] and len(asks[0].reads) == 1

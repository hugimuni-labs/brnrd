"""Two deaths inside ten minutes stop the respawns."""

import time

import pytest

from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import inject_letter
from brr.loom.runtime.project import owed

from _step import Loom, hand_note, notices, wait_until, write_thread


@pytest.mark.parametrize("settled_by", ["inbox body", "a person, by hand"])
def test_two_immediate_deaths_fuse_the_strand(tmp_path, settled_by):
    """The fuse is told to inbox once, and is gone once a body or a person settles it."""
    root = tmp_path / "home"
    write_thread(root, "td", "thread td", "die-now", wait="5")
    if settled_by == "inbox body":
        write_thread(root, "inbox", (Home(root).thread_dir("inbox") / "README.md").read_text(),
                     "answer-fast", wait="0.1")
    inject_letter(root, to="thread:td", body="begin")
    loom = Loom(root, tick=0.05)
    loom.start()
    try:
        def fused():
            return any(fact.id.startswith("attention:fuse:") for fact in loom.facts())

        wait_until(fused, 5, loom.dump)
        # A third launch would land on a later tick. Give it several.
        time.sleep(0.4)
        facts = loom.facts()
        deaths = [fact for fact in facts if fact.kind == "body.died"]
        dead = {fact.data["strand"] for fact in deaths}
        starts = [f for f in facts if f.kind == "body.started" and f.data["strand"] in dead]
        assert len(starts) == 2
        assert len(deaths) == 2
        assert len(dead) == 1
        fuses = [fact for fact in facts if fact.id.startswith("attention:fuse:")]
        assert [fact.id for fact in fuses] == [f"attention:fuse:{starts[0].data['strand']}"]
        # One notice, to inbox, that names the thread, its handle and the way out.
        told = [f for f in facts if f.kind == "letter" and f.data.get("from") == "loom"]
        assert len(told) == 1 and told[0].data["to"] == "thread:inbox"
        body = told[0].data["body"]
        assert "thread td is fused" in body
        assert "a new letter to thread:td resets the fuse" in body
        assert body.endswith(f"re: {told[0].data['id']}")
        if settled_by == "inbox body":
            wait_until(lambda: any(told[0].data["id"] in f.data.get("ids", ())
                                   for f in loom.facts() if f.kind == "shown"), 5, loom.dump)
        else:
            assert notices(loom.facts()) == told
            hand_note(root, told[0].data["id"])
        wait_until(lambda: not notices(loom.facts()), 5, loom.dump)
        # The notice did not un-fuse the thread it is about.
        assert owed(loom.facts(), "td")
        assert len([f for f in loom.facts() if f.kind == "body.died"]) == 2
    finally:
        loom.halt()
        assert not loom.errors


def test_two_unfinished_exits_fuse_the_strand(tmp_path):
    """Exit 0 with the letter still owed is a failed attempt, not a reason to spin."""
    root = tmp_path / "home"
    write_thread(root, "tq", "thread tq", "quit-now", wait="5")
    inject_letter(root, to="thread:tq", body="begin")
    loom = Loom(root, tick=0.05)
    loom.start()
    try:
        def fused():
            return any(fact.id.startswith("attention:fuse:") for fact in loom.facts())

        wait_until(fused, 5, loom.dump)
        time.sleep(0.4)
        facts = loom.facts()
        starts = [fact for fact in facts if fact.kind == "body.started"]
        exits = [fact for fact in facts if fact.kind == "body.exited"]
        assert len(starts) == 2
        assert len(exits) == 2 and all(fact.data.get("unfinished") for fact in exits)
    finally:
        loom.halt()
        assert not loom.errors


def test_a_new_letter_resets_the_fuse(tmp_path):
    """Writing to the thread again grants a fresh strand; silence leaves it fused."""
    root = tmp_path / "home"
    write_thread(root, "tr", "thread tr", "die-now", wait="5")
    inject_letter(root, to="thread:tr", body="begin")
    loom = Loom(root, tick=0.05)
    loom.start()
    try:
        def fuses():
            return [fact for fact in loom.facts() if fact.id.startswith("attention:fuse:")]

        wait_until(lambda: len(fuses()) == 1, 5, loom.dump)
        loom.halt()
        assert not loom.errors
        loom = Loom(root, tick=0.05)
        loom.start()
        time.sleep(0.4)
        assert len([f for f in loom.facts() if f.kind == "body.started"]) == 2
        # The person fixes what was wrong and writes again.
        (loom.home.thread_dir("tr") / "policy").write_text("answer-fast\n")
        inject_letter(root, to="thread:tr", body="again")
        wait_until(lambda: not owed(loom.facts(), "tr"), 10, loom.dump)
        strands = {f.data["strand"] for f in loom.facts() if f.kind == "body.started"}
        assert len(strands) == 2
        assert len(fuses()) == 1
    finally:
        loom.halt()
        assert not loom.errors

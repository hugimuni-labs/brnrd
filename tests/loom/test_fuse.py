"""Two deaths inside ten minutes stop the respawns."""

import time

from brr.loom.runtime.ledger import inject_letter
from brr.loom.runtime.project import owed

from _step import Loom, wait_until, write_thread


def test_two_immediate_deaths_fuse_the_strand(tmp_path):
    root = tmp_path / "home"
    write_thread(root, "td", "thread td", "die-now", wait="5")
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
        starts = [fact for fact in facts if fact.kind == "body.started"]
        deaths = [fact for fact in facts if fact.kind == "body.died"]
        assert len(starts) == 2
        assert len(deaths) == 2
        assert len({fact.data["strand"] for fact in starts}) == 1
        attentions = [fact for fact in facts if fact.kind == "attention"]
        assert len(attentions) == 1
        assert attentions[0].id == f"attention:fuse:{starts[0].data['strand']}"
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

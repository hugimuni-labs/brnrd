"""Two queues: mine in the person's order (move to top only), theirs ranked by parent, Next build."""
from types import SimpleNamespace

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime import verbs_wishes
from brr.loom.runtime.queues import SHOWN, queues, render
from brr.loom.runtime.wishes import wishes

from test_requests_lifecycle import at, delivered, lease, letter, wish


def top(ident, when, ref, by="strand:s-aaaa-work"):
    return Fact(kind="wish.top", by=by, at=when, data={"wish": ident, "ref": ref})


def pick(ident, when, why):
    return Fact(kind="wish.pick", by="strand:s-aaaa-work", at=when, data={"wish": ident, "why": why})


def base():
    """w-old said first, w-new said later: w-new is on top."""
    return [lease("s-aaaa-work", "work", at(30)), letter("L1", at(20)), letter("L2", at(10)),
            wish("w-old", at(20), says=["L1"]), wish("w-new", at(10), says=["L2"])]


def ids(rows):
    return [w.id for w in rows]


def test_the_last_said_is_on_top_and_a_top_moves_an_older_wish_up():
    assert ids(queues(wishes(base()), {}).mine) == ["w-new", "w-old"]
    facts = [*base(), letter("L3", at(5)), top("w-old", at(5), "L3")]
    q = queues(wishes(facts), {})
    assert ids(q.mine) == ["w-old", "w-new"] and q.next.id == "w-old" and q.why == "top"


def test_a_steer_naming_a_wish_again_does_not_move_it():
    restamp = Fact(kind="wish", by="strand:s-aaaa-work", id="restamp", at=at(5), data={
        "wish": "w-old", "from": "person", "line": "w-old", "thread": "work", "says": [{"letter": "L3"}]})
    facts = [*base(), letter("L3", at(5)), restamp]
    assert ids(queues(wishes(facts), {}).mine) == ["w-new", "w-old"]


def test_a_derived_wish_is_idle_until_the_person_tops_it():
    facts = [*base(), wish("w-derived", at(1))]
    q = queues(wishes(facts), {})
    assert "w-derived" not in ids(q.mine) and ids(q.idle) == ["w-derived"]
    facts += [letter("L4", at(0.5)), top("w-derived", at(0.5), "L4")]
    assert queues(wishes(facts), {}).mine[0].id == "w-derived"


def test_mine_folds_below_the_cut_into_idle():
    facts = [lease("s-aaaa-work", "work", at(99))]
    for n in range(SHOWN + 2):
        facts += [letter(f"L{n}", at(50 - n)), wish(f"w-{n}", at(50 - n), says=[f"L{n}"])]
    q = queues(wishes(facts), {})
    assert len(q.mine) == SHOWN and ids(q.idle) == ["w-1", "w-0"]
    assert "+2 idle" in render(q)


def test_an_ask_ranks_with_its_parent_and_blocks_it_from_next_build():
    facts = [*base(), wish("w-ask-old", at(3), sender="resident", parent="w-old"),
             wish("w-ask-new", at(4), sender="resident", parent="w-new"), delivered("w-old", at(2))]
    # w-old is delivered (theirs); w-new is the only open one in mine, and an ask blocks it.
    q = queues(wishes(facts), {"w-ask-old": "person:arseni", "w-ask-new": "person:arseni"})
    assert ids(q.yours) == ["w-ask-new", "w-ask-old", "w-old"]
    assert q.next is None and "w-new" in q.blocked
    # An ask waiting on a thread is not the person's to answer and blocks nothing.
    q = queues(wishes(facts), {"w-ask-new": "thread:other"})
    assert q.next.id == "w-new" and "w-ask-new" not in ids(q.yours)


def test_theirs_follows_the_order_of_mine():
    facts = [*base(), wish("w-ask-old", at(3), sender="resident", parent="w-old"),
             wish("w-ask-new", at(4), sender="resident", parent="w-new")]
    heads = {"w-ask-old": "person:arseni", "w-ask-new": "person:arseni"}
    assert ids(queues(wishes(facts), heads).yours) == ["w-ask-new", "w-ask-old"]
    facts += [letter("L3", at(1)), top("w-old", at(1), "L3")]
    assert ids(queues(wishes(facts), heads).yours) == ["w-ask-old", "w-ask-new"]


def test_a_pick_wins_next_build_unless_it_is_blocked():
    facts = [*base(), pick("w-old", at(2), "unblocks the handover")]
    q = queues(wishes(facts), {})
    assert q.next.id == "w-old" and q.why == "mine: unblocks the handover"
    assert ids(q.mine) == ["w-new", "w-old"]  # a pick never reorders their list
    facts.append(wish("w-ask", at(1), sender="resident", parent="w-old"))
    q = queues(wishes(facts), {"w-ask": "person:arseni"})
    assert q.next.id == "w-new" and q.why == "top"


def rank(facts, ident, *, top=None, pick=None, by="strand:s-aaaa-work"):
    args = SimpleNamespace(id=ident, top=top, pick=pick)
    return verbs_wishes._rank_fact(args, facts, by)


def test_top_cites_a_person_letter_and_refuses_anything_else():
    facts = [*base(), letter("R1", at(3), by="strand:s-aaaa-work", sender="s-aaaa-work")]
    fact = rank(facts, "w-old", top="L2")
    assert fact.kind == "wish.top" and fact.data == {"wish": "w-old", "ref": "L2"}
    with pytest.raises(ValueError, match="not a person's letter"):
        rank(facts, "w-old", top="R1")
    with pytest.raises(ValueError, match="not a person's letter"):
        rank(facts, "w-old", top="nope")
    with pytest.raises(ValueError, match="no wish"):
        rank(facts, "w-none", top="L2")


def test_pick_needs_a_body_and_a_why_and_an_open_person_wish():
    facts = [*base(), wish("w-ask", at(1), sender="resident", parent="w-old")]
    assert rank(facts, "w-old", pick="  first   things ").data == {"wish": "w-old", "why": "first things"}
    with pytest.raises(ValueError, match="only a body"):
        rank(facts, "w-old", pick="x", by="person:arseni")
    with pytest.raises(ValueError, match="needs its why"):
        rank(facts, "w-old", pick=" ")
    with pytest.raises(ValueError, match="not an open wish to the resident"):
        rank(facts, "w-ask", pick="x")
    with pytest.raises(ValueError, match="one at a time"):
        rank(facts, "w-old", top="L2", pick="x")


def test_render_names_next_build_and_both_queues():
    facts = [*base(), wish("w-ask", at(1), sender="resident", parent="w-old"), delivered("w-new", at(1))]
    out = render(queues(wishes(facts), {"w-ask": "person:arseni"}))
    assert out.startswith("## Queues\nNext build: none")
    assert "- ask w-ask" in out and "- delivered w-new" in out and "1. w-old" in out

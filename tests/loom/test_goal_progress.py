"""A goal's progress on the board comes from the wishes linked to it (wish.goal), never from its prose."""
from datetime import datetime
from types import SimpleNamespace

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime import idle, verbs_wishes
from brr.loom.runtime.board import goal_progress
from brr.loom.runtime.wishes import wishes

from test_requests_lifecycle import NOW, at, delivered, letter, wish

GOAL = {"id": "growth", "stands": "s", "next": "n", "now": "w", "stake": "k"}


def link(ident, when, goal="growth"):
    return Fact(kind="wish.goal", by="strand:s-aaaa-work", at=when, data={"wish": ident, "goal": goal})


def facts():
    return [letter("L1", at(30)), letter("L2", at(30)),
            wish("w-pilot", at(20), says=["L1"]), wish("w-board", at(20), says=["L2"]),
            wish("w-ask", at(10), sender="resident", parent="w-pilot"),
            link("w-pilot", at(19)), link("w-board", at(19))]


def progress(rows, waits, hours=0):
    return goal_progress([GOAL], wishes(rows), waits, now=NOW.timestamp() + hours * 3600)[0]


def test_a_link_is_folded_and_a_later_none_unlinks():
    rows = facts()
    assert wishes(rows)["w-pilot"].goal == "growth"
    assert wishes([*rows, link("w-pilot", at(5), goal="")])["w-pilot"].goal == ""


def test_one_free_wish_under_the_goal_means_it_does_not_wait_on_the_person():
    row = progress(facts(), {"w-ask": "person:arseni"})
    assert row["asks"] == [{"wish": "w-ask", "line": "w-ask", "blocks": "w-pilot", "waits": "person:arseni",
                            "at": row["asks"][0]["at"]}]
    assert row["blocked"] is False and row["progress"] == {"heard": 2}


def test_the_goal_waits_on_the_person_only_when_every_live_wish_has_an_ask():
    rows = [*facts(), wish("w-ask2", at(9), sender="resident", parent="w-board")]
    assert progress(rows, {"w-ask": "person:arseni", "w-ask2": "person:arseni"})["blocked"] is True
    # An ask that waits on another thread is that thread's move, not the person's.
    assert progress(rows, {"w-ask": "person:arseni", "w-ask2": "thread:inbox"})["blocked"] is False


def test_the_latest_delivery_and_a_stall_are_read_from_the_facts():
    rows = [*facts(), delivered("w-board", at(3))]
    row = progress(rows, {})
    assert row["delivery"]["wish"] == "w-board" and row["delivery"]["ref"] == "#1"
    assert row["progress"] == {"heard": 1, "delivered": 1} and row["stalled"] is False
    assert progress(rows, {}, hours=30)["stalled"] is True


def test_an_unlinked_goal_shows_its_prose_and_no_counts():
    row = goal_progress([GOAL], wishes(facts()[:4]), {})[0]
    assert row["progress"] == {} and row["asks"] == [] and row["blocked"] is False
    assert "moved" not in row and "delivery" not in row


def args(**kw):
    base = dict(id="w-pilot", goal="growth", top=None, pick=None, read=None, result=None, accept=None,
                drop=None, answer=None, left=[], clear_wait=False)
    return SimpleNamespace(**{**base, **kw})


def test_goal_verb_names_only_an_active_goal(monkeypatch):
    monkeypatch.setattr(idle, "active_goals", lambda home: [GOAL])
    fact = verbs_wishes._goal_fact(None, args(), facts(), "strand:s-aaaa-work")
    assert fact.kind == "wish.goal" and fact.data == {"wish": "w-pilot", "goal": "growth"}
    assert verbs_wishes._goal_fact(None, args(goal="none"), facts(), "strand:x").data["goal"] == ""
    with pytest.raises(ValueError, match="not an active goal"):
        verbs_wishes._goal_fact(None, args(goal="nope"), facts(), "strand:x")
    with pytest.raises(ValueError, match="goes alone"):
        verbs_wishes._goal_fact(None, args(pick="why"), facts(), "strand:x")

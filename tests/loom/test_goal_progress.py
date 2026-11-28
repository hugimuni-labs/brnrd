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
                drop=None, answer=None, left=[], clear_wait=False, line=None, sender=None,
                thread=None, parent=None, ref=None, wait=None, until=None, say=[],
                force=False, deliver=False)
    return SimpleNamespace(**{**base, **kw})


def test_goal_verb_names_only_an_active_goal(monkeypatch):
    monkeypatch.setattr(idle, "active_goals", lambda home: [GOAL])
    fact = verbs_wishes._goal_fact(None, args(), facts(), "strand:s-aaaa-work")
    assert fact.kind == "wish.goal" and fact.data == {"wish": "w-pilot", "goal": "growth"}
    assert verbs_wishes._goal_fact(None, args(goal="none"), facts(), "strand:x").data["goal"] == ""
    with pytest.raises(ValueError, match="not an active goal"):
        verbs_wishes._goal_fact(None, args(goal="nope"), facts(), "strand:x")
    with pytest.raises(ValueError, match="goes alone"):
        verbs_wishes._goal_fact(None, args(top="L1"), facts(), "strand:x")


@pytest.fixture
def goal_command(tmp_path, monkeypatch):
    from brr.loom.runtime.home import Home
    from brr.loom.runtime.ledger import append, read_facts

    home = Home(tmp_path, install="aaaa")
    append(home, letter("L1", at(30)))
    append(home, wish("w-pilot", at(20), says=["L1"]))
    append(home, wish("w-ask", at(10), sender="resident", parent="w-pilot"))
    monkeypatch.setattr(verbs_wishes, "_context", lambda args: (home, "strand:s-aaaa-work", "work"))
    monkeypatch.setattr(idle, "active_goals", lambda home: [GOAL])
    return lambda: read_facts(home)


def test_goal_and_pick_write_both_facts_in_order(goal_command):
    before = len(goal_command())
    assert verbs_wishes._wish(["w-pilot", "--goal", "growth", "--pick", "Screen  the extract"]) == 0
    assert [(f.kind, f.data) for f in goal_command()[before:]] == [
        ("wish.goal", {"wish": "w-pilot", "goal": "growth"}),
        ("wish.pick", {"wish": "w-pilot", "why": "Screen the extract"}),
    ]


@pytest.mark.parametrize(("ident", "flags", "error"), [
    ("w-pilot", ["--goal", "nope", "--pick", "Screen the extract"], "not an active goal"),
    ("w-pilot", ["--goal", "growth", "--pick", " "], "--pick needs its why"),
    ("w-ask", ["--goal", "growth", "--pick", "Handle the ask"], "not an open wish"),
    ("w-pilot", ["--goal", "growth", "--result", "file:report.md"], "--goal goes alone or with --pick"),
    ("w-pilot", ["--goal", "growth", "--pick", "Screen", "--line", "New line"], "--goal goes alone or with --pick"),
])
def test_refused_goal_command_writes_no_facts(goal_command, capsys, ident, flags, error):
    before = list(goal_command())
    assert verbs_wishes._wish([ident, *flags]) == 1
    assert error in capsys.readouterr().err
    assert list(goal_command()) == before


def test_an_ask_names_what_it_holds_up_and_what_moves_meanwhile():
    from brr.loom.runtime.board import calls_of
    rows = [*facts(), letter("L3", at(30)), wish("w-other", at(8), says=["L3"], thread="elsewhere"),
            link("w-other", at(7))]
    threads = [{"id": "work", "opened_by": "person:arseni", "status": "open"}]
    ask = next(c for c in calls_of(threads, wishes(rows))["work"] if c["wish"] == "w-ask")
    assert ask["blocks"] == "w-pilot"
    # Same goal, any thread, newest first; the held wish itself never shows.
    assert [m["wish"] for m in ask["meanwhile"]] == ["w-other", "w-board"]
    unlinked = [f for f in rows if f.kind != "wish.goal"]
    ask = next(c for c in calls_of(threads, wishes(unlinked))["work"] if c["wish"] == "w-ask")
    # No goal: the same thread only.
    assert [m["wish"] for m in ask["meanwhile"]] == ["w-board"]


def test_board_and_wake_show_the_same_runnable_step_beside_an_open_ask(tmp_path, monkeypatch):
    from brr.loom.runtime import lifecycle, selfrepo

    rows = facts()
    rows.extend(
        Fact(kind="wish.left", by="strand:s-aaaa-work", at=at(5),
             data={"wish": ident, "steps": [step]})
        for ident, step in [("w-pilot", "Wait for verdict"), ("w-board", "Check the build")]
    )
    told = wishes(rows)
    waits = {"w-ask": "person:arseni"}
    (tmp_path / "self" / ".git").mkdir(parents=True)
    home = SimpleNamespace(root=tmp_path)
    monkeypatch.setattr(lifecycle, "main_tip", lambda repo: "main")
    monkeypatch.setattr(selfrepo, "git", lambda *args: SimpleNamespace(stdout=""))
    monkeypatch.setattr(idle, "active_goals", lambda *args: [GOAL])

    def check(expected):
        board = goal_progress([GOAL], told, waits, now=NOW.timestamp())[0]
        wake, = idle.goal_lines(home, now=NOW.timestamp(), told=told, waits=waits)
        assert board["asks"][0]["wish"] == "w-ask"
        assert "open asks: w-ask" in wake
        assert board.get("move") == expected
        if expected:
            assert f"ready {expected['wish']}: {expected['step']}" in wake
        else:
            assert " · ready " not in wake

    check({"wish": "w-board", "step": "Check the build"})
    rows.append(Fact(kind="wish.read", by="strand:s-aaaa-work", at=at(4),
                     data={"wish": "w-board", "by": "resident", "reading": "waiting: CI", "wait": {"on": "ci:repo@sha"}}))
    told = wishes(rows)
    check(None)
    rows.append(Fact(kind="wish.read", by="strand:s-aaaa-work", at=at(3),
                     data={"wish": "w-board", "by": "resident", "reading": "CI passed"}))
    told = wishes(rows)
    check({"wish": "w-board", "step": "Check the build"})

"""The one rule (threads/judged-acts): an act marked final runs only on a letter of the owner's its mark cites."""

import json
import os
import stat
import subprocess

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime.grants import letter_for, names
from brr.loom.runtime.home import Home
from brr.loom.runtime.jack import execute
from brr.loom.runtime.ledger import append, read_facts
from test_guards import STRAND, _call, _repo



def _letter(ident, body, sender="person:arseni"):
    return Fact(kind="letter", by="loom:aaaa", id=ident,
                data={"id": ident, "body": body, "from": sender, "to": "thread:inbox"})


def _wish(ident, thread, says, verdict=None):
    facts = [Fact(kind="wish", by="strand:s-aaaa-inbox", id=ident,
                  at="2026-11-01T00:00:00+00:00", data={"wish": ident, "from": "person", "line": "x",
                                                       "thread": thread, "says": [{"letter": s} for s in says]})]
    if verdict:
        facts.append(Fact(kind="wish.verdict", by="loom:aaaa", at="2026-11-02T00:00:00+00:00",
                          data={"wish": ident, "as": verdict}))
    return facts


OWNER = {"person:arseni"}


@pytest.mark.parametrize("text,target,named", [
    ("go, merge 2610", "#2610", True),
    ("merge #2610.", "#2610", True),
    ("merge 26100", "#2610", False),
    ("merge to main when green", "main", True),
    ("maintain it", "main", False),
    ("see main.py", "main", False),
    ("rm rooms/a", "rooms/a", True),
    ("rm rooms/a/b", "rooms/a", False),
    ("v0.9.10 is out", "v0.9.1", False),
    ("Publish 0.7.0 then 🥳", "v0.7.0", True),
    ("publish v0.7.0", "v0.7.0", True),
    ("publish 0.7.01", "v0.7.0", False),
    ("publish 10.7.0", "v0.7.0", False),
    ("release 0.7", "v0.7.0", False),
])
def test_a_target_is_named_as_a_word(text, target, named):
    assert names(text, target) is named


@pytest.mark.parametrize("facts,cited,granted", [
    ([_letter("cloud:telegram:1:2610", "go, merge 2610")], "2610", True),
    ([_letter("cloud:telegram:1:2610", "go, merge 2610")], "cloud:telegram:1:2610", True),
    ([_letter("cloud:telegram:1:2610", "go")], "his 2610.", True),
    ([_letter("cloud:telegram:1:2610", "go")], "26100", False),
    # A strand's letter is never the owner's word, whatever it says.
    ([_letter("s-aaaa-ta/x1", "merge 2610", sender="s-aaaa-ta")], "s-aaaa-ta/x1", False),
    # Only the owner grants; another person on the install does not, by design.
    ([_letter("cloud:telegram:2:77", "merge 2610", sender="person:sasha")], "77", False),
    ([], "2610", False),
    ([_letter("cloud:telegram:1:2610", "go")], "", False),
])
def test_a_final_act_cites_a_letter_of_the_owners(facts, cited, granted):
    assert (letter_for(facts, cited, OWNER) is not None) is granted


def _room(tmp_path, facts=()):
    _repo(tmp_path / "self", {"people/arseni/agreement.md": "a\n", "README.md": "self\n"})
    room = tmp_path / "rooms" / STRAND
    (room / "port" / "in").mkdir(parents=True)
    (room / "port" / "out").mkdir()
    home = Home(tmp_path, install="aaaa")
    for fact in facts:
        append(home, fact)
    return room, home


@pytest.mark.parametrize("walled", [True, False])
def test_the_hook_refuses_a_final_mark_with_no_letter_and_says_ask(tmp_path, monkeypatch, walled):
    monkeypatch.setenv("BRNRD_THREAD", "ta")
    if walled:
        monkeypatch.setenv("BRNRD_WALL", "1")
    else:
        monkeypatch.delenv("BRNRD_WALL", raising=False)
    room, home = _room(tmp_path)
    said = json.loads(execute("pre", room, _call(
        "Bash", command='BRNRD_SAY="final · v0.9.1 release · 2610" gh release create v0.9.1')))["hookSpecificOutput"]
    assert said["permissionDecision"] == "deny"
    reason = said["permissionDecisionReason"]
    assert "v0.9.1 release cannot be undone, and 2610 names no letter of arseni's" in reason
    assert "brnrd runtime ask" in reason
    assert [(f.data["class"], f.data["target"]) for f in read_facts(home) if f.kind == "guard.refused"] == [
        ("final", "v0.9.1 release")]


def test_his_letter_lets_the_final_act_through_and_leaves_its_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("BRNRD_THREAD", "ta")
    monkeypatch.setenv("BRNRD_WALL", "1")
    room, home = _room(tmp_path, [_letter("cloud:telegram:1:2610", "ship 0.9.1"), *_wish("w-1", "ta", ["cloud:telegram:1:2610"])])
    said = json.loads(execute("pre", room, _call(
        "Bash", command='BRNRD_SAY="final · v0.9.1 release · 2610" gh release create v0.9.1')))["hookSpecificOutput"]
    assert said["permissionDecision"] == "allow" and said["updatedInput"]["dangerouslyDisableSandbox"] is True
    assert "granted: v0.9.1 release (final) by cloud:telegram:1:2610" in said["additionalContext"]
    receipts = [f for f in read_facts(home) if f.kind == "guard.granted"]
    assert [(f.data["class"], f.data["target"], f.data["letter"], f.data["wish"]) for f in receipts] == [
        ("final", "v0.9.1 release", "cloud:telegram:1:2610", "w-1")]


def test_a_change_needs_no_letter(tmp_path, monkeypatch):
    monkeypatch.setenv("BRNRD_THREAD", "ta")
    monkeypatch.setenv("BRNRD_WALL", "1")
    room, home = _room(tmp_path)
    said = json.loads(execute("pre", room, _call(
        "Bash", command='BRNRD_SAY="change · PR 12 title · s-x/y · edit it back" gh pr edit 12 -t x')))["hookSpecificOutput"]
    assert said["permissionDecision"] == "allow"
    assert not [f for f in read_facts(home) if f.kind.startswith("guard.")]


OVER = ("budget: thread ta is over its allocation by 81k (881k of 800k, +10%); this body 300k, its boot 120k. "
        "thread:inbox has the notice and decides a raise; you go on.\n")


@pytest.mark.parametrize("command", ["uv run pytest", "git push", "python -c 1"])
def test_over_the_allocation_the_jack_refuses_nothing(tmp_path, monkeypatch, command):
    """The boot already spent what a refusal would save (his 10224, 10228)."""
    monkeypatch.setenv("BRNRD_THREAD", "ta")
    room, _home = _room(tmp_path)
    (room / "port" / "in" / "boundary.md").write_text(OVER)
    assert "deny" not in execute("pre", room, _call("Bash", command=command))


def test_under_the_allocation_nothing_is_refused_for_spend(tmp_path, monkeypatch):
    monkeypatch.setenv("BRNRD_THREAD", "ta")
    room, _home = _room(tmp_path)
    (room / "port" / "in" / "boundary.md").write_text("status: strand x\n")
    assert execute("pre", room, _call("Bash", command="uv run pytest")) == ""

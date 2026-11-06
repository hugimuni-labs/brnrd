"""Never guards (threads/trust-guards C1): refused unless the owner's letter on the wish grants it."""

import json
import os
import stat
import subprocess

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime.grants import find, names
from brr.loom.runtime.guards import GuardError, parse
from brr.loom.runtime.home import Home
from brr.loom.runtime.jack import execute
from brr.loom.runtime.ledger import append, read_facts
from test_guards import STRAND, _call, _repo

MERGE = r"""
says = "A merge whose checks are not green goes only on the owner's letter."
set-by = "letter:10181"

[[match]]
tool = "Bash"
input = '''\bgh\s+pr\s+merge\s+(?P<target>\d+)'''
refuse = true
grant = "merge-red"
"""


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
])
def test_a_target_is_named_as_a_word(text, target, named):
    assert names(text, target) is named


@pytest.mark.parametrize("facts,cls,target,granted", [
    ([_letter("L1", "go, merge 2610"), *_wish("w-1", "ta", ["L1"])], "merge-red", "#2610", True),
    # The target alone is a common word: "merge to main when green" grants no force-push to main.
    ([_letter("L1", "merge to main when green"), *_wish("w-1", "ta", ["L1"])], "force-push", "main", False),
    # A strand's letter is never the owner's word, whatever it says.
    ([_letter("L1", "merge 2610", sender="s-aaaa-ta"), *_wish("w-1", "ta", ["L1"])], "merge-red", "#2610", False),
    # Only the owner grants; another person on the install does not, by design.
    ([_letter("L1", "merge 2610", sender="person:sasha"), *_wish("w-1", "ta", ["L1"])], "merge-red", "#2610", False),
    ([_letter("L1", "merge 2610"), *_wish("w-1", "other", ["L1"])], "merge-red", "#2610", False),
    ([_letter("L1", "merge 2610"), *_wish("w-1", "ta", ["L1"], verdict="dropped")], "merge-red", "#2610", False),
    ([_letter("L1", "merge 2610")], "merge-red", "#2610", False),
])
def test_a_grant_is_the_owners_letter_on_an_open_wish_of_the_thread(facts, cls, target, granted):
    assert (find(facts, "ta", cls, target, OWNER) is not None) is granted


@pytest.mark.parametrize("extra,error", [
    ('grant = "merge-red"\n', "grant needs refuse = true"),
    ('refuse = true\ngrant = "nope"\n', "grant is one of"),
])
def test_a_grant_key_is_checked_when_the_guard_parses(extra, error):
    text = 'says = "x"\nset-by = "letter:1"\n[[match]]\ninput = "x"\n' + extra
    with pytest.raises(GuardError, match=error):
        parse("g", text)


def test_a_grant_needs_a_target_group():
    with pytest.raises(GuardError, match=r"\(\?P<target>"):
        parse("g", 'says = "x"\nset-by = "letter:1"\n[[match]]\ninput = "merge"\nrefuse = true\ngrant = "merge-red"\n')


def _room(tmp_path, facts=()):
    _repo(tmp_path / "self", {"core/guards/merge.toml": MERGE, "people/arseni/agreement.md": "a\n",
                              "README.md": "self\n"})
    room = tmp_path / "rooms" / STRAND
    (room / "port" / "in").mkdir(parents=True)
    (room / "port" / "out").mkdir()
    home = Home(tmp_path, install="aaaa")
    for fact in facts:
        append(home, fact)
    return room, home


def test_the_jack_refuses_without_a_grant_and_says_the_override(tmp_path, monkeypatch):
    monkeypatch.setenv("BRNRD_THREAD", "ta")
    room, _home = _room(tmp_path)
    said = json.loads(execute("pre", room, _call("Bash", command="gh pr merge 2610 --squash")))["hookSpecificOutput"]
    assert said["permissionDecision"] == "deny"
    reason = said["permissionDecisionReason"]
    assert "refused: merge without green (2610)" in reason
    assert "no letter from arseni on this thread's open wishes names 2610" in reason
    assert "override: a letter from arseni naming 2610 and the act" in reason
    assert "?" not in reason.replace("(?P", "")


def test_the_owners_letter_lets_the_call_through_and_leaves_its_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("BRNRD_THREAD", "ta")
    room, home = _room(tmp_path, [_letter("L1", "go, merge 2610"), *_wish("w-1", "ta", ["L1"])])
    out = execute("pre", room, _call("Bash", command="gh pr merge 2610 --squash"))
    said = json.loads(out)["hookSpecificOutput"]
    assert "permissionDecision" not in said
    assert "granted: merge without green (2610) by L1 on w-1" in said["additionalContext"]
    receipts = [f for f in read_facts(home) if f.kind == "guard.granted"]
    assert [(f.data["class"], f.data["target"], f.data["letter"], f.data["wish"]) for f in receipts] == [
        ("merge-red", "2610", "L1", "w-1")]


def test_a_letter_for_another_pr_grants_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("BRNRD_THREAD", "ta")
    room, home = _room(tmp_path, [_letter("L1", "go, merge 2611"), *_wish("w-1", "ta", ["L1"])])
    said = json.loads(execute("pre", room, _call("Bash", command="gh pr merge 2610")))["hookSpecificOutput"]
    assert said["permissionDecision"] == "deny"
    assert not [f for f in read_facts(home) if f.kind == "guard.granted"]


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


# The gh door: the gate runs the real gh for the checks and the guard verb for the grant.
def _gh_gate(tmp_path, checks: int, granted: int) -> tuple:
    from brr.loom.runtime.loom import _NEVER_GATE
    log = tmp_path / "log"

    def tool(name, body):
        path = tmp_path / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return path

    gh = tool("gh", f'echo "gh $*" >> {log}\n'
                    f'[ "$1 $2" = "pr checks" ] && exit {checks}\n'
                    '[ "$1 $2" = "pr view" ] && echo 77\nexit 0\n')
    brnrd = tool("brnrd", f'echo "brnrd $*" >> {log}\nexit {granted}\n')
    shim = tool("shim", _NEVER_GATE.replace("{gh}", str(gh)).replace("{brnrd}", str(brnrd))
                + f'echo "ran $*" >> {log}\n')
    return shim, log


@pytest.mark.parametrize("argv,checks,granted,ran,asked", [
    (["pr", "merge", "2610", "--squash"], 0, 1, True, None),
    (["pr", "merge", "2610", "--squash"], 1, 1, False, "brnrd runtime guard merge-red #2610"),
    (["pr", "merge", "-R", "o/r", "--subject", "x y", "2610"], 8, 0, True, "brnrd runtime guard merge-red #2610"),
    (["pr", "merge", "https://github.com/o/r/pull/2610"], 1, 1, False, "guard merge-red #2610"),
    (["pr", "merge", "--squash"], 1, 1, False, "guard merge-red #77"),
    (["api", "-X", "PUT", "repos/o/r/pulls/2610/merge"], 1, 1, False, "guard merge-red #2610"),
    (["release", "create", "-t", "Title", "v0.9.1"], 0, 1, False, "guard publish v0.9.1"),
    (["release", "create", "v0.9.1"], 0, 0, True, "guard publish v0.9.1"),
    (["pr", "view", "2610"], 1, 1, True, None),
])
def test_the_gh_door_gates_merge_and_release(tmp_path, argv, checks, granted, ran, asked):
    shim, log = _gh_gate(tmp_path, checks, granted)
    proc = subprocess.run([str(shim), *argv], capture_output=True, text=True,
                          env={**os.environ, "PATH": os.environ["PATH"]})
    lines = log.read_text().splitlines() if log.exists() else []
    assert any(line.startswith("ran ") for line in lines) is ran
    assert (proc.returncode == 0) is ran
    guard = [line for line in lines if line.startswith("brnrd ")]
    if asked is None:
        assert not guard
    else:
        assert guard and asked in guard[0]

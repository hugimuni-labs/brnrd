"""The mark (threads/judged-acts): a call that reaches outside the room says what it is, or does not run.

Through the callers a defect would use: the real pre and post hook (jack.execute),
the send and publish verbs in a body's env, the real gh stand-in, and the claude
body's run script that writes the wall.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from brr.loom.runtime import audit, jack, mark
from brr.loom.runtime.home import Home
from brr.loom.runtime.loom import _env
from brr.loom.runtime.port import render_boundary

from test_doors import _passages, _shim_home, person  # noqa: F401  (person is a fixture)
from test_wishes import command, ledger  # noqa: F401  (ledger is a fixture)


@pytest.mark.parametrize("text, reach, what, for_, back, inferred", [
    ("look", "look", "gh pr checks 12", "l-9", "", ("what", "for")),
    ("look · PR 12 checks", "look", "PR 12 checks", "l-9", "", ("for",)),
    ("change · brnrd#12 title · l-3 · edit it back", "change", "brnrd#12 title", "l-3", "edit it back", ()),
    ("change | branch loom/x | l-3 | delete it", "change", "branch loom/x", "l-3", "delete it", ()),
    ("final · v0.9.0 to PyPI · 11337", "final", "v0.9.0 to PyPI", "11337", "", ()),
    ("final · letter to Ada · l-12 · none", "final", "letter to Ada", "l-12", "", ()),
    ("cannot be undone · the release · l-4", "final", "the release", "l-4", "", ()),
])
def test_a_mark_reads(text, reach, what, for_, back, inferred):
    got, why = mark.parse(text, what="gh pr checks 12", owed=("l-9",))
    assert why == ""
    assert (got.reach, got.what, got.for_, got.back, got.inferred) == (reach, what, for_, back, inferred)


@pytest.mark.parametrize("text, why", [
    (None, "no mark"),
    ("pushing the fix, his letter asked (l-2)", "opens with its reach"),
    ("change", "names the thing"),
    ("change · branch x · l-3", "how it is put back"),
    ("final · v1", "names the letter"),
    ("final · v1 · l-4 · yank it", "it is a change"),
])
def test_what_is_no_mark(text, why):
    got, said = mark.parse(text)
    assert got is None and why in said


@pytest.mark.parametrize("command, only", [
    ("brnrd runtime send --to thread:x 'hi'", True),
    ("cd ../self && BRNRD_THREAD=x brnrd runtime send-self --room . 2>&1 | tail -2", True),
    ("python3 -m brr.loom.runtime wish w-1 --left same", True),
    ("cd ../self; brnrd runtime send --to thread:x \"$(cat note.md)\"", False),
    ("brnrd runtime send --to thread:x hi; rm -rf ~/x", False),
    ("brnrd runtime send --to thread:x hi > out.txt", False),
    ("brnrd runtime send --to thread:x hi | sh", False),
    ("cd /tmp", False),
    ("gh pr view 1", False),
])
def test_the_loom_verbs_alone(command, only):
    assert mark.loom_verbs_only(command) is only


def _room(home: Home, owed=()) -> Path:
    place = home.root / "rooms" / "s-abcd-aaaaaa"
    (place / "port/in").mkdir(parents=True)
    (place / "port/out").mkdir()
    (place / "port/in/boundary.md").write_text(render_boundary(place.name, 3, "judged-acts", [], set(), {}))
    return place


def _bash(command, **more):
    return json.dumps({"tool_name": "Bash", "tool_use_id": "c", "cwd": "/w",
                       "tool_input": {"command": command, **more}})


def _tool(name, **args):
    return json.dumps({"tool_name": name, "tool_use_id": "c", "cwd": "/w", "tool_input": args})


def _out(text):
    return json.loads(text)["hookSpecificOutput"] if text else {}


def _marks(home: Home) -> list:
    base = home.root / "audit" / home.install_id()
    rows = [json.loads(line) for p in sorted(base.glob("*/entries.jsonl")) for line in p.read_bytes().splitlines()]
    return [(r["event"]["phase"], r["event"].get("mark")) for r in rows if r["kind"] == "tool"]


@pytest.fixture
def walled(tmp_path, monkeypatch):
    home = Home(tmp_path)
    place = _room(home)
    monkeypatch.setattr(jack, "_guard_warnings", lambda *a: ("", False))
    monkeypatch.setattr(jack, "_first_word", lambda *a, **kw: ("", False))
    monkeypatch.setenv(mark.WALL, "1")
    monkeypatch.setenv("TMPDIR", str(place / "tmp"))
    (home.root / "loom").mkdir(exist_ok=True)
    writer = audit.Writer(home)
    yield home, place
    writer.close()


def test_a_marked_call_crosses_the_wall_and_the_mark_is_on_the_record(walled):
    home, place = walled
    out = _out(jack.execute("pre", place, _bash("BRNRD_SAY=look gh pr checks 12")))
    # The hook lifts the engine's own sandbox flag; the body never types it.
    assert out["permissionDecision"] == "allow"
    assert out["updatedInput"] == {"command": "BRNRD_SAY=look gh pr checks 12", "dangerouslyDisableSandbox": True}
    out = _out(jack.execute("pre", place, _bash(
        'BRNRD_SAY="change · brnrd#12 title · l-3 · edit it back" gh pr edit 12 --title x')))
    assert out["updatedInput"]["dangerouslyDisableSandbox"] is True
    [(phase, look), (_, change)] = _marks(home)
    assert phase == "pre"
    assert look == {"reach": "look", "what": "BRNRD_SAY=look gh pr checks 12", "for": "", "back": "",
                    "inferred": ["what", "for"], "crossed": True}
    assert change["reach"] == "change" and change["back"] == "edit it back" and change["inferred"] == []
    assert audit.main(["verify", "--home", str(home.root)]) == 0
    counts = audit.marks(home.root / "audit" / home.install_id())
    assert counts == {"calls": 2, "marked": {"look": 1, "change": 1, "final": 0}, "crossed": 2, "inferred": 1}


def test_no_mark_no_crossing(walled):
    home, place = walled
    # A walled call runs as asked: the wall holds it, nothing to say.
    assert jack.execute("pre", place, _bash("git status && pytest -q")) == ""
    # Asking to leave the wall without a mark is refused, and the refusal says how to mark.
    out = _out(jack.execute("pre", place, _bash("curl https://x.example", dangerouslyDisableSandbox=True)))
    assert out["permissionDecision"] == "deny"
    assert "BRNRD_SAY=\"reach · what · for · back\"" in out["permissionDecisionReason"]
    # A sentence that is no mark is refused too: the old door sentence names no reach.
    out = _out(jack.execute("pre", place, _bash('BRNRD_SAY="pushing it, he asked" gh pr merge 3')))
    assert out["permissionDecision"] == "deny" and "opens with its reach" in out["permissionDecisionReason"]
    out = _out(jack.execute("pre", place, _bash('BRNRD_SAY="change · branch x · l-3" git push')))
    assert out["permissionDecision"] == "deny" and "put back" in out["permissionDecisionReason"]
    assert [phase for phase, _ in _marks(home)] == ["pre", "pre", "refused", "pre", "refused", "pre", "refused"]


def test_the_loom_verbs_cross_unmarked_and_judge_themselves(walled):
    home, place = walled
    out = _out(jack.execute("pre", place, _bash("cd ../self && brnrd runtime send-self --room . 2>&1 | tail -2")))
    assert out["permissionDecision"] == "allow" and out["updatedInput"]["dangerouslyDisableSandbox"] is True
    # A verb beside code of its own stays behind the wall.
    assert jack.execute("pre", place, _bash('brnrd runtime send --to thread:x "$(cat f)"')) == ""


def test_file_web_and_mcp_tools(walled, monkeypatch):
    home, place = walled
    assert jack.execute("pre", place, _tool("Write", file_path=str(place / "self/a.md"), content="x")) == ""
    assert jack.execute("pre", place, _tool("Edit", file_path="notes/a.md", old_string="a", new_string="b")) == ""
    assert jack.execute("pre", place, _tool("Write", file_path=str(place / "tmp/s.txt"), content="x")) == ""
    out = _out(jack.execute("pre", place, _tool("Write", file_path=str(Path.home() / ".zshrc"), content="x")))
    assert out["permissionDecision"] == "deny" and "outside the room" in out["permissionDecisionReason"]
    # Reads are never acts, wherever they are.
    assert jack.execute("pre", place, _tool("Read", file_path="/etc/hosts")) == ""
    # A web tool can only look, and marks itself.
    assert jack.execute("pre", place, _tool("WebFetch", url="https://example.com", prompt="p")) == ""
    out = _out(jack.execute("pre", place, _tool("mcp__docs__update", ref={})))
    assert out["permissionDecision"] == "deny" and "brnrd runtime mark" in out["permissionDecisionReason"]
    assert mark.main(["change · the Docs page · l-5 · restore the version", "--room", str(place)]) == 0
    assert jack.execute("pre", place, _tool("mcp__docs__update", ref={})) == ""
    # One shot: the next MCP call needs its own.
    assert _out(jack.execute("pre", place, _tool("mcp__docs__update", ref={})))["permissionDecision"] == "deny"
    marked = [m for _, m in _marks(home) if m]
    assert [(m["reach"], m["what"]) for m in marked] == [("look", "https://example.com"),
                                                         ("change", "the Docs page")]


def test_with_no_wall_a_mark_is_recorded_and_never_required(walled, monkeypatch):
    home, place = walled
    monkeypatch.delenv(mark.WALL)
    assert jack.execute("pre", place, _bash("curl x", dangerouslyDisableSandbox=True)) == ""
    assert jack.execute("pre", place, _bash("BRNRD_SAY=look curl x")) == ""
    assert jack.execute("pre", place, _tool("mcp__docs__update", ref={})) == ""
    assert [m["reach"] for _, m in _marks(home) if m] == ["look"]


def test_a_call_the_wall_stopped_hears_how_to_cross(walled):
    home, place = walled
    hit = json.loads(_bash("touch ~/x"))
    hit["tool_response"] = {"stderr": "touch: /Users/a/x: Operation not permitted"}
    assert mark.HIT in _out(jack.execute("post", place, json.dumps(hit)))["additionalContext"]
    fine = json.loads(_bash("ls"))
    fine["tool_response"] = {"stdout": "a b"}
    assert mark.HIT not in _out(jack.execute("post", place, json.dumps(fine))).get("additionalContext", "")


def test_a_send_to_a_person_refuses_without_a_mark(person, monkeypatch):
    home, seat = person
    writer = audit.Writer(home, doors=True)
    try:
        monkeypatch.setenv(mark.MARKS, "1")
        said = command(home, seat, "send", "--to", "channel:mine/42", "unmarked", ok=False)
        assert "refused at the door" in said
        # The old door sentence is no mark either.
        monkeypatch.setenv(audit.SAY, "telling ada, she asked (l-1)")
        command(home, seat, "send", "--to", "channel:mine/42", "old sentence", ok=False)
        monkeypatch.setenv(audit.SAY, "final · letter to Ada · l-1")
        command(home, seat, "send", "--to", "channel:mine/42", "marked")
    finally:
        writer.close()
    [passage] = _passages(home)
    assert passage["mark"] == {"reach": "final", "what": "letter to Ada", "for": "l-1", "back": "",
                               "inferred": [], "crossed": True}


def test_publish_refuses_before_the_push_without_a_mark(tmp_path, monkeypatch):
    from test_publish import _kill, _note, _open, _tip, _verb
    from brr.loom.runtime.ledger import read_facts
    monkeypatch.delenv(audit.SAY, raising=False)
    root, room, checkout, bare = _open(tmp_path, config='audit = "on"\ndoors = "on"\n')
    home = Home(root)
    try:
        # The switch rides doors = on from the launch env, never from the body.
        assert _env(room)[mark.MARKS] == "1"
        _note(checkout, room, "fix.txt", "fixed\n")
        commit = _tip(checkout, "HEAD")
        proc = _verb(room, "widget")
        assert proc.returncode != 0 and "refused at the door" in proc.stderr
        assert _tip(bare, "refs/heads/loom/work") != commit
        monkeypatch.setenv(audit.SAY, "change · loom/work on widget · l-2 · force it back")
        proc = _verb(room, "widget")
        assert proc.returncode == 0, proc.stderr
        assert _tip(bare, "refs/heads/loom/work") == commit
    finally:
        _kill(read_facts(home))


def test_the_gh_stand_in_refuses_a_forge_act_without_a_mark(tmp_path, monkeypatch):
    real, room, home = _shim_home(tmp_path, monkeypatch)
    env = _env(room)
    env[mark.MARKS] = "1"
    env.pop(audit.SAY, None)
    proc = subprocess.run(["gh", "pr", "comment", "12", "--body", "x"], env=env, capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 1 and "refused at the door" in proc.stderr and proc.stdout == ""
    # A look is no forge door.
    proc = subprocess.run(["gh", "repo", "view", "o/r"], env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0
    env[audit.SAY] = "final · comment on #12 · l-3"
    proc = subprocess.run(["gh", "pr", "comment", "12", "--body", "x"], env=env, capture_output=True,
                          text=True, timeout=60)
    assert (proc.returncode, proc.stdout) == (0, "home-token\n"), proc.stderr


@pytest.mark.parametrize("marks, available", [("1", True), ("1", False), ("", True)])
def test_the_claude_body_writes_the_wall_only_behind_the_switch(tmp_path, monkeypatch, marks, available):
    room = tmp_path / "rooms" / "s-abcd-aaaaaa"
    (room / "port").mkdir(parents=True)
    (room / "port" / "wake.md").write_text("wake\n")
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "claude").write_text(f'#!/bin/sh\necho "${{{mark.WALL}-}}" > {tmp_path}/wall\n')
    (fake / "claude").chmod(0o755)
    sitecustom = tmp_path / "site"
    sitecustom.mkdir()
    # The run script asks the platform; this answers for the case, nothing else is stubbed.
    (sitecustom / "sitecustomize.py").write_text(
        f"from brr.loom.runtime import mark\nmark.wall_available = lambda: {available}\n")
    import brr
    src = Path(brr.__file__).resolve().parent.parent
    env = dict(os.environ, PATH=f"{fake}{os.pathsep}{os.environ['PATH']}", BRNRD_JACK="jack",
               BRNRD_WAIT="60", BRNRD_WAKE=str(room / "port" / "wake.md"),
               PYTHONPATH=os.pathsep.join([str(sitecustom), str(src)]))
    env.pop(mark.WALL, None)
    env[mark.MARKS] = marks
    run = src / "brr" / "loom" / "runtime" / "bodies" / "claude" / "run"
    proc = subprocess.run([sys.executable, str(run), str(room), ""], env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    settings = json.loads((room / ".claude/settings.json").read_text())
    on = marks == "1" and available
    assert ("sandbox" in settings) is on
    assert (tmp_path / "wall").read_text().strip() == ("1" if on else "")
    if on:
        box = settings["sandbox"]
        assert box["enabled"] and box["network"]["strictAllowlist"] and box["network"]["allowedDomains"] == []
        assert str(room) in box["filesystem"]["allowWrite"]

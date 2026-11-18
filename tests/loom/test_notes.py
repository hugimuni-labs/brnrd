"""Notes that open themselves (threads/judged-acts, his 11339): a note with a trigger is shown at the call.

Through a started body's room and the real hook: pre shows the note once per
body, post records what worked or blocked, the stop asks one line when
something blocked.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from brr.loom.runtime import notes
from brr.loom.runtime.home import Home
from brr.loom.runtime.jack import execute
from brr.loom.runtime.ledger import read_facts
from brr.loom.runtime.router import Router
from brr.loom.runtime.selfrepo import land_files

from test_project_repos import _home, _kill, _log, _room, _seat, _started, _tick

SCAR = """# A merge gated on grepped check output merged red
trigger: gh pr checks, gh pr merge, concluded
date: 2026-11-01

Read the exit code of `gh pr checks`, never its text.
"""

PAGE = """# ada
trigger: ada, ada@example.com

## who
"""


def _call(command: str, session: str = "s1", event: str = "PreToolUse", **more) -> str:
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": "/w",
                       "tool_use_id": "t", "session_id": session, "hook_event_name": event, **more})


def _context(out: str) -> str:
    return json.loads(out)["hookSpecificOutput"].get("additionalContext", "") if out else ""


def _body(tmp_path, monkeypatch):
    root = _home(tmp_path)
    land_files(root, {"memory/scars/git/a-merge-gated.md": SCAR, "people/ada/page.md": PAGE}, "notes\n\n")
    _seat(root, "work", [])
    home = Home(root)
    _tick(home, {}, Router(home))
    assert _started(root), _log(root)
    monkeypatch.setenv("BRNRD_THREAD", "work")
    monkeypatch.setenv("BRNRD_STOP_TIMEOUT", "91")
    return home, _room(root)


def test_a_note_opens_at_the_call_that_names_its_trigger_once_per_body(tmp_path, monkeypatch):
    home, room = _body(tmp_path, monkeypatch)
    try:
        said = _context(execute("pre", room, _call("gh pr checks 12 --watch")))
        assert "note (gh pr checks): self/memory/scars/git/a-merge-gated.md: A merge gated on grepped" in said
        # Once per body: the same body does not hear it again.
        assert "a-merge-gated" not in _context(execute("pre", room, _call("gh pr merge 12")))
        # A word, not a substring: "gh pr checksum" names nothing.
        assert "a-merge-gated" not in _context(execute("pre", room, _call("gh pr checksum", session="s2")))
        # The next body hears it again.
        assert "a-merge-gated" in _context(execute("pre", room, _call("gh pr merge 12", session="s3")))
        # A person's page is a note like a scar: her name opens it.
        assert "people/ada" in _context(execute("pre", room, _call("echo hi ada", session="s4")))
        # A product note opens the same way.
        assert "brnrd:limbs/npm.md" in _context(execute("pre", room, _call("npm view brnrd versions")))
    finally:
        _kill(read_facts(home))


def test_the_loom_writes_what_worked_and_what_blocked_and_the_stop_asks_one_line(tmp_path, monkeypatch):
    home, room = _body(tmp_path, monkeypatch)
    try:
        execute("start", room, "{}")
        # An unmarked call is no record: only a marked crossing says what it reached.
        execute("post", room, _call("gh pr view 1", event="PostToolUse"))
        assert notes.hands(room) == {}
        execute("post", room, _call("BRNRD_SAY=look gh pr checks 12", event="PostToolUse"))
        execute("post", room, _call("BRNRD_SAY=look gh pr checks 13", event="PostToolUseFailure",
                                    error="Exit code 4\nHTTP 401: Bad credentials"))
        record = notes.hands(room)["gh"]
        assert record["worked"] and record["blocked"] and record["why"] == "HTTP 401: Bad credentials"
        said = _context(execute("pre", room, _call("gh pr checks 14", session="s9")))
        assert "last worked " in said and "last blocked " in said and "(HTTP 401: Bad credentials)" in said
        first = json.loads(execute("stop", room, "{}"))
        assert first["decision"] == "block"
        assert "something blocked you this wake" in first["reason"] and "- gh: HTTP 401" in first["reason"]
        # Asked once per body.
        assert "something blocked" not in execute("stop", room, "{}")
    finally:
        _kill(read_facts(home))


def test_a_note_with_no_success_behind_it_for_30_days_is_unverified():
    note = notes.Note("memory/limbs/amazon.md", "Limb: Amazon", ("amazon.fr", "osascript"))
    now = time.time()
    old = {"osascript": {"worked": now - 31 * 86400}}
    assert notes.stamp(note, old, now).startswith("unverified")
    fresh = {"osascript": {"worked": now - 86400}}
    assert not notes.stamp(note, fresh, now).startswith("unverified")
    # Nothing ever reached: nothing to say, it is not claimed either way.
    assert notes.stamp(note, {}, now) == ""


def test_the_product_notes_ship_with_their_triggers():
    found = {n.path: n for n in notes._from_product()}
    assert {"brnrd:limbs/github.md", "brnrd:limbs/npm.md"} <= set(found)
    assert "npm" in found["brnrd:limbs/npm.md"].phrases and "gh" in found["brnrd:limbs/github.md"].phrases
    assert Path(notes.PRODUCT, "npm.md").is_file()

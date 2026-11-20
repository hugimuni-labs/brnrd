"""Sensors at the three doors brnrd owns, through the entry points a body uses.

A passage is recorded with its sentence (BRNRD_SAY) or its absence; nothing is
refused or judged; a passage with no sentence tells the person once per window.
"""
import json
import subprocess
import sys
import time

import pytest

from brr.loom.runtime import audit
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import read_facts
from brr.loom.runtime.loom import _env

from test_body_gh import _fake_gh, _room as _gh_room
from test_wishes import command, ledger  # noqa: F401  (ledger is a fixture)


def _passages(home: Home) -> list[dict]:
    base = home.root / "audit" / home.install_id()
    rows = [json.loads(line) for p in sorted(base.glob("*/entries.jsonl")) for line in p.read_bytes().splitlines()]
    return [row["event"] for row in rows if row["kind"] == "door"]


def _notes(home: Home) -> list:
    return [f for f in read_facts(home) if f.kind == "letter" and "door-unsaid" in str(f.data.get("id", ""))]


@pytest.fixture
def person(ledger, monkeypatch):
    home, seat = ledger
    (home.root / "loom/config.toml").write_text('connectors = ["mine=fake"]\n')
    (home.root / "self/people/ada").mkdir(parents=True)
    (home.root / "self/people/ada/channels.toml").write_text('channels = ["mine:42"]\n')
    monkeypatch.delenv(audit.SAY, raising=False)
    return home, seat


def test_send_to_a_person_is_a_passage_with_its_sentence_or_one_note(person, monkeypatch):
    home, seat = person
    # Sensors off: the send goes, nothing is recorded, nobody is told.
    command(home, seat, "send", "--to", "channel:mine/42", "before")
    writer = audit.Writer(home, doors=True)
    try:
        monkeypatch.setenv(audit.SAY, "telling ada the build is green, her letter asked (l-1)")
        command(home, seat, "send", "--to", "channel:mine/42", "said")
        monkeypatch.delenv(audit.SAY)
        command(home, seat, "send", "--to", "channel:mine/42", "unsaid one")
        command(home, seat, "send", "--to", "channel:mine/42", "unsaid two")
    finally:
        writer.close()
    passages = _passages(home)
    assert [(p["door"], p["act"], p["target"]) for p in passages] == [("send", "letter", "channel:mine/42")] * 3
    assert [p["sentence"] for p in passages] == [
        "telling ada the build is green, her letter asked (l-1)", None, None]
    notes = _notes(home)
    # A burst of two unsaid passages is one note, to the person's channel.
    assert len(notes) == 1 and notes[0].data["to"] == "channel:mine/42"
    counts = audit.doors(home.root / "audit" / home.install_id())
    assert counts["send"] == {"seen": 3, "declared": 1}
    assert counts["publish"] == counts["forge"] == {"seen": 0, "declared": 0}
    assert audit.verify(home.root / "audit" / home.install_id())["entries"] == 3


def test_trust_reads_mixed_door_rows_from_the_journal(person, monkeypatch, capsys):
    from brr.loom.runtime import trust
    from brr.loom.runtime.port import render_boundary

    home, seat = person
    (home.root / "loom/install-id").write_text(home.install_id() + "\n")
    (seat / "port/in").mkdir(parents=True, exist_ok=True)
    (seat / "port/in/boundary.md").write_text(render_boundary(seat.name, 7, "work", [], set(), {}))
    monkeypatch.setenv("CODEX_THREAD_ID", "codex-session")
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    writer = audit.Writer(home, doors=True)
    try:
        writer.journal.append({"kind": "door", "event": {
            "id": "old-door", "at_ns": time.time_ns(), "door": "forge",
            "act": "pr merge", "sentence": None,
        }})
        command(home, seat, "send", "--to", "channel:mine/42", "unsaid")
        old, new = _passages(home)
        assert "wake" not in old and "session" not in old
        assert (new["wake"], new["session"]) == (7, "codex-session")
        assert trust.main(["--home", str(home.root)]) == 0
        line = capsys.readouterr().out
        assert "0 granted, 0 refused: ledger, unchained" in line
        assert "2 unsaid passages" in line
        assert "coverage unknown; audit-warning absent" in line
    finally:
        writer.close()
    assert audit.verify(writer.journal.base)["entries"] == 2
    assert trust.main(["--home", str(home.root)]) == 0
    assert "capture off" in capsys.readouterr().out


def test_audit_without_doors_records_no_passage(person):
    home, seat = person
    writer = audit.Writer(home)
    try:
        command(home, seat, "send", "--to", "channel:mine/42", "unsaid")
    finally:
        writer.close()
    assert _passages(home) == [] and _notes(home) == []


def test_the_count_reads_a_period(person, monkeypatch):
    home, seat = person
    writer = audit.Writer(home, doors=True)
    try:
        monkeypatch.setenv(audit.SAY, "why")
        command(home, seat, "send", "--to", "channel:mine/42", "one")
    finally:
        writer.close()
    base = home.root / "audit" / home.install_id()
    at = _passages(home)[0]["at_ns"]
    assert audit.doors(base, since_ns=at + 1)["send"] == {"seen": 0, "declared": 0}
    assert audit.doors(base, until_ns=at + 1)["send"] == {"seen": 1, "declared": 1}


def test_publish_records_the_push_after_it_went(tmp_path, monkeypatch):
    from test_publish import _kill, _note, _open, _tip, _verb
    monkeypatch.delenv(audit.SAY, raising=False)
    root, room, checkout, _bare = _open(tmp_path)
    home = Home(root)
    writer = audit.Writer(home, doors=True)
    try:
        _note(checkout, room, "fix.txt", "fixed\n")
        commit = _tip(checkout, "HEAD")
        monkeypatch.setenv(audit.SAY, "pushing the fix for review, his letter asked (l-2)")
        proc = _verb(room, "widget")
        assert proc.returncode == 0, proc.stderr
    finally:
        writer.close()
        _kill(read_facts(home))
    [passage] = _passages(home)
    assert (passage["door"], passage["act"]) == ("publish", "push")
    assert passage["target"] == f"widget loom/work {commit}"
    assert passage["sentence"] == "pushing the fix for review, his letter asked (l-2)"


def _shim_home(tmp_path, monkeypatch):
    """The real gh shim, with a real ``brnrd`` at entry.shim() running this checkout's runtime."""
    from pathlib import Path
    from brr.loom.runtime import entry
    import brr
    shim = entry.shim()
    shim.parent.mkdir(parents=True, exist_ok=True)
    src = Path(brr.__file__).resolve().parent.parent
    shim.write_text(f'#!/bin/sh\n[ "$1" = runtime ] && shift\n'
                    f'PYTHONPATH={src} exec {sys.executable} -m brr.loom.runtime "$@"\n')
    shim.chmod(0o755)
    real = _fake_gh(tmp_path, monkeypatch)
    room = _gh_room(tmp_path, "home-token")
    (room / "port/in").mkdir(parents=True, exist_ok=True)
    return real, room, Home(room.parent.parent)


@pytest.mark.parametrize("args, act, target", [
    (["pr", "comment", "12", "--body", "x"], "pr comment", "12"),
    (["pr", "close", "12"], "pr close", "12"),
    (["issue", "comment", "7", "-b", "y"], "issue comment", "7"),
])
def test_the_gh_shim_records_forge_acts_and_still_runs_them(tmp_path, monkeypatch, args, act, target):
    real, room, home = _shim_home(tmp_path, monkeypatch)
    writer = audit.Writer(home, doors=True)
    try:
        env = _env(room)
        env.pop(audit.SAY, None)
        env[audit.SAY] = "closing the duplicate, ada said so (l-3)"
        proc = subprocess.run(["gh", *args], env=env, capture_output=True, text=True, timeout=60)
        assert (proc.returncode, proc.stdout) == (0, "home-token\n"), proc.stderr
        proc = subprocess.run(["gh", "repo", "view", "o/r"], env=env, capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0
    finally:
        writer.close()
    [passage] = _passages(home)
    assert (passage["door"], passage["act"], passage["target"]) == ("forge", act, target)
    assert passage["sentence"] == "closing the duplicate, ada said so (l-3)"


def test_the_gh_shim_with_capture_off_runs_no_sensor(tmp_path, monkeypatch):
    _real, room, home = _shim_home(tmp_path, monkeypatch)
    proc = subprocess.run(["gh", "pr", "comment", "1"], env=_env(room), capture_output=True, text=True, timeout=60)
    assert (proc.returncode, proc.stdout) == (0, "home-token\n")
    assert not (home.root / "audit").exists()


def test_a_door_entry_that_is_malformed_is_dropped_and_the_writer_keeps_serving(tmp_path, capsys):
    import socket
    home = Home(tmp_path)
    writer = audit.Writer(home, doors=True)
    try:
        endpoint = json.loads((home.root / "loom/audit-endpoint.json").read_bytes())
        for bad in ({"id": "x", "door": "room-edge", "act": "rm", "at_ns": 1},
                    {"id": "x", "door": "send", "act": "", "at_ns": 1},
                    {"id": "x", "door": "send", "act": "letter", "at_ns": 1, "sentence": 3}):
            with socket.create_connection(("127.0.0.1", endpoint["port"]), timeout=1) as conn:
                conn.sendall(audit.canonical({"token": endpoint["token"], "door": bad}) + b"\n")
                assert conn.recv(3) == b""
        room = tmp_path / "rooms" / "s-abcd-aaaaaa"
        room.mkdir(parents=True)
        audit.passage(room, "forge", "pr merge", "9")
    finally:
        writer.close()
    assert [p["act"] for p in _passages(home)] == ["pr merge"]
    assert writer.error is None
    capsys.readouterr()
    assert audit.main(["doors", "--home", str(home.root)]) == 0
    assert json.loads(capsys.readouterr().out)["forge"] == {"seen": 1, "declared": 0}
    assert audit.main(["verify", "--home", str(home.root)]) == 0

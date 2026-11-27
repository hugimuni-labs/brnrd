"""The resident row: the inbox seat, empty or seated, and the short-window pace."""

import json
import time
from datetime import datetime, timedelta, timezone

from brr.daemon2.facts import Fact
from brr.loom.runtime.config import load_config
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import append, inject_letter, read_facts
from brr.loom.runtime.port import render_boundary
from brr.loom.runtime import snapshot, usage
from _step import write_thread

SEAT = "s-aaaa-seat"
WISH = "w-residentrow1"


def _at(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _home(tmp_path):
    root = tmp_path / "home"
    write_thread(root, "inbox", "The inbox.\n", "quit-now")
    return Home(root, install="aaaa")


def _lease(home, strand=SEAT, shell="claude", core="fable"):
    append(home, Fact(kind="lease", by="loom:aaaa", id=f"lease:inbox:{strand}", at=_at(-120), data={
        "thread": "inbox", "strand": strand, "gen": 1, "shell": shell, "core": core}))


def _doc(home, monkeypatch, alive=False):
    monkeypatch.setattr("brr.loom.runtime.loom.body_alive", lambda room: alive)
    return snapshot.assemble(home, read_facts(home), load_config(home.root))


def test_a_seated_inbox_names_the_plan_and_the_letter(tmp_path, monkeypatch):
    home = _home(tmp_path)
    _lease(home)
    letter = inject_letter(home.root, to="thread:inbox", body="Build the row")
    append(home, Fact(kind="letter", by=f"strand:{SEAT}", id="seat/reply", at=_at(-90), data={
        "id": "seat/reply", "from": f"strand:{SEAT}", "to": "channel:test", "re": letter,
        "gen": 1, "body": "reading"}))
    append(home, Fact(kind="shown", by=f"strand:{SEAT}", id="shown:seat", at=_at(-30), data={
        "strand": SEAT, "gen": 1, "ids": [letter]}))
    append(home, Fact(kind="wish", by=f"strand:{SEAT}", id=WISH, data={
        "wish": WISH, "from": "person", "line": "Build the row", "thread": "inbox",
        "says": [{"letter": letter}]}))
    append(home, Fact(kind="wish.left", by=f"strand:{SEAT}", id="wish.left:seat", data={
        "wish": WISH, "steps": ["name the keys in the schema"], "gen": 1}))
    doc = _doc(home, monkeypatch, alive=True)
    seat = doc["presence"]
    assert seat["state"] in {"working", "holding"}
    assert seat["body"] == "claude/fable"
    assert seat["now"] == "Build the row"
    assert seat["doing"] == "plan"
    assert seat["step"] == "name the keys in the schema"
    assert seat["letters"] == [{"id": letter, "filed": "wish", "wish": WISH, "state": "heard"}]
    assert "your next message" not in seat.get("wait", "")


def _park(home):
    append(home, Fact(kind="wish.read", by=f"strand:{SEAT}", id="wish.read:park", at=_at(-20), data={
        "wish": WISH, "by": "resident",
        "reading": "waiting: the inbox review of the resident row",
        "wait": {"on": "pr:hugimuni-labs/brnrd#2866"}}))


def _wish(home, letter):
    append(home, Fact(kind="wish", by=f"strand:{SEAT}", id=WISH, data={
        "wish": WISH, "from": "person", "line": "Build the row", "thread": "inbox",
        "says": [{"letter": letter}]}))


def test_a_working_seat_with_a_thread_wait_says_nothing_about_it(tmp_path, monkeypatch):
    home = _home(tmp_path)
    _lease(home)
    letter = inject_letter(home.root, to="thread:inbox", body="Build the row")
    append(home, Fact(kind="shown", by=f"strand:{SEAT}", id="shown:seat", at=_at(-30), data={
        "strand": SEAT, "gen": 1, "ids": [letter]}))
    _wish(home, letter)
    _park(home)
    seat = _doc(home, monkeypatch, alive=True)["presence"]
    assert seat["state"] == "working"
    assert "wait" not in seat


def test_a_holding_seat_with_a_thread_wait_names_it(tmp_path, monkeypatch):
    home = _home(tmp_path)
    _lease(home)
    letter = inject_letter(home.root, to="thread:inbox", body="Build the row")
    append(home, Fact(kind="letter", by=f"strand:{SEAT}", id="seat/reply", at=_at(-90), data={
        "id": "seat/reply", "from": f"strand:{SEAT}", "to": "channel:test", "re": letter,
        "gen": 1, "body": "reading"}))
    append(home, Fact(kind="shown", by=f"strand:{SEAT}", id="shown:seat", at=_at(-30), data={
        "strand": SEAT, "gen": 1, "ids": [letter]}))
    _wish(home, letter)
    _park(home)
    seat = _doc(home, monkeypatch, alive=True)["presence"]
    assert seat["state"] == "holding"
    assert seat["wait"] == "waiting on the inbox review of the resident row"
    assert "your next message" not in seat["wait"]


def test_an_empty_seat_with_nothing_pending_says_the_next_message(tmp_path, monkeypatch):
    home = _home(tmp_path)
    _lease(home, shell="codex", core="")
    ended = _at(-300)
    append(home, Fact(kind="attempt.ended", by="loom:aaaa", id=f"attempt.ended:{SEAT}:1", at=ended, data={
        "thread": "inbox", "strand": SEAT, "gen": 1, "cause": "reincarnated"}))
    seat = _doc(home, monkeypatch)["presence"]
    assert seat["state"] == "empty"
    assert seat["last_body"] == "codex"
    assert seat["since"] == ended
    assert seat["wait"] == "starts on your next message"


def test_an_empty_seat_with_a_park_names_the_park(tmp_path, monkeypatch):
    home = _home(tmp_path)
    _lease(home, shell="codex", core="")
    until = time.time() + 3600
    append(home, Fact(kind="body.parked", by="loom:aaaa", id=f"body.parked:{SEAT}:1", at=_at(-60), data={
        "strand": SEAT, "gen": 1, "shell": "codex", "core": "echo",
        "window": "session", "left_pct": 4, "until": until}))
    seat = _doc(home, monkeypatch)["presence"]
    assert seat["state"] == "empty"
    assert "park" in seat["wait"]
    assert "session" in seat["wait"]
    assert "your next message" not in seat["wait"]


def test_six_shown_letters_keep_five_since_the_reply(tmp_path, monkeypatch):
    home = _home(tmp_path)
    _lease(home)
    early = inject_letter(home.root, to="thread:inbox", body="before the reply")
    append(home, Fact(kind="letter", by=f"strand:{SEAT}", id="seat/reply", at=_at(-100), data={
        "id": "seat/reply", "from": f"strand:{SEAT}", "to": "channel:test", "re": early,
        "gen": 1, "body": "replied"}))
    append(home, Fact(kind="shown", by=f"strand:{SEAT}", id="shown:early", at=_at(-110), data={
        "strand": SEAT, "gen": 1, "ids": [early]}))
    later = []
    for n in range(6):
        ident = inject_letter(home.root, to="thread:inbox", body=f"after {n}")
        later.append(ident)
        append(home, Fact(kind="shown", by=f"strand:{SEAT}", id=f"shown:{n}", at=_at(-50 + n), data={
            "strand": SEAT, "gen": 1, "ids": [ident]}))
    # A reply handles the early letter, so the live body is holding rather than working.
    seat = _doc(home, monkeypatch, alive=True)["presence"]
    assert early not in {row["id"] for row in seat["letters"]}
    assert len(seat["letters"]) == 5
    assert seat["letters_more"] == 1
    assert {row["filed"] for row in seat["letters"]} == {"open"}


def test_session_pace_says_when_the_short_window_runs_dry():
    now = 1_700_000_000.0
    payload = {
        "at": now,
        "windows": [
            {"name": "session", "left_pct": 33, "resets_at": "2023-11-15T08:13:20Z"},
            {"name": "week", "left_pct": 96, "resets_at": "2023-11-15T12:00:00Z"},
        ],
        "samples": [{"at": now - 3600, "windows": [{"name": "session", "left_pct": 45}]}],
    }
    # 1_700_000_000 is 2023-11-14 22:13:20Z. 12 points in one hour, 33 left, dry in 2.75h.
    # The reset literal is ten hours later, so the dry time is before it.
    pace = usage.session_pace(payload, now)
    assert usage.session_pace_line(pace) == "session pace: dry in about 3h, before the reset"
    assert usage.session_pace_line(usage.session_pace(
        {"at": now, "windows": [{"name": "session", "left_pct": 33}]}, now)) == (
        "session pace: one reading, burn not known yet")
    flat = dict(payload, samples=[{"at": now - 3600, "windows": [{"name": "session", "left_pct": 33}]}])
    assert usage.session_pace_line(usage.session_pace(flat, now)) == "session pace: not falling"
    assert usage.session_pace({"at": now, "windows": [{"name": "week", "left_pct": 90}]}, now) is None


def test_the_fuel_row_carries_the_reading_time_and_the_pace(tmp_path, monkeypatch):
    home = _home(tmp_path)
    _lease(home)
    now = time.time()
    path = home.root / "loom/usage"
    path.mkdir(parents=True)
    (path / "claude.json").write_text(json.dumps({
        "at": now,
        "windows": [
            {"name": "session", "left_pct": 33, "resets_at": "2099-01-01T00:00:00Z"},
            {"name": "week", "left_pct": 96, "resets_at": "2099-01-02T00:00:00Z"},
        ],
        "samples": [{"at": now - 3600, "windows": [{"name": "session", "left_pct": 45}]}],
    }))
    (path / "grok.json").write_text(json.dumps({
        "at": now, "windows": [{"name": "week", "left_pct": 50, "resets_at": "2099-01-02T00:00:00Z"}]}))
    fuel = {row["shell"]: row for row in _doc(home, monkeypatch, alive=True)["fuel"]}
    assert fuel["claude"]["pace"] == "session pace: dry in about 3h, before the reset"
    assert fuel["claude"]["read_at"]
    assert "pace" not in fuel["grok"]
    assert fuel["grok"]["read_at"]


def test_the_boundary_gets_the_pace_as_its_own_line(tmp_path):
    home = _home(tmp_path)
    now = time.time()
    path = home.root / "loom/usage"
    path.mkdir(parents=True)
    (path / "claude.json").write_text(json.dumps({
        "at": now,
        "windows": [{"name": "session", "left_pct": 33, "resets_at": "2099-01-01T00:00:00Z"}],
        "samples": [{"at": now - 3600, "windows": [{"name": "session", "left_pct": 45}]}],
    }))
    line = usage.pace_line_for(home, "claude")
    assert line == "session pace: dry in about 3h, before the reset"
    text = render_boundary(SEAT, 1, "inbox", [], set(), {}, pace=line)
    assert line in text.splitlines()
    status = next(row for row in text.splitlines() if row.startswith("status:"))
    assert line not in status


def test_a_changed_pace_is_one_new_line_and_not_gone():
    from brr.loom.runtime.jack import _news
    first = "session pace: dry in about 3h, before the reset"
    second = "session pace: dry in about 1h, before the reset"
    _, said, _ = _news(render_boundary(SEAT, 1, "inbox", [], set(), {}, pace=first), {"said": []}, False)
    news, _, _ = _news(render_boundary(SEAT, 1, "inbox", [], set(), {}, pace=second), {"said": said}, False)
    assert second in news.splitlines()
    assert first not in news
    assert not any(row.startswith("gone:") for row in news.splitlines())

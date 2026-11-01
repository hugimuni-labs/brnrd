"""Shedding is the body's call: the loom states facts about the README and never says a shed is due."""

import json
import time
from datetime import datetime, timezone

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime import jack, shed
from brr.loom.runtime.jack import _state, execute
from brr.loom.runtime.port import render_boundary

STRAND = "s-ab12-aaaaaa"


@pytest.fixture
def room(tmp_path, monkeypatch):
    room = tmp_path / "rooms" / STRAND
    (room / "port" / "in").mkdir(parents=True)
    (room / "port" / "out").mkdir()
    (room / "port" / "in" / "boundary.md").write_text(render_boundary(STRAND, 1, "work", [], set(), {}))
    readme = room / "self" / "threads" / "work" / "README.md"
    readme.parent.mkdir(parents=True)
    (room / "self" / "core" / "loom").mkdir(parents=True)
    (room / "self" / "core" / "loom" / "wake").write_text("")
    readme.write_text("# Work\n\n## Now\n- nothing in flight\n")
    monkeypatch.setenv("BRNRD_THREAD", "work")
    monkeypatch.delenv("BRNRD_BODY_SHELL", raising=False)
    # A stop with nothing to ask holds in _poll; these tests read only the ask.
    monkeypatch.setattr(jack, "_poll", lambda room, stdin_text="": "")
    return room


def _transcript(tmp_path, *contexts) -> str:
    path = tmp_path / "t.jsonl"
    with path.open("w") as out:
        for tokens in contexts:
            out.write(json.dumps({"message": {"usage": {"input_tokens": 1, "cache_read_input_tokens": tokens}}}) + "\n")
    return json.dumps({"transcript_path": str(path)})


def _fact(room, kind, **data):
    path = room.parent.parent / "ledger" / "facts" / "ab12" / f"{STRAND}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as out:
        out.write(json.dumps({"kind": kind, "by": f"strand:{STRAND}", "id": f"{STRAND}/x",
                              "at": datetime.now(timezone.utc).isoformat(), "data": data}) + "\n")


def _turn(room, stdin):
    """One woken turn: a call, then the stop."""
    execute("post", room, json.dumps({"tool_name": "Bash"}))
    return execute("stop", room, stdin)


def test_a_turn_records_the_context_and_no_tally(room, tmp_path):
    stdin = _transcript(tmp_path, 40_000, 50_000)
    execute("start", room, "")
    _fact(room, "note", re="a/b", why="ack")
    _fact(room, "wish.read", wish="w-aaaaaaaaaaaa", reading="waiting")
    _turn(room, stdin)
    time.sleep(0.01)
    execute("post", room, json.dumps({"tool_name": "Bash"}))
    _fact(room, "letter", to="thread:inbox", body="hi")
    execute("stop", room, stdin)
    # The letter is left behind for the README only as routing; the context is plain cost.
    assert _state(room)["context"] == 50_001 and "turns" not in _state(room)


def test_a_quiet_heavy_stop_never_says_a_shed_is_due(room, tmp_path):
    # 5557: the value of a context can't be measured from outside it.
    stdin = _transcript(tmp_path, 40_000, 700_000)
    execute("start", room, "")
    for _ in range(4):
        assert _turn(room, stdin) == ""
    assert "shed_due_at" not in _state(room)

def test_readme_facts_name_size_and_closed_citations_and_ask_nothing(room):
    readme = room / "self" / "threads" / "work" / "README.md"
    readme.write_text("# Work\n\n## Now\n- PR #7 in review\n- #5 merged, w-aaaaaaaaaaaa next\n")
    merged = [Fact(kind=k, by=f"strand:{STRAND}", id=f"p{k}", data={"thread": "work", "repo": "brnrd", "number": 5,
                                                                      "url": "u", "head": "h", "merge_commit": "c"})
              for k in ("pr.opened", "pr.merged")]
    said = shed.readme_facts(room, "work", merged)
    assert said == f"README: {len(readme.read_bytes())} B · cites 1 closed: #5 merged"
    assert shed.readme_facts(room, "gone", merged) == ""


def test_a_cited_closure_names_its_readme_place_until_the_readme_moves(room):
    readme = room / "self" / "threads" / "work" / "README.md"
    readme.write_text("# Work\n\n## Boot and shed\n- PR #7 in review\n")
    old = time.time() - 60
    import os
    os.utime(readme, (old, old))
    began = datetime.fromtimestamp(old - 60, timezone.utc).isoformat()
    merged = Fact(kind="pr.merged", by="loom:ab12", id="m7", at=datetime.now(timezone.utc).isoformat(),
                  data={"number": 7, "repo": "brnrd", "thread": "work", "merge_commit": "c"})
    assert shed.cited_line(room, "work", [merged], began) == 'cited: #7 merged · README "Boot and shed" (l.4)'
    readme.write_text("# Work\n\n## Boot and shed\n- #7 merged; next is C\n")
    assert shed.cited_line(room, "work", [merged], began) == ""
    assert shed.cited_line(room, "work", [merged], datetime.now(timezone.utc).isoformat()) == ""


def test_a_long_park_states_the_stakes_and_a_short_one_says_nothing():
    now = datetime(2026, 11, 1, 1, 0, tzinfo=timezone.utc)
    assert shed.long_wait("letter:s-ab12-aaaaaa/x7c53", now)
    assert shed.long_wait("at:2026-11-01T03:00:00+00:00", now)
    assert not shed.long_wait("at:2026-11-01T01:20:00+00:00", now)
    assert not shed.long_wait("ci:o/r@abc", now) and not shed.long_wait("file:/x", now)
    line = shed.rest_line("letter:a/b", 134_000)
    assert line.startswith("resting until letter:a/b. Context 134k. What isn't in the README is lost on any end")
    assert line.endswith("What here would you not carry into the next hour?") and "due" not in line

def test_a_broken_tally_never_ends_the_stop(room, tmp_path, monkeypatch):
    def broken(*args):
        raise OSError("no git")
    monkeypatch.setattr(shed, "heads", broken)
    execute("start", room, "")
    assert _turn(room, _transcript(tmp_path, 40_000)) == ""
    assert "no git" in (room / "port" / "shed-errors.log").read_text()
    assert not (room / "port" / "jack-errors.log").exists()



def test_a_resting_stop_asks_once_for_the_readme_acts_left_behind(room, tmp_path):
    stdin = _transcript(tmp_path, 40_000, 50_000)
    readme = room / "self" / "threads" / "work" / "README.md"
    execute("start", room, "")
    # Letters alone are routing, not state: nothing to ask.
    _fact(room, "letter", to="thread:inbox", body="routed")
    assert _turn(room, stdin) == ""
    time.sleep(0.01)
    execute("post", room, json.dumps({"tool_name": "Bash"}))
    _fact(room, "wish.left", wish="w-aaaaaaaaaaaa", steps=["C"])
    _fact(room, "pr.opened", number=7)
    asked = json.loads(execute("stop", room, stdin))
    assert asked["decision"] == "block"
    assert "since threads/work/README.md last changed this body did wish.left, pr.opened." in asked["reason"]
    assert "never a log line" in asked["reason"]
    # Asked once: stopping again rests.
    assert execute("stop", room, stdin) == ""
    # Acts after a rewrite of the README are reflected in it.
    time.sleep(0.01)
    execute("post", room, json.dumps({"tool_name": "Bash"}))
    _fact(room, "wish.left", wish="w-aaaaaaaaaaaa", steps=["C", "D"])
    readme.write_text("# Work\n\n## Now\n- Next build: C, PR #7 open\n")
    assert execute("stop", room, stdin) == ""
    # An act after the rewrite, in the same turn, is still left behind.
    time.sleep(0.01)
    execute("post", room, json.dumps({"tool_name": "Bash"}))
    readme.write_text("# Work\n\n## Now\n- Next build: D\n")
    time.sleep(0.01)
    _fact(room, "pr.opened", number=8)
    asked = json.loads(execute("stop", room, stdin))
    assert "this body did pr.opened." in asked["reason"]

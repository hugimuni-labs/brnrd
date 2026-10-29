"""Shedding: a mostly idle scroll makes the stop ask, and shed gates the README."""

import json
import time
from datetime import datetime, timezone

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime import jack, shed
from brr.loom.runtime.jack import _set_state, _state, execute
from brr.loom.runtime.port import PortError, render_boundary

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


def test_the_bar_is_noise_share_and_growth_scaled_by_pace():
    quiet = {"boot": 40_000, "turns": [[True, 60_000], [False, 90_000], [False, 120_000]]}
    assert shed.bars(quiet, 60_000, 1.0)[:2] == (pytest.approx(2 / 3), 80_000)
    assert shed.due(quiet, 60_000, 1.0)
    # Slack raises the bar, at most to double; 80k is under 120k.
    assert not shed.due(quiet, 60_000, 0.2)
    busy = {"boot": 40_000, "turns": [[True, 60_000], [True, 90_000], [False, 300_000]]}
    assert not shed.due(busy, 60_000, 3.0)
    assert not shed.due({"boot": 40_000, "turns": [[False, 300_000]] * 2}, 60_000, 1.0)
    # Past the backstop, any share.
    assert shed.due({"boot": 40_000, "turns": [[True, 700_000]]}, 60_000, 1.0)
    assert shed.news(quiet, 60_000, 0.9).startswith("shed: q 0.7 · x 80k/66k")
    assert shed.news(quiet, 60_000, 0.2) == ""


def test_a_turn_acted_only_when_it_wrote_beyond_its_settlement(room, tmp_path):
    stdin = _transcript(tmp_path, 40_000, 50_000)
    execute("start", room, "")
    _fact(room, "note", re="a/b", why="ack")
    _fact(room, "wish.read", wish="w-aaaaaaaaaaaa", reading="waiting")
    _turn(room, stdin)
    time.sleep(0.01)
    execute("post", room, json.dumps({"tool_name": "Bash"}))
    _fact(room, "letter", to="thread:inbox", body="hi")
    execute("stop", room, stdin)
    assert [acted for acted, _ in _state(room)["turns"]] == [False, True]
    assert _state(room)["boot"] == 40_001 and _state(room)["turns"][-1][1] == 50_001


def test_a_quiet_stop_asks_once_and_a_held_thread_is_never_asked(room, tmp_path, monkeypatch):
    stdin = _transcript(tmp_path, 40_000, 200_000)
    execute("start", room, "")
    for _ in range(3):
        out = _turn(room, stdin)
    asked = json.loads(out)
    assert asked["decision"] == "block" and "shed due: q 1.0 · x 160k/60k" in asked["reason"]
    assert "threads/work/README.md" in asked["reason"] and _state(room)["shed_due_at"]
    # Stopping again with no call is let through, as for every stop list.
    assert execute("stop", room, stdin) == ""
    (room.parent.parent / "loom").mkdir(exist_ok=True)
    (room.parent.parent / "loom" / "config.toml").write_text('shed_hold = ["work"]\n')
    assert _turn(room, stdin) == ""


def test_decline_once_then_the_ask_cannot_be_declined(room, tmp_path):
    with pytest.raises(PortError, match="nothing to decline"):
        shed.decline(room, "mid-decision")
    stdin = _transcript(tmp_path, 40_000, 200_000)
    execute("start", room, "")
    for _ in range(3):
        _turn(room, stdin)
    shed.decline(room, "two turns from a PR")
    assert "shed due" not in _turn(room, stdin)
    _turn(room, stdin)
    again = json.loads(_turn(room, stdin))["reason"]
    assert "\n\nshed due again: " in again and "(two turns from a PR)" in again and "--decline" not in again
    with pytest.raises(PortError, match="declined once already"):
        shed.decline(room, "still busy")


def test_shed_refuses_a_readme_its_successor_cannot_boot_into(room):
    readme = room / "self" / "threads" / "work" / "README.md"
    open_pr = Fact(kind="pr.opened", by=f"strand:{STRAND}", id="p1",
                   data={"thread": "work", "repo": "brnrd", "number": 7, "url": "https://x/pull/7", "head": "h"})
    merged = [Fact(kind=k, by=f"strand:{STRAND}", id=f"p{k}", data={"thread": "work", "repo": "brnrd", "number": 5,
                                                                      "url": "u", "head": "h", "merge_commit": "c"})
              for k in ("pr.opened", "pr.merged")]
    state = {"shed_due_at": time.time(), "shed_readme": shed.readme_hash(room, "work")}
    found = shed.refusals(room, "work", [open_pr, *merged], state)
    assert any("unchanged since the shed came due" in x for x in found)
    assert any("open PR https://x/pull/7" in x for x in found)
    readme.write_text("# Work\n\n## Plan\n| step | state |\n|---|---|\n| B | merged #5 |\n| C | next, w-aaaaaaaaaaaa |\n")
    found = shed.refusals(room, "work", [open_pr, *merged], state)
    assert any("no `## Now` block" in x for x in found)
    assert not any("unchanged" in x for x in found)
    readme.write_text("# Work\n\n## Now\n- PR #7 in review\n\n## Plan\n| step | state |\n|---|---|\n"
                      "| B | merged #5 |\n| C | next, w-aaaaaaaaaaaa |\n")
    found = shed.refusals(room, "work", [open_pr, *merged], state)
    assert found == ["dead row (every id it cites is closed): prune it or move it to a topic: | B | merged #5 |"]


def test_a_broken_tally_never_ends_the_stop(room, tmp_path, monkeypatch):
    def broken(*args):
        raise OSError("no git")
    monkeypatch.setattr(shed, "heads", broken)
    monkeypatch.setattr(shed, "ask", broken)
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

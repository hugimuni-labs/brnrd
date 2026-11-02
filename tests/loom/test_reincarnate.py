"""Shedding is the body's call: the loom states facts about the README and never says a reincarnation is due."""

import json
import time
from datetime import datetime, timezone

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime import jack, reincarnate
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


def test_a_quiet_heavy_stop_asks_once_and_never_says_a_reincarnation_is_due(room, tmp_path):
    # 5557: the value of a context can't be measured from outside it; 10048: past
    # the mark the number decides when to ask, once, never what to do.
    stdin = _transcript(tmp_path, 40_000, 700_000)
    execute("start", room, "")
    reason = json.loads(_turn(room, stdin))["reason"]
    assert "would die" in reason and "due" not in reason and "reincarnate?" not in reason
    for _ in range(3):
        assert _turn(room, stdin) == ""
    assert "shed_due_at" not in _state(room)

def test_readme_facts_name_size_and_closed_citations_and_ask_nothing(room):
    readme = room / "self" / "threads" / "work" / "README.md"
    readme.write_text("# Work\n\n## Now\n- PR #7 in review\n- #5 merged, w-aaaaaaaaaaaa next\n")
    merged = [Fact(kind=k, by=f"strand:{STRAND}", id=f"p{k}", data={"thread": "work", "repo": "brnrd", "number": 5,
                                                                      "url": "u", "head": "h", "merge_commit": "c"})
              for k in ("pr.opened", "pr.merged")]
    said = reincarnate.readme_facts(room, "work", merged)
    assert said == f"README: {len(readme.read_bytes())} B · cites 1 closed: #5 merged"
    assert reincarnate.readme_facts(room, "gone", merged) == ""


def test_a_cited_closure_names_its_readme_place_until_the_readme_moves(room):
    readme = room / "self" / "threads" / "work" / "README.md"
    readme.write_text("# Work\n\n## Boot and reincarnate\n- PR #7 in review\n")
    old = time.time() - 60
    import os
    os.utime(readme, (old, old))
    began = datetime.fromtimestamp(old - 60, timezone.utc).isoformat()
    merged = Fact(kind="pr.merged", by="loom:ab12", id="m7", at=datetime.now(timezone.utc).isoformat(),
                  data={"number": 7, "repo": "brnrd", "thread": "work", "merge_commit": "c"})
    assert reincarnate.cited_line(room, "work", [merged], began) == 'cited: #7 merged · README "Boot and reincarnate" (l.4)'
    readme.write_text("# Work\n\n## Boot and reincarnate\n- #7 merged; next is C\n")
    assert reincarnate.cited_line(room, "work", [merged], began) == ""
    assert reincarnate.cited_line(room, "work", [merged], datetime.now(timezone.utc).isoformat()) == ""


def test_a_long_park_states_the_stakes_and_a_short_one_says_nothing():
    now = datetime(2026, 11, 1, 1, 0, tzinfo=timezone.utc)
    assert reincarnate.long_wait("letter:s-ab12-aaaaaa/x7c53", now)
    assert reincarnate.long_wait("at:2026-11-01T03:00:00+00:00", now)
    assert not reincarnate.long_wait("at:2026-11-01T01:20:00+00:00", now)
    assert not reincarnate.long_wait("ci:o/r@abc", now) and not reincarnate.long_wait("file:/x", now)
    line = reincarnate.rest_line("letter:a/b", 134_000)
    assert line.startswith("resting until letter:a/b. Context 134k. What isn't in the README is lost on any end")
    assert line.endswith("What does the next hour need that isn't written yet?") and "due" not in line

def test_a_broken_tally_never_ends_the_stop(room, tmp_path, monkeypatch):
    def broken(*args):
        raise OSError("no git")
    monkeypatch.setattr(reincarnate, "heads", broken)
    execute("start", room, "")
    assert _turn(room, _transcript(tmp_path, 40_000)) == ""
    assert "no git" in (room / "port" / "reincarnate-errors.log").read_text()
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


# threads/reincarnate (10048): past the mark a park carries a census and asks what would die.

def _scroll(tmp_path, context, tool="x" * 600, said="y" * 300, own="z" * 100, name="s.jsonl"):
    """A synthetic Claude transcript: one round of each class, then the usage that sets the context."""
    path = tmp_path / name
    rows = [
        {"type": "user", "message": {"content": said}},
        {"type": "attachment", "attachment": {"type": "hook_additional_context", "content": [""]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": own}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": tool}]}},
        {"type": "api-request", "body": "q" * 5000},
        {"message": {"usage": {"input_tokens": 0, "cache_read_input_tokens": context}}},
    ]
    with path.open("a") as out:
        for row in rows:
            out.write(json.dumps(row) + "\n")
    return path


def test_the_scroll_census_counts_bytes_by_class_and_reads_on_from_its_offset(tmp_path):
    path = _scroll(tmp_path, 10)
    kept = reincarnate.scroll(str(path))
    assert (kept["tool"], kept["said"], kept["own"]) == (600, 300, 100)
    assert kept["offset"] == path.stat().st_size
    # Only the new tail is read; a line still being written waits.
    _scroll(tmp_path, 20, tool="x" * 400)
    with path.open("a") as out:
        out.write('{"type": "user", "message": {"content": "half')
    again = reincarnate.scroll(str(path), kept)
    assert (again["tool"], again["said"], again["own"]) == (1000, 600, 200)
    assert again["offset"] < path.stat().st_size
    # Another scroll starts over.
    assert reincarnate.scroll(str(_scroll(tmp_path, 5, name="other.jsonl")), again)["tool"] == 600


def test_the_park_line_asks_what_would_die_only_past_the_mark():
    below = reincarnate.rest_line("letter:a/b", reincarnate.MARK - 1_000, "census here")
    assert below.endswith("What does the next hour need that isn't written yet?") and "census" not in below
    above = reincarnate.rest_line("letter:a/b", reincarnate.MARK, "census here")
    assert above.startswith("census here\nresting until letter:a/b. Context 150k.")
    assert reincarnate.DIE in above and "reincarnate --why" in above and "hold --why" in above
    # A number and a question, never a verdict.
    assert "reincarnate?" not in above and "due" not in above


def test_the_census_splits_what_landed_from_what_only_this_context_holds(room, tmp_path):
    path = _scroll(tmp_path, 200_000)
    booted = time.time() - 120
    _fact(room, "letter", to="thread:inbox", body="hi")
    _fact(room, "letter", to="thread:inbox", body="again")
    _fact(room, "wish.left", wish="w-aaaaaaaaaaaa", steps=["x"])
    _fact(room, "wish.left", wish="w-aaaaaaaaaaaa", same=True)
    _fact(room, "shown", strand=STRAND, gen=1, ids=["a/1", "a/2", "a/3"])
    said, kept = reincarnate.census(room, "work", {"booted_at": booted, "transcript": str(path), "stops": 4}, [])
    first, after, counts = said.splitlines()
    assert first.startswith("since this gen booted (2m ago): nothing landed in the README")
    # Counted, not the stop's distinct kinds; a left that did not move is no act.
    assert after == "unlanded, not yet in the README: 2 letter, 1 wish.left"
    assert counts == ("scroll: tool output 60% · letters and boundaries 30% · own turns 10% · "
                      "letters 3 in, 2 out · stops 4")
    assert kept["offset"] == path.stat().st_size


def test_a_resting_stop_past_the_mark_asks_once_and_a_hold_quiets_it(room, tmp_path, monkeypatch):
    from brr.loom.runtime import verbs_loom
    small = json.dumps({"transcript_path": str(_scroll(tmp_path, 100_000, name="a.jsonl"))})
    execute("start", room, small)
    assert _turn(room, small) == ""
    big = json.dumps({"transcript_path": str(_scroll(tmp_path, 160_000, name="b.jsonl"))})
    asked = json.loads(_turn(room, big))
    assert asked["decision"] == "block"
    reason = asked["reason"]
    assert "context 160k, past the 150k mark" in reason and "would die if you reincarnated now" in reason
    assert "burden is on holding" in reason and "mid-way counts as live" in reason
    assert "scroll: tool output 60%" in reason and "stops 2" in reason
    # Asked once: the idle repeat, and the next call's stop, pass.
    assert execute("stop", room, big) == ""
    assert _turn(room, big) == ""
    # A hold names what would die; a bare one is refused and writes nothing.
    assert verbs_loom._hold(["--room", str(room), "--why", "still working, context is warm"]) == 1
    assert verbs_loom._hold(["--room", str(room), "--why", ""]) == 1
    assert verbs_loom._hold(["--room", str(room), "--why",
                             "Sasha's install answer is mid-way and the 3 failing guards are unwritten"]) == 0
    from brr.loom.runtime.home import home_of_room
    from brr.loom.runtime.ledger import read_facts
    holds = [f for f in read_facts(home_of_room(room)) if f.kind == "hold"]
    assert [f.data["why"] for f in holds] == ["Sasha's install answer is mid-way and the 3 failing guards are unwritten"]
    assert holds[0].data["context"] == 160_000 and holds[0].data["gen"] == 1
    # Past the hold by the rearm, the stop asks again.
    bigger = json.dumps({"transcript_path": str(_scroll(tmp_path, 160_000 + reincarnate.REARM, name="c.jsonl"))})
    assert "past the 150k mark" in json.loads(_turn(room, bigger))["reason"]


def test_a_park_before_the_first_stop_reads_the_scroll_itself(room, tmp_path, capsys):
    # jack-state's context is 0 until a gen's first stop; the park reads the transcript.
    from types import SimpleNamespace
    from brr.loom.runtime import verbs_wishes
    execute("start", room, json.dumps({"transcript_path": str(_scroll(tmp_path, 170_000))}))
    assert not _state(room).get("context")
    verbs_wishes._rest_line(SimpleNamespace(room=str(room)), {"wait": {"on": "letter:a/b"}})
    said = capsys.readouterr().err
    assert "resting until letter:a/b. Context 170k." in said and reincarnate.DIE in said
    assert said.startswith("since this gen booted") and "scroll: tool output 60%" in said


def test_a_ledger_written_before_the_rename_reads_in_todays_words(tmp_path):
    # 10052: no old names in the code, but the ledgers keep what they were written with.
    from brr.loom.runtime.ledger import read_file
    path = tmp_path / "old.jsonl"
    rows = [
        {"kind": "molt", "by": f"strand:{STRAND}", "id": f"{STRAND}/a", "data": {"strand": STRAND, "gen": 1, "why": "w"}},
        {"kind": "letter", "by": f"strand:{STRAND}", "id": f"{STRAND}/b",
         "data": {"to": "thread:work", "body": "Fresh molt: start from main.", "molt": f"{STRAND}/a"}},
        {"kind": "attempt.ended", "by": "loom:ab12", "id": "e", "data": {"cause": "molted"}},
        {"kind": "note", "by": f"strand:{STRAND}", "id": f"{STRAND}/c", "data": {"why": "molt"}},
    ]
    from brr.daemon2.facts import VERSION
    path.write_text("".join(json.dumps({**row, "v": VERSION, "at": "2026-11-01T00:00:00+00:00"}) + "\n"
                            for row in rows))
    molt, letter, ended, note = read_file(path)
    assert molt.kind == "reincarnate" and molt.data["why"] == "w"
    assert letter.data == {"to": "thread:work", "body": "Fresh reincarnation: start from main.",
                           "reincarnate": f"{STRAND}/a"}
    assert ended.data["cause"] == "reincarnated" and note.data == {"why": "molt"}

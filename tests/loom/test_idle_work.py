"""An idle wake works: ahead of pace the keepalive names what the thread could move (w-i1xohccy9ht0)."""
import json
import os
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from brr.loom.runtime import idle, jack, loom, usage
from brr.loom.runtime.faces import PROPOSED
from brr.loom.runtime.ledger import inject_letter, read_facts
from brr.loom.runtime.port import render_boundary
from test_never_quit import STRAND, worker  # noqa: F401  (worker is a fixture)
from test_wishes import command, ledger  # noqa: F401  (ledger is a fixture)


def _gauge(left_pct: float, days_to_go: float) -> dict:
    reset = datetime.fromtimestamp(time.time() + days_to_go * 86400, timezone.utc).isoformat()
    return {"at": time.time(), "windows": [{"name": "session", "left_pct": 90.0, "resets_at": reset},
                                           {"name": "week", "left_pct": left_pct, "resets_at": reset}]}


AHEAD, BEHIND = _gauge(92.0, 5.5), _gauge(20.0, 5.5)
BLOCK = idle.render(['w-aaaa is heard, never read: "Decide" — `wish w-aaaa --read "<what it asks>"`'])


def _stop(room, monkeypatch, gauge):
    monkeypatch.setattr(usage, "dry_until", lambda *args: None)
    monkeypatch.setattr(usage, "reading", lambda *args, **kwargs: gauge)
    (room / "port/in/idle.md").write_text(BLOCK)
    return json.loads(jack.execute("stop", room, "{}"))


def test_ahead_of_pace_the_keepalive_carries_the_acts_and_never_says_stop(worker, monkeypatch):
    room, _ = worker
    result = _stop(room, monkeypatch, AHEAD)
    assert result["decision"] == "block"
    reason = result["reason"]
    assert reason.startswith("cache keepalive, fuel ahead of pace (week 92% left, 79% of it to go)")
    assert ": stop" not in reason
    assert "  - w-aaaa is heard, never read" in reason
    assert "or end with one line saying why not" in reason


def test_behind_pace_the_keepalive_says_what_it_said(worker, monkeypatch):
    room, _ = worker
    result = _stop(room, monkeypatch, BEHIND)
    assert result["reason"].startswith("cache keepalive, nothing new arrived: stop, unless the boundary")
    assert "w-aaaa" not in result["reason"]


def test_an_unknown_gauge_is_not_ahead(worker, monkeypatch):
    room, _ = worker
    assert _stop(room, monkeypatch, None)["reason"].startswith("cache keepalive, nothing new arrived: stop")
    assert not idle.ahead({"windows": []})
    assert not idle.ahead({"windows": [AHEAD["windows"][0]]})


def test_pace_is_fuel_left_against_time_left():
    assert idle.ahead(_gauge(50.0, 3.0))
    assert not idle.ahead(_gauge(40.0, 3.0))


def _seat(home, seat, monkeypatch, gauge):
    monkeypatch.setattr(usage, "reading", lambda *args, **kwargs: gauge)
    letter = inject_letter(home.root, to="thread:work", body="Please decide the colour")
    wish = command(home, seat, "wish", "new", "--line", "Decide the colour", "--say", letter)
    (home.room("s-aaaa-test") / "port/in").mkdir(parents=True, exist_ok=True)
    return wish, SimpleNamespace(thread="work", shell="claude")


def test_a_quiet_face_ahead_of_pace_carries_the_threads_acts(ledger, monkeypatch):
    home, seat = ledger
    wish, context = _seat(home, seat, monkeypatch, AHEAD)
    quiet = {"face": PROPOSED["quiet"], "token": "quiet since 20:00", "fact": "quiet:x"}
    block = loom._idle_acts(home, read_facts(home), "s-aaaa-test", context, quiet, ())
    assert block.startswith(idle.HEAD)
    assert wish in block
    # The jack's copy is written whatever the face; another face carries nothing on the boundary.
    assert (home.room("s-aaaa-test") / "port/in/idle.md").read_text() == block
    assert block.rstrip("\n") in render_boundary("s-aaaa-test", 1, "work", [], set(), {}, idle=block)
    letter = {"face": PROPOSED["letter"], "token": "your x", "fact": "x"}
    assert loom._idle_acts(home, read_facts(home), "s-aaaa-test", context, letter, ()) == ""


def test_acts_are_derived_at_most_once_per_stamp(ledger, monkeypatch):
    home, seat = ledger
    _, context = _seat(home, seat, monkeypatch, AHEAD)
    calls = []
    real = idle.candidates
    monkeypatch.setattr(idle, "candidates", lambda *args: calls.append(args) or real(*args))
    quiet = {"face": PROPOSED["quiet"], "token": "quiet since 20:00", "fact": "quiet:x"}
    first = loom._idle_acts(home, read_facts(home), "s-aaaa-test", context, quiet, ())
    assert loom._idle_acts(home, read_facts(home), "s-aaaa-test", context, quiet, ()) == first
    assert len(calls) == 1
    path = home.room("s-aaaa-test") / "port/in/idle.md"
    old = time.time() - idle.EVERY - 1
    os.utime(path, (old, old))
    loom._idle_acts(home, read_facts(home), "s-aaaa-test", context, quiet, ())
    assert len(calls) == 2


def test_a_render_behind_pace_derives_nothing(ledger, monkeypatch):
    home, seat = ledger
    _, context = _seat(home, seat, monkeypatch, BEHIND)
    path = home.room("s-aaaa-test") / "port/in/idle.md"
    path.write_text(BLOCK)
    monkeypatch.setattr(idle, "candidates", lambda *args: (_ for _ in ()).throw(AssertionError("derived")))
    quiet = {"face": PROPOSED["quiet"], "token": "quiet since 20:00", "fact": "quiet:x"}
    assert loom._idle_acts(home, read_facts(home), "s-aaaa-test", context, quiet, ()) == ""
    # A stale list never outlives the pace that asked for it.
    assert not path.exists()


def test_a_child_quiet_for_days_with_nothing_in_hand_is_an_act(ledger, monkeypatch):
    home, seat = ledger
    row = {"thread": "kid", "now": "idle 4d", "owed": 0, "prs": [], "waits": "", "status": "open"}
    acts = idle.candidates(home, read_facts(home), "work", [row, {**row, "thread": "busy", "owed": 1},
                                                             {**row, "thread": "fresh", "now": "idle 2d"}])
    assert acts == ["thread kid quiet 4d with nothing in hand: write to it with its next step, or settle it"]


def test_a_parked_wish_is_offered_as_its_wait_not_as_the_top(ledger, monkeypatch):
    home, seat = ledger
    wish, _ = _seat(home, seat, monkeypatch, AHEAD)
    command(home, seat, "wish", wish, "--read", "waiting: CI on #1", "--wait", "at:2099-01-01T00:00:00Z")
    acts = idle.candidates(home, read_facts(home), "work")
    assert [a for a in acts if a.startswith(wish)] == [next(a for a in acts if "parked" in a)]


def test_nothing_to_move_is_stamped_too(ledger, monkeypatch):
    home, _ = ledger
    monkeypatch.setattr(usage, "reading", lambda *args, **kwargs: AHEAD)
    (home.room("s-aaaa-test") / "port/in").mkdir(parents=True, exist_ok=True)
    calls = []
    monkeypatch.setattr(idle, "candidates", lambda *args: calls.append(args) or [])
    context = SimpleNamespace(thread="work", shell="claude")
    for _ in range(2):
        assert loom._idle_acts(home, read_facts(home), "s-aaaa-test", context, None, ()) == ""
    assert (home.room("s-aaaa-test") / "port/in/idle.md").read_text() == ""
    assert len(calls) == 1

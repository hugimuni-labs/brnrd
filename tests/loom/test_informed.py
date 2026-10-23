"""The loop tells: degraded steps become facts, and a wake opens with what happened since."""

from datetime import datetime, timedelta, timezone

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime import health, loom
from brr.loom.runtime.home import Home, notice_id
from brr.loom.runtime.ledger import append, read_facts
from brr.loom.runtime.loom import _prepare
from brr.loom.runtime.port import parse_boundary
from brr.loom.runtime.project import sender_threads
from brr.loom.runtime.since import HEADER

from _step import Loom, mail, wait_until, write_thread

PREV, NEXT = "s-aaaa-before", "s-aaaa-after"


def _kinds(root, kind):
    return [f for f in read_facts(Home(root)) if f.kind == kind]


def _running(root, until, timeout=10):
    looming = Loom(root, tick=0.01)
    looming.start()
    try:
        wait_until(until, timeout, looming.dump)
    finally:
        looming.halt()
    assert not looming.errors


def test_a_failing_step_opens_one_streak_backs_off_and_recovers(tmp_path, monkeypatch):
    root = tmp_path / "home"
    write_thread(root, "brnrd", "Brnrd", "brnrd")
    real, calls = loom.tick_once, []

    def flaky(*args):
        calls.append(1)
        if len(calls) <= 3:
            raise ValueError("not enough values to unpack")
        return real(*args)

    monkeypatch.setattr(loom, "tick_once", flaky)
    _running(root, lambda: _kinds(root, "loom.recovered"))
    [down] = _kinds(root, "loom.degraded")
    [back] = _kinds(root, "loom.recovered")
    install = Home(root).install_id()
    assert down.id == f"loom.degraded:{install}:tick_once:{down.data['since']}"
    assert down.data["error"] == "ValueError: not enough values to unpack"
    assert down.data["where"].startswith("loom/runtime/")
    assert back.data == {"install": install, "step": "tick_once", "since": down.data["since"],
                         "count": 3, "last": "ValueError: not enough values to unpack"}
    log = (root / "loom/loom.log").read_text()
    assert log.count("Traceback") == 1
    # Backoff skips 1, then 2, then 4 calls: the fourth attempt is the eighth tick.
    assert len(calls) >= 4


def test_a_restart_closes_a_streak_its_predecessor_left_open(tmp_path):
    root = tmp_path / "home"
    write_thread(root, "brnrd", "Brnrd", "brnrd")
    home = Home(root)
    install, since = home.install_id(), "2026-10-23T09:00:00Z"
    append(home, Fact(kind="loom.degraded", by=f"loom:{install}", id=f"loom.degraded:{install}:inputs:{since}",
                      data={"install": install, "step": "inputs", "error": "OSError: gone",
                            "where": "loom/runtime/connector.py:76", "since": since}))
    _running(root, lambda: _kinds(root, "loom.recovered"))
    [back] = _kinds(root, "loom.recovered")
    assert back.id == f"loom.recovered:{install}:inputs:{since}"
    assert back.data["count"] == 1 and back.data["last"] == "OSError: gone"


def test_a_streak_that_lasts_is_told_to_the_home_thread_once(tmp_path, monkeypatch):
    root = tmp_path / "home"
    write_thread(root, "brnrd", "Brnrd", "brnrd")
    monkeypatch.setattr(health, "NOTICE_AFTER", 0)
    monkeypatch.setattr(health, "MAX_BACKOFF", 1)

    def broken(*_args):
        raise KeyError("says")

    monkeypatch.setattr(loom, "tick_once", broken)
    told = lambda: [f for f in _kinds(root, "letter") if f.data.get("from") == "loom"]
    _running(root, lambda: told() and len(_kinds(root, "loom.degraded")) == 1, timeout=10)
    [down] = _kinds(root, "loom.degraded")
    [letter] = told()
    assert letter.id == notice_id("degraded", f"tick_once:{down.data['since']}")
    assert letter.data["to"] == "thread:brnrd"
    assert "KeyError: 'says'" in letter.data["body"]


def _at(minutes):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


@pytest.fixture
def gone(tmp_path):
    root = tmp_path / "home"
    write_thread(root, "brnrd", "Brnrd", "quit-now")
    home = Home(root, install="aaaa")
    by = "loom:aaaa"
    for fact in [
        Fact(kind="lease", by=by, id="lease:brnrd:prev", data={"thread": "brnrd", "strand": PREV, "gen": 1}),
        Fact(kind="body.started", by=by, data={"strand": PREV, "gen": 1, "pid": 1, "shell": "fake",
                                                "core": "haiku", "readme": "", "lstart": ""}),
        Fact(kind="shown", by=by, data={"strand": PREV, "gen": 1, "ids": []}, at=_at(-180)),
        Fact(kind="loom.started", by=by, at=_at(-60),
             data={"install": "aaaa", "pid": 2, "commit": "585f1321abcd", "loaded": []}),
        Fact(kind="attempt.ended", by=by, id=f"attempt.ended:{PREV}:1", at=_at(-59), data={
            "thread": "brnrd", "strand": PREV, "gen": 1, "ids": [], "cause": "failed", "code": -9,
            "dispositions": [], "evidence": {}}),
        Fact(kind="note", by="person:test", id="hand-1", at=_at(-30),
             data={"why": "moved the host to 585f1321 by hand"}),
        Fact(kind="loom.degraded", by=by, id="loom.degraded:aaaa:publisher:2026-10-23T09:00:00Z", at=_at(-20),
             data={"install": "aaaa", "step": "publisher", "error": "ValueError: too many values",
                   "where": "loom/runtime/snapshot.py:166", "since": "2026-10-23T09:00:00Z"}),
        Fact(kind="lease", by=by, id="lease:brnrd:next", data={"thread": "brnrd", "strand": NEXT, "gen": 1}),
    ]:
        append(home, fact)
    return home


def test_the_wake_opens_with_what_happened_since_the_predecessor(gone):
    facts = read_facts(gone)
    room = _prepare(gone, NEXT, "brnrd", 1, mail(gone.root, facts, "brnrd"),
                    sender_threads(facts), facts, "hold")
    part = (room / "port/loom.md").read_text()
    section = part[:part.index("you are strand")]
    assert section.startswith(HEADER + "\n")
    assert len(section.encode()) <= 1500
    assert (f"your predecessor {PREV} gen 1 was lost to a loom restart (recorded failed, code -9) "
            f"(attempt.ended:{PREV}:1)") in section
    assert "note by person:test: moved the host to 585f1321 by hand (hand-1)" in section
    assert ("degraded now: publisher since 2026-10-23T09:00:00Z: ValueError: too many values "
            "at loom/runtime/snapshot.py:166 (loom.degraded:aaaa:publisher:2026-10-23T09:00:00Z)") in section
    boundary = (room / "port/in/boundary.md").read_text()
    assert "degraded: publisher since 09:00Z (ValueError at snapshot.py:166)" in boundary
    assert parse_boundary(boundary).strand == NEXT


def test_a_silent_ledger_before_a_start_is_named(gone):
    append(gone, Fact(kind="loom.started", by="loom:aaaa", id="start-late", at=_at(120),
                      data={"install": "aaaa", "pid": 3, "commit": "9b1254f5", "loaded": []}))
    facts = read_facts(gone)
    room = _prepare(gone, NEXT, "brnrd", 1, [], sender_threads(facts), facts, "hold")
    part = (room / "port/loom.md").read_text()
    assert "the loom started 1 time (at 9b1254f5); the ledger was silent 2h" in part
    assert "(a power-off or a stopped loop) (start-late)" in part


def test_a_first_body_has_no_section(tmp_path):
    root = tmp_path / "home"
    write_thread(root, "brnrd", "Brnrd", "quit-now")
    home = Home(root, install="aaaa")
    append(home, Fact(kind="lease", by="loom:aaaa", id="lease:brnrd:1",
                      data={"thread": "brnrd", "strand": NEXT, "gen": 1}))
    facts = read_facts(home)
    room = _prepare(home, NEXT, "brnrd", 1, [], sender_threads(facts), facts, "hold")
    assert (room / "port/loom.md").read_text().startswith("you are strand")

"""A body never quits: it holds until it settles, molts or runs dry, and says how it ends."""
import json

import pytest

from brr.loom.runtime import jack, usage
from brr.loom.runtime.ledger import inject_letter, read_facts
from brr.loom.runtime.loom import _plan
from brr.loom.runtime.port import render_boundary
from brr.loom.runtime.selfrepo import init_self
from _step import write_thread
from test_wishes import command, ledger  # noqa: F401  (ledger is a fixture)

STRAND = "s-ab12-aaaaaa"


@pytest.fixture
def worker(tmp_path, monkeypatch):
    """A room on a thread another thread opened, with nothing owed and a 55-minute wait."""
    root = tmp_path / "home"
    init_self(root, person="test")
    write_thread(root, "work", "---\nid: work\nstatus: open\ntense: plan\n"
                 "opened-by: thread:opener\n---\n# Worker\n", "work", wait="3300")
    room = root / "rooms" / STRAND
    (room / "port/in").mkdir(parents=True)
    (room / "port/out").mkdir()
    (room / "port/in/boundary.md").write_text(render_boundary(STRAND, 1, "work", [], set(), {}))
    (room / "port/wait").write_text("3300\n")
    monkeypatch.setenv("BRNRD_THREAD", "work")
    monkeypatch.setenv("BRNRD_BODY_SHELL", "claude")
    monkeypatch.setenv("BRNRD_BODY_JACK", "hooks")
    monkeypatch.setenv("BRNRD_STOP_TIMEOUT", str(6 * 3600 + 60))
    now = [0.]
    monkeypatch.setattr(jack.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(jack.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    return room, now


def test_a_thread_opened_body_owing_nothing_is_held_past_its_wait(worker, monkeypatch):
    room, now = worker
    monkeypatch.setattr(usage, "dry_until", lambda *args: None)
    result = json.loads(jack.execute("stop", room, "{}"))
    assert result["decision"] == "block"
    assert result["reason"].startswith("cache keepalive, nothing new arrived")
    assert now[0] == pytest.approx(3300)


def test_a_thread_opened_body_that_runs_dry_within_the_hook_sleeps_then_holds(worker, monkeypatch):
    room, now = worker
    # Dry for ten minutes, a refill well inside the hook's lifetime.
    monkeypatch.setattr(usage, "dry_until", lambda *args: 600.0 if now[0] < 600 else None)
    monkeypatch.setattr(jack, "_refilling", lambda *args: False)
    result = json.loads(jack.execute("stop", room, "{}"))
    assert result["decision"] == "block"
    assert result["reason"].startswith("cache keepalive, nothing new arrived")
    # Parked past the cache: the next keepalive waits for the ceiling.
    assert now[0] == pytest.approx(6 * 3600 + 60 - jack.MARGIN)


def test_the_first_stop_with_wishes_open_is_refused_until_the_body_asks(ledger, monkeypatch):
    home, seat = ledger
    letter = inject_letter(home.root, to="thread:work", body="Please decide")
    wish = command(home, seat, "wish", "new", "--line", "Decide", "--say", letter)
    plan = _plan(home, "work", read_facts(home))
    assert plan.startswith("plan 0/1")
    (seat / "port/in").mkdir(parents=True, exist_ok=True)
    (seat / "port/out").mkdir(exist_ok=True)
    (seat / "port/in/boundary.md").write_text(
        render_boundary("s-aaaa-test", 1, "work", [], set(), {}, plan=plan))
    monkeypatch.setenv("BRNRD_THREAD", "work")
    monkeypatch.setenv("BRNRD_BODY_JACK", "hooks")
    monkeypatch.delenv("BRNRD_BODY_SHELL", raising=False)
    monkeypatch.setenv("BRNRD_STOP_TIMEOUT", "100")
    now = [0.]
    monkeypatch.setattr(jack.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(jack.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    refused = json.loads(jack.execute("stop", seat, "{}"))
    assert refused["decision"] == "block"
    assert "has not said how it ends" in refused["reason"]
    assert 'python -m brr.loom.runtime ask "<question>" --for <w-id>' in refused["reason"]
    assert "settle work --why" in refused["reason"]
    assert now[0] == 0
    # A fresh body (the loom deletes jack-state at every start) asks, then stops.
    (seat / "port/jack-state.json").unlink()
    command(home, seat, "ask", "Which colour?", "--for", wish, "--rec", "grey")
    held = json.loads(jack.execute("stop", seat, "{}"))
    assert held["reason"].startswith("cache keepalive, nothing new arrived")
    assert now[0] > 0


def test_a_body_with_no_open_wish_is_not_asked_how_it_ends(worker, monkeypatch):
    room, now = worker
    monkeypatch.setattr(usage, "dry_until", lambda *args: None)
    (room / "port/in/boundary.md").write_text(
        render_boundary(STRAND, 1, "work", [], set(), {}, plan="plan 2/2"))
    result = json.loads(jack.execute("stop", room, "{}"))
    assert result["reason"].startswith("cache keepalive, nothing new arrived")


def test_a_body_that_settles_with_wishes_open_is_not_asked_how_it_ends(ledger, monkeypatch):
    home, seat = ledger
    letter = inject_letter(home.root, to="thread:work", body="Please decide")
    command(home, seat, "wish", "new", "--line", "Decide", "--say", letter)
    plan = _plan(home, "work", read_facts(home))
    assert plan.startswith("plan 0/1")
    (seat / "port/in").mkdir(parents=True, exist_ok=True)
    (seat / "port/out").mkdir(exist_ok=True)
    # The boundary the body last saw: the settle has not reached it yet.
    (seat / "port/in/boundary.md").write_text(
        render_boundary("s-aaaa-test", 1, "work", [], set(), {}, plan=plan))
    monkeypatch.setenv("BRNRD_THREAD", "work")
    monkeypatch.setenv("BRNRD_BODY_JACK", "hooks")
    monkeypatch.delenv("BRNRD_BODY_SHELL", raising=False)
    monkeypatch.setenv("BRNRD_STOP_TIMEOUT", "100")
    now = [0.]
    monkeypatch.setattr(jack.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(jack.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    command(home, seat, "settle", "work", "--word", inject_letter(home.root, to="thread:work", body="done"),
            "--why", "Parked for good")
    out = jack.execute("stop", seat, "{}")
    assert "has not said how it ends" not in out


def test_only_a_reading_that_says_what_it_waits_on_is_a_park(ledger, monkeypatch):
    home, seat = ledger
    letter = inject_letter(home.root, to="thread:work", body="Please decide")
    wish = command(home, seat, "wish", "new", "--line", "Decide", "--say", letter)
    (seat / "port/in").mkdir(parents=True, exist_ok=True)
    (seat / "port/out").mkdir(exist_ok=True)
    (seat / "port/in/boundary.md").write_text(render_boundary(
        "s-aaaa-test", 1, "work", [], set(), {}, plan=_plan(home, "work", read_facts(home))))
    monkeypatch.setenv("BRNRD_THREAD", "work")
    monkeypatch.setenv("BRNRD_BODY_JACK", "hooks")
    monkeypatch.delenv("BRNRD_BODY_SHELL", raising=False)
    monkeypatch.setenv("BRNRD_STOP_TIMEOUT", "100")
    now = [0.]
    monkeypatch.setattr(jack.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(jack.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    command(home, seat, "wish", wish, "--read", "looks hard")
    assert "has not said how it ends" in jack.execute("stop", seat, "{}")
    (seat / "port/jack-state.json").unlink()
    command(home, seat, "wish", wish, "--read", "waiting: his answer on the colour")
    assert "has not said how it ends" not in jack.execute("stop", seat, "{}")

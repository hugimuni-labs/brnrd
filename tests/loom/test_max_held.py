"""max_held: past the limit, the body held longest leaves at its stop hook; work is never cut."""
import json
from types import SimpleNamespace

import pytest

from brr.loom.runtime import jack, loom, usage
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import inject_letter
from brr.loom.runtime.port import render_boundary
from test_never_quit import worker  # noqa: F401  (fixture)


def _state(room):
    return json.loads((room / "port/jack-state.json").read_text())


def test_a_held_body_marks_when_it_parked_and_leaves_once_displaced(worker, monkeypatch):
    room, now = worker
    monkeypatch.setattr(usage, "dry_until", lambda *args: None)
    sleep = jack.time.sleep

    def displace_later(seconds):
        sleep(seconds)
        if now[0] >= 600:
            (room / "port" / jack.DISPLACED).write_text("6 live bodies, max_held 5\n")

    monkeypatch.setattr(jack.time, "sleep", displace_later)
    assert jack.execute("stop", room, "{}") == ""
    assert now[0] == pytest.approx(600, abs=1)
    assert _state(room)["held_at"]


def test_a_keepalive_turn_keeps_the_held_mark_and_real_work_clears_it(worker, monkeypatch):
    room, _now = worker
    monkeypatch.setattr(usage, "dry_until", lambda *args: None)
    json.loads(jack.execute("stop", room, "{}"))  # keepalive block at the wait
    first = _state(room)["held_at"]
    json.loads(jack.execute("stop", room, "{}"))
    assert _state(room)["held_at"] == first
    jack.execute("post", room, json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}}))
    assert not _state(room).get("held_at")


def test_a_displaced_body_with_a_letter_owed_takes_the_letter(worker, monkeypatch):
    room, _now = worker
    monkeypatch.setattr(usage, "dry_until", lambda *args: None)
    (room / "port" / jack.DISPLACED).write_text("x\n")
    root = room.parent.parent
    letter = inject_letter(root, to="thread:work", body="One more thing")
    from brr.loom.runtime.ledger import read_facts
    fact = next(f for f in read_facts(Home(root)) if f.id == letter)
    (room / "port/in/boundary.md").write_text(
        render_boundary("s-ab12-aaaaaa", 1, "work", [fact], set(), {}))
    result = json.loads(jack.execute("stop", room, "{}"))
    assert result["decision"] == "block"
    assert "One more thing" in result["reason"]
    assert not (room / "port" / jack.DISPLACED).exists()
    assert not _state(room).get("held_at")


@pytest.fixture
def held(tmp_path, monkeypatch):
    """Six live non-inbox bodies plus the inbox's; which are held is set per test."""
    root = tmp_path / "home"
    (root / "loom").mkdir(parents=True)
    (root / "loom/install-id").write_text("ab12\n")
    (root / "self/threads/inbox").mkdir(parents=True)
    home = Home(root)
    holder = {"inbox": ("s-ab12-inbox0", 9)}
    holder.update({f"t{i}": (f"s-ab12-body0{i}", 1) for i in range(6)})
    for strand, _gen in holder.values():
        (home.room(strand) / "port").mkdir(parents=True)
    monkeypatch.setattr(loom, "fold", lambda facts: SimpleNamespace(holder=holder))
    monkeypatch.setattr(loom, "body_alive", lambda room: True)

    def hold(strand, at):
        (home.room(strand) / "port/jack-state.json").write_text(json.dumps({"held_at": at}))

    def limit(n):
        (root / "loom/config.toml").write_text(f"max_held = {n}\n")

    return home, hold, limit


def _displaced(home):
    return sorted(p.parent.parent.name for p in home.root.glob("rooms/*/port/displaced"))


def test_unset_max_held_displaces_nothing(held):
    home, hold, _limit = held
    for i in range(6):
        hold(f"s-ab12-body0{i}", 100 + i)
    loom._displace(home, [])
    assert _displaced(home) == []


def test_over_the_limit_the_longest_held_leaves_and_the_inbox_is_not_counted(held):
    home, hold, limit = held
    limit(4)
    hold("s-ab12-inbox0", 1)  # held longest of all, never displaced
    hold("s-ab12-body03", 50)
    hold("s-ab12-body01", 10)
    hold("s-ab12-body05", 30)
    loom._displace(home, [])
    # 6 live past the inbox, limit 4: the two held longest go.
    assert _displaced(home) == ["s-ab12-body01", "s-ab12-body05"]
    # A second tick before they leave marks no one else.
    loom._displace(home, [])
    assert _displaced(home) == ["s-ab12-body01", "s-ab12-body05"]


def test_working_bodies_are_never_displaced(held):
    home, hold, limit = held
    limit(2)
    hold("s-ab12-body02", 10)  # the only parked body
    loom._displace(home, [])
    assert _displaced(home) == ["s-ab12-body02"]

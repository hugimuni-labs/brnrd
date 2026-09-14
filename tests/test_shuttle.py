from __future__ import annotations

import json

import pytest

from brr import shuttle


def _home(tmp_path):
    home = tmp_path / "home"
    registry = home / "account" / "repos.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({"account_id": "acc-test"}), encoding="utf-8")
    return home


def test_first_read_creates_a_released_record(tmp_path):
    home = _home(tmp_path)

    entity = shuttle.Shuttle.load(home)

    assert entity.key == "acc-test"
    assert entity.state == "released"
    assert entity.why == "first_read"
    assert entity.transitions == []
    assert (home / "shuttle.json").is_file()


def test_persistence_round_trip_and_transition_row_shape(tmp_path):
    home = _home(tmp_path)
    entity = shuttle.Shuttle.load(home)

    entity.transition(
        "awake", why="event_dispatched", by="daemon", run_id="run-1",
        repo_root="/repo", conversation_key="cloud:telegram:1:",
    )

    reread = shuttle.Shuttle.load(home)
    assert reread == entity
    assert reread.transitions[-1] == {
        "at": reread.since,
        "from": "released",
        "to": "awake",
        "why": "event_dispatched",
        "by": "daemon",
        "tick": None,
    }
    assert reread.run_id == "run-1"
    assert reread.repo_root == "/repo"
    assert reread.conversation_key == "cloud:telegram:1:"


@pytest.mark.parametrize("source,target", [
    ("awake", "listening"),
    ("listening", "awake"),
    ("awake", "parked"),
    ("listening", "parked"),
    ("parked", "awake"),
    ("parked", "released"),
    ("awake", "handing-off"),
    ("listening", "handing-off"),
    ("handing-off", "awake"),
    ("awake", "released"),
    ("listening", "released"),
    ("released", "awake"),
])
def test_every_allowed_edge(tmp_path, source, target):
    home = _home(tmp_path)
    entity = shuttle.Shuttle.load(home)
    entity.state = source
    entity.save()

    entity.transition(target, why="test")

    assert shuttle.Shuttle.load(home).state == target


@pytest.mark.parametrize("source,target", [
    ("released", "parked"),
    ("parked", "listening"),
    ("awake", "awake"),
])
def test_refused_edges_name_the_edge(tmp_path, source, target):
    home = _home(tmp_path)
    entity = shuttle.Shuttle.load(home)
    entity.state = source
    entity.save()

    with pytest.raises(ValueError, match=rf"{source} -> {target}"):
        entity.transition(target, why="test")


def test_transition_history_keeps_the_last_200_rows(tmp_path):
    home = _home(tmp_path)
    entity = shuttle.Shuttle.load(home)
    entity.transition("awake", why="start")
    for index in range(205):
        target = "listening" if entity.state == "awake" else "awake"
        entity.transition(target, why=f"row-{index}")

    reread = shuttle.Shuttle.load(home)
    assert len(reread.transitions) == 200
    assert reread.transitions[0]["why"] == "row-5"
    assert reread.transitions[-1]["why"] == "row-204"


def test_a_transition_row_carries_the_frame_tick(tmp_path):
    from brr import tick

    home = _home(tmp_path)
    entity = shuttle.Shuttle.load(home)
    tick.advance(home)
    tick.advance(home)

    entity.transition("awake", why="event_dispatched")
    entity.transition("listening", why="await_armed", tick=41)

    rows = shuttle.Shuttle.load(home).transitions
    assert rows[-2]["tick"] == 2  # the loop's latest, read by default
    assert rows[-1]["tick"] == 41  # the caller's beat wins

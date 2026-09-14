"""The frame's beat (move 2b): one numbered tick per daemon loop iteration."""

from __future__ import annotations

import json
import os

import pytest

from brr import tick


def test_first_tick_is_one_and_each_advance_is_the_next(tmp_path):
    assert tick.current(tmp_path) is None

    first = tick.advance(tmp_path)
    second = tick.advance(tmp_path)
    third = tick.advance(tmp_path)

    assert [first.n, second.n, third.n] == [1, 2, 3]
    assert first.mono <= second.mono <= third.mono
    assert tick.current(tmp_path) == third
    assert tick.current() == third
    assert tick.current_n(tmp_path) == 3


def test_next_is_pure_over_the_previous_tick():
    previous = tick.Tick(n=41, at="2026-09-14T00:00:00Z", mono=1.0)

    successor = tick.Tick.next(previous)

    assert successor.n == 42
    assert tick.Tick.next(None).n == 1
    with pytest.raises(AttributeError):
        successor.n = 7  # frozen


def test_the_count_survives_a_reload_and_never_repeats(tmp_path):
    for _ in range(5):
        tick.advance(tmp_path)
    # A re-exec: the process forgets, the file does not.
    tick._reset_for_tests()
    assert tick.current() is None
    assert tick.current(tmp_path).n == 5

    resumed = tick.advance(tmp_path)

    assert resumed.n == 6
    persisted = json.loads((tmp_path / "tick.json").read_text(encoding="utf-8"))
    assert persisted["n"] == 6
    assert set(persisted) == {"n", "at", "mono"}


def test_the_write_is_atomic_and_leaves_no_temp_behind(tmp_path, monkeypatch):
    tick.advance(tmp_path)
    before = (tmp_path / "tick.json").read_text(encoding="utf-8")

    def boom(src, dst):
        raise OSError("disk said no")

    monkeypatch.setattr(tick.os, "replace", boom)
    with pytest.raises(OSError):
        tick.advance(tmp_path)

    # The old record is intact, no half-written temp is left, and the beat
    # still moved in memory — it cannot repeat inside this process.
    assert (tmp_path / "tick.json").read_text(encoding="utf-8") == before
    assert [p for p in os.listdir(tmp_path) if p.endswith(".tmp")] == []
    assert tick.current().n == 2
    monkeypatch.undo()
    assert tick.advance(tmp_path).n == 3


def test_a_malformed_record_is_loud_on_advance_and_quiet_on_read(tmp_path):
    (tmp_path / "tick.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid tick record"):
        tick.advance(tmp_path)
    assert tick.current(tmp_path) is None

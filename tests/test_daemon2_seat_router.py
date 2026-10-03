from __future__ import annotations

import multiprocessing
import os
import signal
from pathlib import Path

import pytest

from brr.daemon2.facts import FactStore
from brr.daemon2.router import Router, UnaddressedLetter
from brr.daemon2.seat import (
    Seat, SeatStore, Signal, StaleSeat, WakePredicate, legacy_wake_on,
)
from brr.daemon2.statecharts import load
from brr.daemon2.supervisor import Supervisor


def _checkpoint_then_die(root: str) -> None:
    seat = Seat(SeatStore(Path(root)), "telegram:owner")
    started = seat.start(0)
    saved = seat.checkpoint(
        started.generation, data={"next": "answer letter", "draft": "safe"},
        obligations=("letter:x", "promise:y"),
        native_session={"shell": "codex", "id": "native-1",
                        "valid_until": 200, "valid_for": ["repo"]},
        queued_letters=("x",))
    parked = seat.park(saved.generation, why="turn_ended",
                       wake_on=legacy_wake_on("any"))
    # The checkpoint is fsynced before the kill. A multiprocessing Queue
    # uses a feeder thread and can lose its last message on SIGKILL.
    os.kill(os.getpid(), signal.SIGKILL)


def test_statecharts_are_checked_data() -> None:
    assert load("seat").next("parked", "dispatch") == "running"
    assert load("letter").next("claimed", "expire") == "pending"
    assert load("ask").next("working", "deliver") == "delivered"
    with pytest.raises(ValueError, match="illegal"):
        load("seat").next("ended", "wake")


def test_router_keeps_conversation_across_repos_and_rejects_foreign_child() -> None:
    router = Router()
    first = router.route({"source": "telegram", "conversation_key": "chat:7",
                          "repo_label": "org/a", "ask_id": "w-115"})
    second = router.route({"source": "telegram", "conversation_key": "chat:7",
                           "repo_label": "org/b", "ask_id": "w-115"})
    assert first.conversation == second.conversation == "chat:7"
    assert first.repo_hint != second.repo_hint
    # A default-repo change cannot alter the address of a letter.
    assert router.route({"source": "telegram", "conversation_key": "chat:7",
                         "repo_label": "new/default"}).conversation == "chat:7"
    with pytest.raises(UnaddressedLetter):
        router.route({"source": "spawn_completed", "repo_label": "org/a",
                      "parent_run_id": "p", "spawn_edge": "e"})
    assert router.route({"source": "spawn_completed", "conversation_key": "chat:7",
                         "parent_run_id": "p", "spawn_edge": "e"}).parent == "p"


def test_legacy_wakes_are_typed_and_foreign_signals_do_not_wake() -> None:
    now = 100.0
    refill = legacy_wake_on("refill", pool="codex", floor=10, freshness=30)
    assert not any(p.matches(Signal("mail", "c"), now=now, conversation="c")
                   for p in refill)
    assert not any(p.matches(Signal("resource", "c", pool="codex",
                                    remaining_pct=20, measured_at=60),
                             now=now, conversation="c") for p in refill)
    assert any(p.matches(Signal("resource", "c", pool="codex",
                                remaining_pct=20, measured_at=90),
                         now=now, conversation="c") for p in refill)
    children = legacy_wake_on("strands", parent="p", edges=("e1",),
                              schedules=("morning",))
    assert not any(p.matches(Signal("child", "other", parent="p", edge="e1"),
                             now=now, conversation="c") for p in children)
    assert not any(p.matches(Signal("child", "c", parent="foreign", edge="e1"),
                             now=now, conversation="c") for p in children)
    assert any(p.matches(Signal("schedule", "c", schedule="morning"),
                         now=now, conversation="c") for p in children)
    with pytest.raises(ValueError, match="retired"):
        legacy_wake_on("raise")


def test_forced_kill_recovers_checkpoint_and_obligations(tmp_path: Path) -> None:
    child = multiprocessing.Process(target=_checkpoint_then_die,
                                    args=(str(tmp_path),))
    child.start()
    child.join(timeout=5)
    assert child.exitcode == -signal.SIGKILL
    seat = Seat(SeatStore(tmp_path), "telegram:owner", clock=lambda: 100)
    record = seat.read()
    generation = record.generation
    assert record.generation == generation
    assert record.checkpoint["next"] == "answer letter"
    assert record.obligations == ("letter:x", "promise:y")
    assert record.queued_letters == ("x",)
    assert seat.wake(generation, Signal("mail", "foreign"),
                     shell="codex", capabilities={"repo"}) is None
    wake = seat.wake(generation, Signal("mail", "telegram:owner"),
                     shell="codex", capabilities={"repo"})
    assert wake is not None and wake.mode == "native"
    assert wake.record.resume_generation == 1
    with pytest.raises(StaleSeat):
        seat.wake(generation, Signal("mail", "telegram:owner"),
                  shell="codex", capabilities={"repo"})


def test_incompatible_native_session_uses_checkpoint(tmp_path: Path) -> None:
    seat = Seat(SeatStore(tmp_path), "c", clock=lambda: 100)
    started = seat.start(0)
    saved = seat.checkpoint(started.generation, data={"next": "continue"},
                            obligations=("x",),
                            native_session={"shell": "claude", "id": "old",
                                            "valid_until": 200, "valid_for": []})
    parked = seat.park(saved.generation, why="reset",
                       wake_on=legacy_wake_on("reset", deadline=90))
    wake = seat.wake(parked.generation, Signal("timer", "c"),
                     shell="codex", capabilities=set())
    assert wake is not None and wake.mode == "checkpoint"
    assert wake.record.obligations == ("x",)


def test_child_return_keeps_parent_edge(tmp_path: Path) -> None:
    supervisor = Supervisor(FactStore(tmp_path))
    supervisor.register("w-1", "conversation-a", "parent-1", "edge-1", "child-1")
    with pytest.raises(ValueError, match="foreign"):
        supervisor.returned("w-1", "conversation-b", "parent-1",
                            "edge-1", "child-1")
    result = supervisor.returned("w-1", "conversation-a", "parent-1",
                                 "edge-1", "child-1", report="/tmp/report",
                                 branch="brr/child")
    assert (result.conversation, result.parent, result.edge) == (
        "conversation-a", "parent-1", "edge-1")
    assert supervisor.children("w-1")["edge-1"].status == "returned"

from __future__ import annotations

from pathlib import Path

import pytest

from brr.daemon2.facts import FactStore
from brr.daemon2.authority import SignalAuthority
from brr.daemon2.seat import (
    Seat, SeatStore, Signal, WakePredicate,
)
from brr.daemon2.supervisor import Supervisor


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


def test_control_and_handover_authority_comes_from_durable_facts(tmp_path: Path) -> None:
    facts = FactStore(tmp_path / "facts")
    seats = SeatStore(tmp_path / "seats")
    authority = SignalAuthority(facts, seats)
    facts.record("letters", "owner-msg", "pending", "door",
                 {"conversation": "c", "trust_tier": "owner"})
    facts.record("letters", "stranger-msg", "pending", "door",
                 {"conversation": "c", "trust_tier": "stranger"})
    control = WakePredicate("U", {"actions": ["force"]})
    assert control.matches(Signal("control", "c", control="force",
                                  letter_id="owner-msg"),
                           now=100, conversation="c", authorize=authority.allowed)
    assert not control.matches(Signal("control", "c", control="force",
                                      letter_id="stranger-msg"),
                               now=100, conversation="c", authorize=authority.allowed)
    seat = Seat(seats, "c", authorize=authority.allowed)
    started = seat.start(0, run_id="r1")
    parked = seat.handover(started.generation, carry={"next": "resume"},
                           wake_on=(WakePredicate("H"),))
    assert seat.wake(parked.generation,
                     Signal("handover", "c", from_run="r1", from_generation=0),
                     shell="codex", capabilities=set()) is None
    assert seat.wake(parked.generation,
                     Signal("handover", "c", from_run="r1",
                            from_generation=started.generation),
                     shell="codex", capabilities=set()) is not None

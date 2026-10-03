"""One proof path for owner controls and carried handovers."""

from __future__ import annotations

from .facts import FactStore
from .seat import SeatStore, Signal


class SignalAuthority:
    def __init__(self, facts: FactStore, seats: SeatStore):
        self.facts = facts
        self.seats = seats

    def allowed(self, signal: Signal) -> bool:
        if signal.kind == "control":
            if not signal.letter_id:
                return False
            pending = [fact for fact in self.facts.read("letters", signal.letter_id)
                       if fact.kind == "pending"]
            return any(fact.data.get("conversation") == signal.conversation
                       and fact.data.get("trust_tier") == "owner"
                       for fact in pending)
        if signal.kind == "handover":
            record = self.seats.read(signal.conversation)
            return (record.state == "parked"
                    and record.handover_from == {
                        "run": signal.from_run,
                        "generation": signal.from_generation,
                    })
        return False

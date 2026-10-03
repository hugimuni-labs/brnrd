"""Letter lifecycle built from facts and expiring claim leases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .facts import FactStore, Letter, fold_letter, legacy_letter
from .leases import Lease, LeaseAuthority, StaleLease


@dataclass(frozen=True)
class Claim:
    letter: str
    lease: Lease


class LetterService:
    def __init__(self, facts: FactStore, leases: LeaseAuthority):
        self.facts = facts
        self.leases = leases

    def state(self, letter_id: str, *, now: float | None = None) -> Letter | None:
        return fold_letter(self.facts.read("letters", letter_id), now=now)

    def ingest(self, letter_id: str, status: str, *, by: str = "legacy",
               metadata: dict[str, Any] | None = None) -> Letter:
        """Read an old status once. No legacy event file is rewritten."""
        current = self.state(letter_id)
        if current is not None:
            return current
        legacy = legacy_letter(status)
        self.facts.record("letters", letter_id, "pending", by,
                          {"legacy_status": status, **(metadata or {})})
        if legacy.state != "pending":
            self.facts.record("letters", letter_id, legacy.state, by,
                              {"legacy_status": status})
        result = self.state(letter_id)
        assert result is not None
        return result

    def claim(self, letter_id: str, holder: str, ttl: float, *,
              now: float, capabilities: set[str] | None = None,
              required: set[str] | None = None) -> Claim | None:
        lease = self.leases.acquire("letter:" + letter_id, holder, ttl,
                                    capabilities=capabilities, required=required)
        if lease is None:
            return None
        state = self.state(letter_id, now=now)
        if state is None:
            self.facts.record("letters", letter_id, "pending", "door")
            state = self.state(letter_id, now=now)
        if state is None or state.state in {"answered", "retired"}:
            self.leases.release(lease)
            return None
        last_claimed = [f for f in self.facts.read("letters", letter_id)
                        if f.kind == "claimed"]
        if last_claimed and state.state == "pending":
            # A prior claim's lease lapsed. Retain it in history, then reopen
            # factually before recording the new generation.
            last = last_claimed[-1]
            if float(last.data.get("until", 0)) <= now:
                self.facts.record("letters", letter_id, "released", "lease",
                                  {"why": "expired", "gen": last.data.get("gen")})
        elif state.state == "claimed":
            if state.claim and state.claim.get("gen") == lease.gen:
                return Claim(letter_id, lease)
            self.leases.release(lease)
            return None
        self.facts.record("letters", letter_id, "claimed", holder,
                          {"run": holder, "gen": lease.gen, "until": lease.until})
        return Claim(letter_id, lease)

    def answer(self, claim: Claim, response: Any, *,
               send: Callable[[str, int], Any]) -> Any:
        if not self.leases.authorize(claim.lease):
            raise StaleLease(f"letter lease expired: {claim.letter}")
        current = self.state(claim.letter)
        if current is None or current.state != "claimed" or not current.claim:
            raise ValueError("letter is not claimed")
        if current.claim.get("gen") != claim.lease.gen:
            raise StaleLease(f"letter generation changed: {claim.letter}")
        receipt = self.leases.effect_once(claim.lease, "reply:" + claim.letter, send)
        self.facts.record("letters", claim.letter, "answered", claim.lease.holder,
                          {"gen": claim.lease.gen, "response": response,
                           "receipt": receipt})
        self.leases.release(claim.lease)
        return receipt

    def retire(self, claim: Claim, *, by: str, why: str) -> None:
        if not self.leases.authorize(claim.lease):
            raise StaleLease(f"letter lease expired: {claim.letter}")
        self.facts.record("letters", claim.letter, "retired", by,
                          {"gen": claim.lease.gen, "why": why})
        self.leases.release(claim.lease)

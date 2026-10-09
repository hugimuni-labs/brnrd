"""Fence the existing gate delivery callbacks with daemon2 send facts."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..gates import runtime as gate_runtime
from .facts import FactStore
from .leases import Lease, LocalLeaseAuthority, StaleLease


class GateTransport:
    def __init__(self, leases: LocalLeaseAuthority, facts: FactStore,
                 current_self: Callable[[], Lease | None]):
        self.leases = leases
        self.facts = facts
        self.current_self = current_self

    def _lease(self) -> Lease:
        lease = self.current_self()
        if lease is None or not self.leases.authorize(lease):
            raise StaleLease("account self lease is not held for delivery")
        return lease

    def send(self, key: str, event: dict, body: str,
             deliver: Callable[[dict, str], object]) -> object:
        lease = self._lease()
        return self.leases.effect_once(
            lease, "transport:" + key,
            lambda _key, _gen: deliver(event, body))

    def undeliverable(self, key: str, reason: str) -> None:
        lease = self._lease()
        entity = "transport:" + key
        if any(f.kind in {"sent", "undeliverable"}
               for f in self.facts.read("sends", entity)):
            return
        self.facts.record("sends", entity, "undeliverable", lease.holder,
                          {"key": key, "gen": lease.gen, "reason": reason})

    def install(self, inbox: Path, responses: Path) -> None:
        gate_runtime.set_delivery_hook(inbox, responses, self)

    def remove(self, inbox: Path, responses: Path) -> None:
        gate_runtime.set_delivery_hook(inbox, responses, None)

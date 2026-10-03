"""Conversation-keyed seat checkpoints and typed wake predicates.

Every state write is an atomic generation change. A killed runner resumes
from the last checkpoint; it is never asked to write a carry after death.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from .statecharts import load


@dataclass(frozen=True)
class Signal:
    kind: str
    conversation: str
    ask: str | None = None
    parent: str | None = None
    edge: str | None = None
    schedule: str | None = None
    pool: str | None = None
    remaining_pct: float | None = None
    measured_at: float | None = None
    authorized: bool = False
    control: str | None = None


@dataclass(frozen=True)
class WakePredicate:
    kind: str
    params: dict[str, Any] = field(default_factory=dict)

    def matches(self, signal: Signal, *, now: float, conversation: str) -> bool:
        if signal.conversation != conversation:
            return False
        p = self.params
        if self.kind == "M":
            return signal.kind == "mail" and (not p.get("ask") or p["ask"] == signal.ask)
        if self.kind == "S":
            return signal.kind == "schedule" and p.get("name") == signal.schedule
        if self.kind == "C":
            return (signal.kind == "child" and p.get("parent") == signal.parent
                    and p.get("edge") == signal.edge)
        if self.kind == "H":
            return signal.kind == "handover" and signal.authorized
        if self.kind == "R":
            return (signal.kind == "resource"
                    and p.get("pool") == signal.pool
                    and signal.remaining_pct is not None
                    and signal.remaining_pct >= float(p["floor"])
                    and signal.measured_at is not None
                    and 0 <= now - signal.measured_at <= float(p["freshness"]))
        if self.kind == "T":
            return signal.kind == "timer" and now >= float(p["deadline"])
        if self.kind == "U":
            return (signal.kind == "control" and signal.authorized
                    and (not p.get("actions") or signal.control in p["actions"]))
        raise ValueError(f"unknown wake predicate: {self.kind}")

    def row(self) -> dict[str, Any]:
        return {"kind": self.kind, "params": self.params}


def legacy_wake_on(reason: str, *, parent: str | None = None,
                   edges: tuple[str, ...] = (), pool: str | None = None,
                   floor: float | None = None, freshness: float = 120,
                   deadline: float | None = None,
                   schedules: tuple[str, ...] = ()) -> tuple[WakePredicate, ...]:
    """The old six holds mapped to addressed, serializable predicates."""
    mail = WakePredicate("M")
    handover = WakePredicate("H")
    control = WakePredicate("U")
    children = tuple(WakePredicate("C", {"parent": parent, "edge": edge})
                     for edge in edges)
    schedule = tuple(WakePredicate("S", {"name": name}) for name in schedules)
    if reason == "operator":
        return mail, handover, control
    if reason == "reset":
        return ((WakePredicate("T", {"deadline": deadline}),)
                if deadline is not None else ()) + (mail, handover, control)
    if reason == "refill":
        if pool is None or floor is None:
            raise ValueError("refill requires pool and floor")
        return (WakePredicate("R", {"pool": pool, "floor": floor,
                                    "freshness": freshness}), handover,
                WakePredicate("U", {"actions": ["force", "stop", "respawn"]}))
    if reason in {"strands", "any", "daemon_restarted"}:
        return children + schedule + (mail, handover, control)
    if reason == "raise":
        raise ValueError("held_raise is retired")
    raise ValueError(f"unknown legacy hold: {reason}")


@dataclass(frozen=True)
class SeatRecord:
    conversation: str
    state: str = "parked"
    generation: int = 0
    resume_generation: int = 0
    wake_on: tuple[WakePredicate, ...] = ()
    why: str = ""
    checkpoint: dict[str, Any] = field(default_factory=dict)
    native_session: dict[str, Any] | None = None
    queued_letters: tuple[str, ...] = ()
    obligations: tuple[str, ...] = ()
    carry: dict[str, Any] | None = None
    asks: tuple[str, ...] = ()

    def row(self) -> dict[str, Any]:
        return {**self.__dict__, "wake_on": [p.row() for p in self.wake_on],
                "queued_letters": list(self.queued_letters),
                "obligations": list(self.obligations), "asks": list(self.asks)}

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "SeatRecord":
        return cls(conversation=row["conversation"], state=row["state"],
                   generation=row["generation"],
                   resume_generation=row["resume_generation"],
                   wake_on=tuple(WakePredicate(**p) for p in row.get("wake_on", ())),
                   why=row.get("why", ""), checkpoint=row.get("checkpoint", {}),
                   native_session=row.get("native_session"),
                   queued_letters=tuple(row.get("queued_letters", ())),
                   obligations=tuple(row.get("obligations", ())),
                   carry=row.get("carry"), asks=tuple(row.get("asks", ())))


class StaleSeat(RuntimeError):
    pass


class SeatStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, conversation: str) -> Path:
        if not conversation:
            raise ValueError("conversation required")
        return self.root / (quote(conversation, safe="") + ".json")

    def read(self, conversation: str) -> SeatRecord:
        path = self._path(conversation)
        try:
            return SeatRecord.from_row(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return SeatRecord(conversation)

    def change(self, conversation: str, expected: int,
               update: Callable[[SeatRecord], SeatRecord]) -> SeatRecord:
        path = self._path(conversation)
        self.root.mkdir(parents=True, exist_ok=True)
        lock = path.with_suffix(".lock")
        with lock.open("a+b") as guard:
            fcntl.flock(guard, fcntl.LOCK_EX)
            try:
                current = self.read(conversation)
                if current.generation != expected:
                    raise StaleSeat(f"{conversation}: expected {expected}, got {current.generation}")
                updated = update(current)
                if updated.conversation != conversation:
                    raise ValueError("seat address changed")
                updated = replace(updated, generation=expected + 1)
                fd, name = tempfile.mkstemp(prefix=".seat-", dir=self.root)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as stream:
                        json.dump(updated.row(), stream, sort_keys=True)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(name, path)
                    dirfd = os.open(self.root, os.O_RDONLY)
                    try:
                        os.fsync(dirfd)
                    finally:
                        os.close(dirfd)
                finally:
                    if os.path.exists(name):
                        os.unlink(name)
                return updated
            finally:
                fcntl.flock(guard, fcntl.LOCK_UN)


@dataclass(frozen=True)
class Wake:
    record: SeatRecord
    mode: str
    session_id: str | None


class Seat:
    def __init__(self, store: SeatStore, conversation: str,
                 *, clock: Callable[[], float] = time.time):
        self.store = store
        self.conversation = conversation
        self.clock = clock
        self.machine = load("seat")

    def read(self) -> SeatRecord:
        return self.store.read(self.conversation)

    def _transition(self, expected: int, trigger: str, **changes: Any) -> SeatRecord:
        def update(current: SeatRecord) -> SeatRecord:
            state = self.machine.next(current.state, trigger)
            return replace(current, state=state, **changes)
        return self.store.change(self.conversation, expected, update)

    def start(self, expected: int) -> SeatRecord:
        return self._transition(expected, "dispatch")

    def await_signal(self, expected: int, predicates: tuple[WakePredicate, ...]) -> SeatRecord:
        return self._transition(expected, "await", wake_on=predicates)

    def signal(self, expected: int, signal: Signal) -> SeatRecord | None:
        current = self.read()
        if current.generation != expected:
            raise StaleSeat("stale await generation")
        if not any(p.matches(signal, now=self.clock(), conversation=self.conversation)
                   for p in current.wake_on):
            return None
        return self._transition(expected, "signal", wake_on=())

    def checkpoint(self, expected: int, *, data: dict[str, Any],
                   obligations: tuple[str, ...], native_session: dict[str, Any] | None,
                   queued_letters: tuple[str, ...] | None = None) -> SeatRecord:
        def update(current: SeatRecord) -> SeatRecord:
            if current.state not in {"running", "awaiting", "parked"}:
                raise ValueError("ended seat cannot checkpoint")
            return replace(current, checkpoint=data, obligations=obligations,
                           native_session=native_session,
                           queued_letters=(queued_letters if queued_letters is not None
                                           else current.queued_letters))
        return self.store.change(self.conversation, expected, update)

    def park(self, expected: int, *, why: str,
             wake_on: tuple[WakePredicate, ...]) -> SeatRecord:
        if not wake_on:
            raise ValueError("park requires a wake predicate")
        return self._transition(expected, "park", why=why, wake_on=wake_on)

    def handover(self, expected: int, *, carry: dict[str, Any],
                 wake_on: tuple[WakePredicate, ...]) -> SeatRecord:
        if not carry or not wake_on:
            raise ValueError("handover requires carry and wake predicates")
        return self._transition(expected, "handover", carry=carry,
                                why="handover", wake_on=wake_on)

    def queue_letter(self, expected: int, letter_id: str) -> SeatRecord:
        def update(current: SeatRecord) -> SeatRecord:
            if current.state == "ended":
                raise ValueError("ended seat cannot queue mail")
            return replace(current, queued_letters=tuple(dict.fromkeys(
                (*current.queued_letters, letter_id))))
        return self.store.change(self.conversation, expected, update)

    def wake(self, expected: int, signal: Signal, *,
             shell: str, capabilities: set[str]) -> Wake | None:
        current = self.read()
        if current.generation != expected:
            raise StaleSeat("stale resume generation")
        if current.state != "parked":
            raise ValueError("only a parked seat can wake")
        if not any(p.matches(signal, now=self.clock(), conversation=self.conversation)
                   for p in current.wake_on):
            return None
        native = current.native_session or {}
        valid = (native.get("shell") == shell and native.get("id")
                 and float(native.get("valid_until", 0)) > self.clock()
                 and set(native.get("valid_for", ())) <= capabilities)
        updated = self._transition(expected, "wake", wake_on=(),
                                   resume_generation=current.resume_generation + 1)
        return Wake(updated, "native" if valid else "checkpoint",
                    str(native["id"]) if valid else None)

    def end(self, expected: int) -> SeatRecord:
        return self._transition(expected, "end", wake_on=())

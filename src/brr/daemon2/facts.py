"""Versioned facts with an identity-based merge and deterministic projections.

The store deliberately has no mutable status file. A log is append-only;
replicas merge by fact id. Timestamps order independent facts, while the
caller supplies causality through explicit generation and transition data.
"""

from __future__ import annotations

import fcntl
import heapq
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote


VERSION = 1
LETTER_KINDS = frozenset({"pending", "claimed", "released", "answered", "retired"})
LEGACY_STATUS = {
    "pending": "pending", "processing": "claimed",
    "done": "answered", "delivered": "answered",
    "noted": "retired", "retired": "retired", "cancelled": "retired",
    "stopped": "retired", "error": "retired", "conflict": "retired",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Fact:
    kind: str
    by: str
    data: dict[str, Any] = field(default_factory=dict)
    at: str = field(default_factory=_now)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    v: int = VERSION
    hlc: tuple[int, int, str] | None = None
    after: tuple[str, ...] = ()

    def row(self) -> dict[str, Any]:
        return {"v": self.v, "id": self.id, "kind": self.kind,
                "by": self.by, "at": self.at, "data": self.data,
                "hlc": list(self.hlc) if self.hlc is not None else None,
                "after": list(self.after)}

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Fact":
        if row.get("v") != VERSION:
            raise ValueError(f"unsupported fact version: {row.get('v')!r}")
        if not isinstance(row.get("data"), dict):
            raise ValueError("fact data must be an object")
        hlc = row.get("hlc")
        if (hlc is not None and
                (not isinstance(hlc, list) or len(hlc) != 3 or
                 not isinstance(hlc[0], int) or not isinstance(hlc[1], int) or
                 not isinstance(hlc[2], str))):
            raise ValueError("invalid hybrid logical clock")
        after = row.get("after", [])
        if not isinstance(after, list) or any(not isinstance(x, str) for x in after):
            raise ValueError("invalid causal predecessors")
        return cls(kind=row["kind"], by=row["by"], data=row["data"],
                   at=row["at"], id=row["id"], v=row["v"],
                   hlc=tuple(hlc) if hlc is not None else None,
                   after=tuple(after))


def union(*streams: Iterable[Fact]) -> list[Fact]:
    """Commutative identity union with causal, then HLC, ordering."""
    by_id: dict[str, Fact] = {}
    for stream in streams:
        for fact in stream:
            old = by_id.setdefault(fact.id, fact)
            if old != fact:
                raise ValueError(f"conflicting fact id: {fact.id}")
    outgoing: dict[str, list[str]] = {ident: [] for ident in by_id}
    incoming: dict[str, int] = {ident: 0 for ident in by_id}
    for fact in by_id.values():
        for predecessor in fact.after:
            if predecessor in by_id:
                outgoing[predecessor].append(fact.id)
                incoming[fact.id] += 1
    def order(ident: str) -> tuple:
        fact = by_id[ident]
        # Legacy/shadow facts have no HLC and retain their timestamp order.
        return (fact.hlc or (int(datetime.fromisoformat(fact.at).timestamp() * 1000),
                             0, fact.by), fact.id)
    ready = [(order(ident), ident) for ident, count in incoming.items() if count == 0]
    heapq.heapify(ready)
    result = []
    while ready:
        _, ident = heapq.heappop(ready)
        result.append(by_id[ident])
        for successor in outgoing[ident]:
            incoming[successor] -= 1
            if incoming[successor] == 0:
                heapq.heappush(ready, (order(successor), successor))
    if len(result) != len(by_id):
        raise ValueError("causal fact cycle")
    return result


class FactStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def path(self, scope: str, entity: str) -> Path:
        if not scope or not entity or scope not in {"letters", "asks", "seats", "sends"}:
            raise ValueError("invalid fact address")
        return self.root / scope / (quote(entity, safe="") + ".jsonl")

    def read(self, scope: str, entity: str) -> list[Fact]:
        path = self.path(scope, entity)
        try:
            with path.open(encoding="utf-8") as stream:
                return union(Fact.from_row(json.loads(line)) for line in stream if line.strip())
        except FileNotFoundError:
            return []

    def append(self, scope: str, entity: str, fact: Fact) -> None:
        """One locked O_APPEND write, flushed before returning.

        A stable sibling lock avoids locking an inode that rename could replace.
        """
        if fact.v != VERSION or not fact.kind or not fact.by:
            raise ValueError("unsupported fact version")
        Fact.from_row(fact.row())
        path = self.path(scope, entity)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = path.with_suffix(".lock")
        with lock.open("a+b") as guard:
            fcntl.flock(guard, fcntl.LOCK_EX)
            try:
                existing = {item.id: item for item in self.read(scope, entity)}
                if fact.id in existing:
                    if existing[fact.id] != fact:
                        raise ValueError(f"conflicting fact id: {fact.id}")
                    return
                data = (json.dumps(fact.row(), sort_keys=True, separators=(",", ":")) + "\n").encode()
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                try:
                    if os.write(fd, data) != len(data):
                        raise OSError("short fact append")
                    os.fsync(fd)
                finally:
                    os.close(fd)
            finally:
                fcntl.flock(guard, fcntl.LOCK_UN)

    def merge(self, scope: str, entity: str, incoming: Iterable[Fact]) -> list[Fact]:
        for fact in union(incoming):
            self.append(scope, entity, fact)
        return self.read(scope, entity)

    def record(self, scope: str, entity: str, kind: str, by: str,
               data: dict[str, Any] | None = None, *,
               after: Iterable[str] = ()) -> Fact:
        """Tick a per-entity HLC after all locally observed predecessors.

        `after` records causality across replicas. The clock is held under
        the entity lock so local writers cannot choose the same tick.
        """
        path = self.path(scope, entity)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = path.with_suffix(".lock")
        with lock.open("a+b") as guard:
            fcntl.flock(guard, fcntl.LOCK_EX)
            try:
                seen = self.read(scope, entity)
                prior = max((f.hlc for f in seen if f.hlc is not None),
                            default=(0, 0, ""))
                physical = int(datetime.now(timezone.utc).timestamp() * 1000)
                tick = (max(physical, prior[0]),
                        prior[1] + 1 if physical <= prior[0] else 0, by)
                fact = Fact(kind, by, data or {}, hlc=tick,
                            after=tuple(after))
                data_bytes = (json.dumps(fact.row(), sort_keys=True,
                                         separators=(",", ":")) + "\n").encode()
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                try:
                    if os.write(fd, data_bytes) != len(data_bytes):
                        raise OSError("short fact append")
                    os.fsync(fd)
                finally:
                    os.close(fd)
                return fact
            finally:
                fcntl.flock(guard, fcntl.LOCK_UN)


@dataclass(frozen=True)
class Letter:
    state: str
    claim: dict[str, Any] | None = None
    answer: dict[str, Any] | None = None
    retirement: dict[str, Any] | None = None
    anomalies: tuple[str, ...] = ()


def fold_letter(facts: Iterable[Fact], *, now: float | None = None) -> Letter | None:
    """Project the letter state. Expiry makes a claim eligible, not answered."""
    state: Letter | None = None
    anomalies: list[str] = []
    for fact in union(facts):
        kind = fact.kind
        data = fact.data
        if kind not in LETTER_KINDS:
            anomalies.append(f"{fact.id}: unknown letter fact {kind}")
            continue
        if kind == "pending":
            if state is None:
                state = Letter("pending")
            elif state.state != "pending":
                anomalies.append(f"{fact.id}: pending after {state.state}")
        elif kind == "claimed":
            if state is None or state.state != "pending":
                anomalies.append(f"{fact.id}: claim without pending letter")
            else:
                state = Letter("claimed", claim=data)
        elif kind == "released":
            if state is None or state.state != "claimed":
                anomalies.append(f"{fact.id}: release without claim")
            else:
                state = Letter("pending")
        elif kind == "answered":
            if state is None or state.state not in {"pending", "claimed"}:
                anomalies.append(f"{fact.id}: answer after terminal or without letter")
            else:
                state = Letter("answered", answer=data)
        elif kind == "retired":
            if state is None or state.state not in {"pending", "claimed"}:
                anomalies.append(f"{fact.id}: retire after terminal or without letter")
            else:
                state = Letter("retired", retirement=data)
    if state is not None:
        state = Letter(state.state, state.claim, state.answer, state.retirement,
                       tuple(anomalies))
    if (state is not None and state.state == "claimed" and now is not None
            and state.claim is not None and float(state.claim.get("until", 0)) <= now):
        return Letter("pending", anomalies=tuple(anomalies))
    return state


def legacy_letter(status: str) -> Letter:
    """Read-only migration adapter for an old event's mutable status."""
    try:
        state = LEGACY_STATUS[status]
    except KeyError as exc:
        raise ValueError(f"unknown legacy letter status: {status}") from exc
    return Letter(state)

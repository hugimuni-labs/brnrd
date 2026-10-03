"""Versioned facts with an identity-based merge and deterministic projections.

The store deliberately has no mutable status file. A log is append-only;
replicas merge by fact id. Timestamps order independent facts, while the
caller supplies causality through explicit generation and transition data.
"""

from __future__ import annotations

import fcntl
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
    "noted": "retired", "cancelled": "retired",
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

    def row(self) -> dict[str, Any]:
        return {"v": self.v, "id": self.id, "kind": self.kind,
                "by": self.by, "at": self.at, "data": self.data}

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Fact":
        if row.get("v") != VERSION:
            raise ValueError(f"unsupported fact version: {row.get('v')!r}")
        if not isinstance(row.get("data"), dict):
            raise ValueError("fact data must be an object")
        return cls(kind=row["kind"], by=row["by"], data=row["data"],
                   at=row["at"], id=row["id"], v=row["v"])


def union(*streams: Iterable[Fact]) -> list[Fact]:
    """Commutative, idempotent merge; conflicting copies of an id fail closed."""
    by_id: dict[str, Fact] = {}
    for stream in streams:
        for fact in stream:
            old = by_id.setdefault(fact.id, fact)
            if old != fact:
                raise ValueError(f"conflicting fact id: {fact.id}")
    return sorted(by_id.values(), key=lambda f: (f.at, f.id))


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
        if fact.v != VERSION:
            raise ValueError("unsupported fact version")
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


@dataclass(frozen=True)
class Letter:
    state: str
    claim: dict[str, Any] | None = None
    answer: dict[str, Any] | None = None
    retirement: dict[str, Any] | None = None


def fold_letter(facts: Iterable[Fact], *, now: float | None = None) -> Letter | None:
    """Project the letter state. Expiry makes a claim eligible, not answered."""
    state: Letter | None = None
    for fact in union(facts):
        kind = fact.kind
        data = fact.data
        if kind not in LETTER_KINDS:
            raise ValueError(f"unknown letter fact: {kind}")
        if kind == "pending":
            state = Letter("pending")
        elif kind == "claimed":
            if state is None or state.state not in {"pending", "claimed"}:
                raise ValueError("claim without pending letter")
            state = Letter("claimed", claim=data)
        elif kind == "released":
            if state is None or state.state != "claimed":
                raise ValueError("release without claim")
            state = Letter("pending")
        elif kind == "answered":
            if state is None or state.state not in {"pending", "claimed"}:
                raise ValueError("answer without open letter")
            state = Letter("answered", answer=data)
        elif kind == "retired":
            if state is None or state.state not in {"pending", "claimed"}:
                raise ValueError("retire without open letter")
            state = Letter("retired", retirement=data)
    if (state is not None and state.state == "claimed" and now is not None
            and state.claim is not None and float(state.claim.get("until", 0)) <= now):
        return Letter("pending")
    return state


def legacy_letter(status: str) -> Letter:
    """Read-only migration adapter for an old event's mutable status."""
    try:
        state = LEGACY_STATUS[status]
    except KeyError as exc:
        raise ValueError(f"unknown legacy letter status: {status}") from exc
    return Letter(state)

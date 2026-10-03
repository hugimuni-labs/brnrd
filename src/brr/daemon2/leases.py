"""Local compare-and-set lease authority and fenced external effects.

The file implementation gives one machine a real authority. A cloud authority
must implement the same protocol and validate generations at the service
doing the effect; this module does not pretend a local lock is distributed.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol, TypeVar
from urllib.parse import quote

from .facts import FactStore


T = TypeVar("T")


@dataclass(frozen=True)
class Lease:
    what: str
    holder: str
    gen: int
    until: float
    capabilities: tuple[str, ...] = ()


class LeaseAuthority(Protocol):
    def acquire(self, what: str, holder: str, ttl: float, *,
                capabilities: set[str] | None = None,
                required: set[str] | None = None) -> Lease | None: ...
    def renew(self, lease: Lease, ttl: float) -> Lease | None: ...
    def authorize(self, lease: Lease) -> bool: ...
    def release(self, lease: Lease) -> bool: ...
    def effect_once(self, lease: Lease, key: str,
                    effect: Callable[[str, int], T]) -> T | Any: ...


class StaleLease(RuntimeError):
    pass


class EffectInFlight(RuntimeError):
    pass


class LocalLeaseAuthority:
    def __init__(self, root: Path, *, clock: Callable[[], float] = time.time):
        self.root = Path(root)
        self.clock = clock
        self.sends = FactStore(self.root / "facts")

    def _paths(self, what: str) -> tuple[Path, Path]:
        if not what or "/" in what or "\x00" in what:
            raise ValueError("invalid lease key")
        stem = quote(what, safe="")
        return self.root / (stem + ".json"), self.root / (stem + ".lock")

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"gen": 0, "holder": "", "until": 0, "effects": {}}

    @staticmethod
    def _write(path: Path, row: dict[str, Any]) -> None:
        fd, name = tempfile.mkstemp(prefix=".lease-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(row, stream, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
            dirfd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _locked(self, what: str, action: Callable[[Path, dict[str, Any]], T]) -> T:
        path, lock = self._paths(what)
        self.root.mkdir(parents=True, exist_ok=True)
        with lock.open("a+b") as guard:
            fcntl.flock(guard, fcntl.LOCK_EX)
            try:
                return action(path, self._read(path))
            finally:
                fcntl.flock(guard, fcntl.LOCK_UN)

    def acquire(self, what: str, holder: str, ttl: float, *,
                capabilities: set[str] | None = None,
                required: set[str] | None = None) -> Lease | None:
        if not holder or ttl <= 0:
            raise ValueError("holder and positive ttl required")
        offered = capabilities or set()
        if not (required or set()) <= offered:
            return None

        def action(path: Path, row: dict[str, Any]) -> Lease | None:
            now = self.clock()
            if row["until"] > now and row["holder"] != holder:
                return None
            if row["until"] > now and row["holder"] == holder:
                # A live holder renews without changing the generation.
                row["until"] = now + ttl
                if capabilities is not None:
                    row["capabilities"] = sorted(offered)
            else:
                row.update(holder=holder, gen=row["gen"] + 1,
                           until=now + ttl, capabilities=sorted(offered))
            self._write(path, row)
            return Lease(what, holder, row["gen"], row["until"],
                         tuple(row.get("capabilities", ())))

        return self._locked(what, action)

    def _valid(self, row: dict[str, Any], lease: Lease) -> bool:
        return (row["gen"] == lease.gen and row["holder"] == lease.holder
                and row["until"] > self.clock())

    def renew(self, lease: Lease, ttl: float) -> Lease | None:
        if ttl <= 0:
            raise ValueError("positive ttl required")

        def action(path: Path, row: dict[str, Any]) -> Lease | None:
            if not self._valid(row, lease):
                return None
            row["until"] = self.clock() + ttl
            self._write(path, row)
            return Lease(lease.what, lease.holder, lease.gen, row["until"],
                         tuple(row.get("capabilities", ())))

        return self._locked(lease.what, action)

    def authorize(self, lease: Lease) -> bool:
        return self._locked(lease.what, lambda _path, row: self._valid(row, lease))

    def release(self, lease: Lease) -> bool:
        def action(path: Path, row: dict[str, Any]) -> bool:
            if not self._valid(row, lease):
                return False
            row["until"] = 0
            self._write(path, row)
            return True

        return self._locked(lease.what, action)

    def effect_once(self, lease: Lease, key: str,
                    effect: Callable[[str, int], T]) -> T | Any:
        """Record intent, run outside the lock, then append a send receipt.

        The effect must pass `key` and `gen` to a remote endpoint that
        enforces idempotency. A process crash after the remote effect and
        before the local receipt cannot be solved by a local file alone.
        """
        if not key:
            raise ValueError("effect idempotency key required")
        if not self.authorize(lease):
            raise StaleLease(f"stale generation for {lease.what}")
        prior = [fact for fact in self.sends.read("sends", key) if fact.kind == "sent"]
        if prior:
            return prior[0].data["receipt"]
        # A separate per-send lease keeps concurrent local calls from
        # entering the effect together. A remote transport must still dedupe
        # the key if this process dies after the effect and before the receipt.
        send_lease = self.acquire("send:" + key, uuid.uuid4().hex, 300)
        if send_lease is None:
            raise EffectInFlight(key)
        try:
            prior = [fact for fact in self.sends.read("sends", key) if fact.kind == "sent"]
            if prior:
                return prior[0].data["receipt"]
            self.sends.record("sends", key, "intended", lease.holder,
                              {"key": key, "gen": lease.gen, "what": lease.what})
            if not self.authorize(lease):
                raise StaleLease(f"stale generation for {lease.what}")
            result = effect(key, lease.gen)
            json.dumps(result)  # a receipt must survive restart
            self.sends.record("sends", key, "sent", lease.holder,
                              {"key": key, "gen": lease.gen, "receipt": result})
            return result
        finally:
            self.release(send_lease)

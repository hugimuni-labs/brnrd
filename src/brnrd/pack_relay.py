"""In-memory, TTL-bounded relay for diffense review packs.

brnrd is a **transient relay** for packs, never a store
([`kb/design-diffense.md`](../../kb/design-diffense.md) → "Where packs
live"; [`kb/design-brnrd-protocol.md`](../../kb/design-brnrd-protocol.md)
→ data ownership). A pack is derived from the user's diff + conversation;
persisting it server-side would break that stance. So a relayed pack
lives only here — in process memory, behind an unguessable token, dropped
on TTL expiry or process restart. It is never written to the database or
to disk.

Single-process by construction. A horizontally-scaled deployment would
need a shared *ephemeral* store (e.g. Redis with a TTL) — but it must stay
non-durable to preserve the stance. A durable pack table is exactly what
this design refuses.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass


@dataclass
class _Entry:
    pack: dict
    expires_at: float


class PackRelayStore:
    """Token → pack, with per-entry expiry. Thread-safe, RAM-only."""

    def __init__(self, *, default_ttl_s: int = 3600) -> None:
        self._default_ttl_s = default_ttl_s
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def put(self, pack: dict, *, ttl_s: int | None = None) -> tuple[str, float]:
        """Stash *pack* behind a fresh token; return ``(token, expires_at)``."""
        ttl = ttl_s if ttl_s and ttl_s > 0 else self._default_ttl_s
        token = secrets.token_urlsafe(24)
        expires_at = time.time() + ttl
        with self._lock:
            self._sweep_locked()
            self._entries[token] = _Entry(pack=pack, expires_at=expires_at)
        return token, expires_at

    def get(self, token: str) -> dict | None:
        """Return the live pack for *token*, or ``None`` if absent/expired."""
        now = time.time()
        with self._lock:
            entry = self._entries.get(token)
            if entry is None:
                return None
            if entry.expires_at <= now:
                self._entries.pop(token, None)
                return None
            return entry.pack

    def _sweep_locked(self) -> None:
        now = time.time()
        dead = [t for t, e in self._entries.items() if e.expires_at <= now]
        for t in dead:
            self._entries.pop(t, None)

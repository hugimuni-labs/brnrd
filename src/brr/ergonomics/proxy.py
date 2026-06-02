"""Ergo proxies — where records cross from producer to reader.

The ``ErgoProxy`` Protocol is the producer-side seam: probe / telemetry
/ reflection layers write ``Record`` instances without caring how (or
whether) they reach an operator. Proxy choice is tenancy-driven, not
data-driven (see ``kb/design-agent-ergonomics.md`` → "The ergo proxy").

This slice ships two of the three designed proxies:

- ``NullErgoProxy`` — the default. Drops the record so the hot path
  stays free for users who never opt in.
- ``LocalErgoProxy`` — opt-in (``ergonomics.proxy=local``). Appends
  JSONL to ``.brr/ergonomics/<YYYY-MM-DD>.jsonl`` for ``brr
  ergonomics`` to read.

``BrnrdErgoProxy`` (batched HTTPS to brnrd) is a later slice gated on
the brnrd ergonomics endpoint; ``ergonomics.proxy=brnrd`` currently
falls back to ``NullErgoProxy`` rather than silently writing local.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .record import Record
from .store import ergonomics_dir


@runtime_checkable
class ErgoProxy(Protocol):
    def emit(self, record: Record) -> None: ...


class NullErgoProxy:
    """Drop every record. Default for self-hosted with no opt-in."""

    def emit(self, record: Record) -> None:  # noqa: D401 - trivial
        return None


class LocalErgoProxy:
    """Append records as JSONL under the shared ``.brr/ergonomics`` dir.

    One file per UTC day so the store rotates without a sweeper and
    ``brr ergonomics clear --before`` can drop whole days by filename.
    Appends are guarded by a process-local lock; cross-process writers
    (daemon + a concurrent CLI) rely on append-mode atomicity for
    single short lines, which is sufficient for this low-rate stream.
    """

    def __init__(self, store_dir: Path) -> None:
        self._dir = store_dir
        self._lock = threading.Lock()

    def emit(self, record: Record) -> None:
        day = _utc_day(record.timestamp)
        path = self._dir / f"{day}.jsonl"
        line = record.to_json_line()
        with self._lock:
            self._dir.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")


def _utc_day(timestamp: float) -> str:
    import time

    return time.strftime("%Y-%m-%d", time.gmtime(timestamp))


def get_proxy(cfg: dict[str, Any], brr_dir: Path) -> ErgoProxy:
    """Resolve the configured proxy for this daemon/repo.

    Default is ``null`` (self-hosted factory default — capture nothing,
    pollute nothing). ``local`` opts into the on-disk JSONL store.
    Unknown or not-yet-wired values (``brnrd``) degrade to ``null``.
    """
    choice = str(
        cfg.get("ergonomics.proxy", cfg.get("ergonomics_proxy", "null"))
    ).strip().lower()
    if choice == "local":
        return LocalErgoProxy(ergonomics_dir(brr_dir))
    return NullErgoProxy()

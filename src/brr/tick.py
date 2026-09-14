"""The frame's one beat: a numbered tick the daemon loop advances.

The loom's window is rebuilt from a focus every tick, never accumulated
(design-the-loom.md §4, §17 "the metronome"). Before this module the daemon
emitted three clocks and no beat: the portal's ``generated_at``, the
Shuttle's ``at``, the run ledger's ``at``. A :class:`Tick` is the beat those
rows can name.

One writer: the daemon's main loop calls :func:`advance` once per iteration.
Everything else reads :func:`current`. The counter is persisted at
``<account_home>/tick.json`` so ``n`` survives a dev-reload re-exec and a
restart, and never repeats. ``mono`` is ``time.monotonic()`` in the process
that minted the tick — comparable only between ticks from the same boot of
the machine, carried for spacing, never for ordering (``n`` orders).
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_TICK_PATH = Path("tick.json")


def _stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass(frozen=True)
class Tick:
    n: int
    at: str
    mono: float

    @classmethod
    def next(cls, previous: "Tick | None") -> "Tick":
        """The tick after *previous* (the first tick is ``n=1``)."""
        return cls(
            n=(previous.n + 1) if previous is not None else 1,
            at=_stamp(),
            mono=time.monotonic(),
        )

    def as_dict(self) -> dict[str, Any]:
        return {"n": self.n, "at": self.at, "mono": self.mono}


# The loop is the only writer, and it runs in this process: the last tick it
# advanced is served from memory so a boundary-path reader (the portal writer
# runs at every tool boundary) pays no file read. A reader in another process
# (a CLI verb) falls through to the file.
_lock = threading.Lock()
_last: Tick | None = None
_last_home: Path | None = None


def path_for(account_home: Path) -> Path:
    return account_home / _TICK_PATH


def load(account_home: Path) -> Tick | None:
    """The persisted tick under *account_home*, or ``None`` before the first.

    A record that exists but does not parse raises ``ValueError`` — the same
    stance ``Shuttle.load`` takes: restarting the count silently would repeat
    ``n``, which is the one thing a beat may not do.
    """
    path = path_for(account_home)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Tick(n=int(raw["n"]), at=str(raw["at"]), mono=float(raw["mono"]))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"invalid tick record at {path}: {exc}") from exc


def save(account_home: Path, tick: Tick) -> None:
    """Atomically persist *tick* (temp file, fsync, ``os.replace``)."""
    account_home.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tick-", suffix=".tmp", dir=account_home)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(tick.as_dict(), handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path_for(account_home))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def advance(account_home: Path) -> Tick:
    """Mint, persist and return the next tick. The loop's call, nobody else's.

    Continues from the in-memory tick when it belongs to this home, otherwise
    from the persisted record (the first iteration after a re-exec).
    """
    global _last, _last_home
    home = Path(account_home)
    with _lock:
        previous = _last if _last_home == home else load(home)
        tick = Tick.next(previous)
        # Memory moves first: if the write fails the beat still never repeats
        # inside this process, and the next successful save carries it.
        _last, _last_home = tick, home
        save(home, tick)
        return tick


def current(account_home: Path | None = None) -> Tick | None:
    """The latest tick, or ``None`` when no loop has advanced one.

    ``account_home`` omitted ⇒ the tick this process's loop last advanced
    (the daemon's worker threads share it). Given ⇒ that home's tick: from
    memory when this process advanced it, otherwise read from disk. A
    malformed record reads as ``None`` here — a stamp must never sink the
    row it rides; :func:`advance` is where malformation is loud.
    """
    if account_home is None:
        return _last
    home = Path(account_home)
    if _last is not None and _last_home == home:
        return _last
    try:
        return load(home)
    except ValueError:
        return None


def current_n(account_home: Path | None = None) -> int | None:
    """``current(account_home).n``, or ``None`` — the shape a row stamps."""
    tick = current(account_home)
    return tick.n if tick is not None else None


def _reset_for_tests() -> None:
    global _last, _last_home
    with _lock:
        _last, _last_home = None, None

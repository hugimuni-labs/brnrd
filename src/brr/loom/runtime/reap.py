"""``Reaper``: rooms and loom files nothing needs any more, removed with a fact each.

A room goes only when the ledger and git both show its work is held
elsewhere: its last gen released and drained at least ``ROOM_AGE`` ago, no
body holding ``room/.body``, nothing in ``port/out``, no live lease naming it
``parent``, and ``leftovers`` empty (self commits on home self's main, repo
commits on their declared source, no dirty paths). A room failing any check
is kept, and the reason is logged. ``self.broken-*`` asides go ``BROKEN_AGE``
after the ``room.repaired`` that made them, ``.self-clone-*`` once their pid is
dead. Each removal writes ``room.reaped`` first, with the bytes and branch
heads it held; a path still on disk under such a fact is finished next pass.
``loom.log`` rotates at ``LOG_MAX`` to three generations. Below the disk floor
``disk`` raises, so the guard opens a degradation, and the pass runs at once.
The pass runs off the loop: ``leftovers`` fetches into every room it reads.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

EVERY = 3600
ROOM_AGE = 7 * 86400
BROKEN_AGE = 14 * 86400
LOG_MAX = 20 * 1024 * 1024
LOG_KEEP = 3
FLOOR_BYTES = 5 * 1024 ** 3
FLOOR_SHARE = 0.05


class DiskFloor(Exception):
    pass


def _seconds(at: str) -> float:
    return datetime.fromisoformat(str(at).replace("Z", "+00:00")).timestamp()


def size(path: Path) -> int:
    total = 0
    for root, dirs, files in os.walk(path):
        for name in files + dirs:
            try:
                total += os.lstat(os.path.join(root, name)).st_blocks * 512
            except OSError:
                pass
    return total


def heads(path: Path) -> dict[str, dict[str, str]]:
    """Every checkout's branch tips under ``path``, so the fact says what was there."""
    from .selfrepo import git
    found = {}
    for checkout in [path, *sorted(p for p in path.iterdir() if p.is_dir())]:
        if not (checkout / ".git").exists():
            continue
        out = git(checkout, "for-each-ref", "--format=%(refname:short) %(objectname)",
                  "refs/heads", check=False).stdout
        found[str(checkout.relative_to(path)) or "."] = dict(
            line.split(" ", 1) for line in out.splitlines() if " " in line)
    return found


def floor(path: Path) -> tuple[int, int]:
    """``(free, floor)`` in bytes for the volume holding ``path``."""
    usage = shutil.disk_usage(path)
    return usage.free, max(FLOOR_BYTES, int(usage.total * FLOOR_SHARE))


def room_kept(home, facts, strand: str, now: float) -> str:
    """Why ``rooms/<strand>`` stays, or empty when it may go. Cheapest checks first."""
    from .loom import body_alive
    from .project import fold
    room = home.room(strand)
    leases = [f for f in facts if f.kind == "lease" and f.data.get("strand") == strand]
    if not leases:
        return "no lease names it"
    gen = max(int(f.data.get("gen") or 0) for f in leases)
    if any(s == strand for s, _ in fold(facts).holder.values()):
        return "it holds its thread"
    released = next((f for f in facts if f.kind == "released" and f.data.get("strand") == strand
                     and int(f.data.get("gen") or 0) == gen), None)
    if released is None:
        return f"gen {gen} is not released"
    if not any(f.kind == "port.drained" and f.data.get("strand") == strand
               and int(f.data.get("gen") or 0) == gen for f in facts):
        return f"gen {gen}'s port is not drained"
    if now - _seconds(released.at) < ROOM_AGE:
        return "released less than 7 days ago"
    if body_alive(room):
        return "a body holds it"
    out = room / "port" / "out"
    if out.is_dir() and any(out.iterdir()):
        return "port/out is not empty"
    done = {(str(f.data.get("strand")), int(f.data.get("gen") or 0))
            for f in facts if f.kind == "released"}
    for lease in facts:
        if lease.kind != "lease" or lease.data.get("parent") != strand:
            continue
        child = str(lease.data.get("strand"))
        last = max(int(f.data.get("gen") or 0) for f in facts
                   if f.kind == "lease" and f.data.get("strand") == child)
        if (child, last) not in done:
            return f"{child} is its live child"
    from .leftovers import leftovers
    left = leftovers(home, strand)
    if left:
        item = left[0]
        why = item.error or f"{len(item.commits)} commits, {len(item.edits)} edits"
        return f"leftovers in {item.path.name}: {why}"
    return ""


class Reaper:
    def __init__(self, home, log):
        self.home = home
        self.log = log
        self.next = 0.0
        self.below = False
        self.worker: threading.Thread | None = None
        self.failing: tuple[Exception, object] | None = None

    def disk(self) -> None:
        free, least = floor(self.home.root)
        if free < least and not self.below:
            self.next = 0.0  # Once per streak: the pass is not a tick-rate loop.
        self.below = free < least
        if self.below:
            raise DiskFloor(f"{free >> 20} MB free on the home's volume, under the floor of {least >> 20} MB")

    def step(self) -> None:
        """Until a pass succeeds again, raise the last pass's failure for the loop's guard."""
        if not (self.worker and self.worker.is_alive()) and time.monotonic() >= self.next:
            self.next = time.monotonic() + EVERY
            self.worker = threading.Thread(target=self._pass, name="loom-reap", daemon=True)
            self.worker.start()
        if self.failing is not None:
            raise self.failing[0].with_traceback(self.failing[1])

    def _pass(self) -> None:
        try:
            self.rotate()
            self.reap()
            self.failing = None
        except Exception as exc:
            self.failing = (exc, exc.__traceback__)

    def rotate(self) -> None:
        log = self.home.root / "loom" / "loom.log"
        try:
            if log.stat().st_size < LOG_MAX:
                return
        except FileNotFoundError:
            return
        for n in range(LOG_KEEP - 1, 0, -1):
            older = log.with_name(f"loom.log.{n}")
            if older.exists():
                os.replace(older, log.with_name(f"loom.log.{n + 1}"))
        os.replace(log, log.with_name("loom.log.1"))

    def reap(self) -> None:
        from .ledger import read_facts
        from .selfrepo import _pid_alive
        home = self.home
        rooms = home.root / "rooms"
        if not rooms.is_dir():
            return
        facts = read_facts(home)
        now = time.time()
        reaped = {str(f.data.get("path")) for f in facts if f.kind == "room.reaped"}
        asides = {Path(str(f.data["aside"])).resolve(): f.at for f in facts
                  if f.kind == "room.repaired" and f.data.get("aside")}
        key = self._key
        gone, kept = 0, {}
        for room in sorted(rooms.iterdir()):
            if room.name.startswith(".") or not room.is_dir():
                continue
            if key(room) in reaped:
                shutil.rmtree(room, ignore_errors=True)
                continue
            try:
                why = room_kept(home, facts, room.name, now)
            except Exception as exc:
                why = f"inspection failed: {type(exc).__name__}: {exc}"
            if not why:
                self._remove(room, "room", room.name)
                gone += 1
                continue
            kept[why.split(":")[0]] = kept.get(why.split(":")[0], 0) + 1
            for aside in sorted(room.glob("self.broken-*")):
                at = asides.get(aside.resolve())
                if key(aside) in reaped or (at and now - _seconds(at) >= BROKEN_AGE):
                    self._remove(aside, "self.broken", room.name, done=key(aside) in reaped)
            for clone in sorted(room.glob(".self-clone-*")):
                pid = clone.name.split("-")[2]
                if not (pid.isdigit() and _pid_alive(int(pid))):
                    self._remove(clone, "self-clone", room.name, done=key(clone) in reaped)
        self.log(home, f"reap: {gone} rooms reaped; kept " + (
            ", ".join(f"{n} ({why})" for why, n in sorted(kept.items())) or "none"))

    def _key(self, path: Path) -> str:
        return str(path.resolve().relative_to(self.home.root.resolve()))

    def _remove(self, path: Path, what: str, strand: str, done: bool = False) -> None:
        if not done:
            from brr.daemon2.facts import Fact
            from .ledger import append
            install = self.home.install_id()
            # The fact first: a crash mid-delete leaves a path the next pass finishes.
            append(self.home, Fact(kind="room.reaped", by=f"loom:{install}", id=f"room.reaped:{self._key(path)}",
                                   data={"what": what, "strand": strand, "path": self._key(path),
                                         "bytes": size(path), "heads": heads(path)}))
            self.log(self.home, f"reaped {what} {path}")
        shutil.rmtree(path, ignore_errors=True)

"""``Reaper``: rooms and loom files nothing needs any more, removed with a fact each.

A room goes only when the ledger and git both show its work is held
elsewhere: its last gen released and drained at least ``ROOM_AGE`` ago, no
body holding ``room/.body``, nothing in ``port/out``, no live lease naming it
``parent``, nothing at its root but plumbing and checkouts, and ``leftovers`` empty (self commits on home self's main, repo
commits on their declared source, no dirty paths). A room failing any check
is kept, and the reason is logged. ``self.broken-*`` asides go ``BROKEN_AGE``
after the ``room.repaired`` that made them, ``.self-clone-*`` once their pid is
dead. Each removal writes ``room.reaped`` first, with the bytes and branch
heads it held; a path still on disk under such a fact is finished next pass.
Before any removal, a clone elsewhere under ``rooms/`` that borrows objects
from the path through ``objects/info/alternates`` (a ``git clone --shared``)
is repacked to hold its own and its line dropped (``unlend``); a borrower
that cannot be keeps the path.
A kept room still loses each code clone (a checkout other than self) once
``clone_kept`` finds no reason: one ``clone:<strand>/<name>`` converge want
each, ``CLONE_AGE`` after release; self and port stay until the room goes.
A strand whose thread is settled (closed on main, no open wish) skips both
ages and the latest-strand keep: its room goes once released and drained, and
a wish that reopens the thread wakes it into a fresh room.
``loom.log`` rotates at ``LOG_MAX`` to three generations. Free space counts
what macOS purges on demand (``important``); below the disk floor
``disk`` raises, so the guard opens a degradation, and the pass runs at once;
a pass that ends still under the floor prunes the package caches (``PRUNE``)
and writes ``loom.pruned`` with the bytes it freed, which the gauge shows.
The pass runs off the loop: ``leftovers`` fetches into every room it reads.

Loose files and leftovers are work a body left: ``left`` lists them for the
jack, which shows them at every boundary and blocks the stop on them. A room
kept for them anyway goes to inbox in one notice per new room, and its thread
reads the reason at wake (``KEPT``). ``reap <strand> --force --why`` clears one
a body has settled; every other reason still keeps it.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

EVERY = 3600
ROOM_AGE = 7 * 86400
CLONE_AGE = 2 * 86400
BROKEN_AGE = 14 * 86400
LOG_MAX = 20 * 1024 * 1024
LOG_KEEP = 3
FLOOR_BYTES = 5 * 1024 ** 3
# What the loom and the harness put at a room's root; checkouts are read by leftovers.
# lib/ holds links into a person's own folders; nothing here follows a link.
PLUMBING = {"self", "port", ".body", ".starting", ".claude", ".pytest-tmp", "delegates", "lib", "tmp"}
# Scratch dirs a body leaves at the root that nobody settles: loose, but never a reason to keep.
DISPOSABLE = {"shots", "runs", "cache-probes", "scratch"}
# Reasons only a body can settle; the rest are the loom's or time's.
BODY_REASONS = ("loose files", "leftovers in", "a stash in")
KEPT = "loom/reap-kept.json"
# Caches a tool rebuilds on its next install; each runs only where its binary is on PATH.
PRUNE = (("uv", "cache", "prune"), ("npm", "cache", "verify"), ("pip", "cache", "purge"))
PRUNE_TIMEOUT = 600
REPACK_TIMEOUT = 900


class DiskFloor(Exception):
    """Free space under the floor; ``grade`` counts the halvings of the floor it is below."""

    def __init__(self, free: int, least: int, plain: int | None = None, swap: str = ""):
        held = f" ({plain >> 20} MB before macOS purges)" if plain is not None and plain != free else ""
        super().__init__(f"{free >> 20} MB free on the home's volume{held}, under the floor of {least >> 20} MB{swap}")
        self.grade, mark = 0, least // 2
        while free < mark and self.grade < 8:
            self.grade, mark = self.grade + 1, mark // 2


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
    for checkout in [path, *sorted(p for p in path.iterdir() if p.is_dir() and not p.is_symlink())]:
        if not (checkout / ".git").exists():
            continue
        out = git(checkout, "for-each-ref", "--format=%(refname:short) %(objectname)",
                  "refs/heads", check=False).stdout
        found[str(checkout.relative_to(path)) or "."] = dict(
            line.split(" ", 1) for line in out.splitlines() if " " in line)
    return found


def important(path: Path) -> int | None:
    """Bytes macOS frees for an important write at ``path``, purgeable space (local snapshots, caches) counted; None off macOS."""
    if sys.platform != "darwin":
        return None
    try:
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        cf.CFURLCreateFromFileSystemRepresentation.restype = ctypes.c_void_p
        cf.CFURLCreateFromFileSystemRepresentation.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_bool]
        cf.CFURLCopyResourcePropertyForKey.restype = ctypes.c_bool
        cf.CFURLCopyResourcePropertyForKey.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
        cf.CFNumberGetValue.restype = ctypes.c_bool
        cf.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        key = ctypes.c_void_p.in_dll(cf, "kCFURLVolumeAvailableCapacityForImportantUsageKey")
        raw = os.fsencode(path)
        url = cf.CFURLCreateFromFileSystemRepresentation(None, raw, len(raw), True)
        if not url:
            return None
        try:
            value, out = ctypes.c_void_p(), ctypes.c_int64()
            if not cf.CFURLCopyResourcePropertyForKey(url, key, ctypes.byref(value), None) or not value.value:
                return None
            try:
                return out.value if cf.CFNumberGetValue(value, 4, ctypes.byref(out)) and out.value > 0 else None  # 4: kCFNumberSInt64Type
            finally:
                cf.CFRelease(value)
        finally:
            cf.CFRelease(url)
    except (OSError, AttributeError, ValueError):
        return None


def space(path: Path) -> tuple[int, int, int]:
    """``(free, plain, total)``: ``free`` counts what macOS purges on demand, ``plain`` is statvfs's free."""
    usage = shutil.disk_usage(path)
    return max(usage.free, important(path) or 0), usage.free, usage.total


SWAP_SHARE = 0.25


def _run(*command: str) -> str:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def swap(path: Path) -> str:
    """On darwin, the disk the swapfiles hold, and when that is a quarter of what is in use, the
    process with the largest footprint. Only named: a person's process is theirs to stop.

    ``vm.swapusage``'s total is the swapfiles' bytes; the VM volume's statvfs reads the whole
    APFS container, so it cannot say."""
    if sys.platform != "darwin":
        return ""
    found = re.search(r"total = ([\d.]+)M", _run("sysctl", "-n", "vm.swapusage"))
    if not found:
        return ""
    held = int(float(found.group(1)) * 2 ** 20)
    note = f"; swap holds {held >> 20} MB"
    usage = shutil.disk_usage(path)
    if held <= SWAP_SHARE * (usage.total - usage.free):
        return note
    rows = [line.split() for line in _run("top", "-l", "1", "-o", "mem", "-n", "1",
                                          "-stats", "pid,command,mem").splitlines() if line.strip()]
    if not rows or len(rows[-1]) < 3 or not rows[-1][0].isdigit():
        return note
    pid, *command, mem = rows[-1]
    return note + f", the largest footprint is pid {pid} {' '.join(command)} at {mem}"


def floor(path: Path) -> tuple[int, int]:
    """``(free, floor)`` in bytes for the volume holding the home at ``path``; the share is ``disk_floor_pct``.

    Free counts purgeable space: a local snapshot pins deleted blocks that statvfs reports as used,
    and macOS hands them back the moment a write needs them."""
    from .config import load_config
    free, _plain, total = space(path)
    return free, max(FLOOR_BYTES, int(total * load_config(path).disk_floor_pct / 100))


def loose(room: Path) -> list[str]:
    """Root entries that are neither plumbing nor a checkout: leftovers reads checkouts only."""
    return sorted(p.name for p in room.iterdir() if p.name not in PLUMBING
                  and not p.name.startswith(("self.broken-", ".self-clone-"))
                  and not (p.name in DISPOSABLE and p.is_dir() and not p.is_symlink())
                  and not (p / ".git").exists())


def left(home, strand: str) -> list[str]:
    """What only ``rooms/<strand>`` holds, one item a line, each with the command that clears it."""
    from .leftovers import leftovers
    room = home.room(strand)
    if not room.is_dir():
        return []
    run = "python -m brr.loom.runtime"
    lines = [f"{name}: a loose file at the room's root; move it into the self, or discard it:"
             f" `{run} reap --discard '{name}' --why \"<why>\"`" for name in loose(room)]
    for item in leftovers(home, strand):
        where, at = item.path.name, item.path
        # A checkout leftovers could not inspect has no command that clears it, so it
        # would hold the stop for good; room_kept still keeps the room for it.
        if item.commits:
            shown = ", ".join(f"{sha[:8]} {subject!r}" for sha, subject in item.commits[:3])
            more = ", ..." if len(item.commits) > 3 else ""
            land = (f"cd '{at}' && {run} send-self --room ." if where == "self"
                    else f"{run} publish {where} --room '{room}'")
            whose = "main" if where == "self" else "its source"
            lines.append(f"{where}: {len(item.commits)} commits on no branch of {whose} ({shown}{more});"
                         f" land them: `{land}`")
        if item.edits:
            shown = ", ".join(item.edits[:3]) + (", ..." if len(item.edits) > 3 else "")
            lines.append(f"{where}: {len(item.edits)} paths not committed ({shown}); commit them:"
                         f" `git -C '{at}' add -A && git -C '{at}' commit -m \"<what>\"`, or discard them:"
                         f" `git -C '{at}' stash push -u -m \"<why>\"`")
    return lines


def settled_threads(home, facts) -> set[str]:
    """Threads closed on main that hold no open wish: no wake is owed to their rooms."""
    from .lifecycle import CLOSED, main_snapshot
    from .wishes import wishes
    held = {w.thread for w in wishes(facts).values() if w.open}
    return {thread for thread, data in main_snapshot(home).items()
            if data.get("status") in CLOSED and thread not in held}


def _thread(facts, strand: str) -> str:
    return next((str(f.data.get("thread")) for f in reversed(facts)
                 if f.kind == "lease" and f.data.get("strand") == strand), "")


def holders(facts) -> set[str]:
    """Strands that hold a thread: one fold of the ledger, shared by a pass's rooms."""
    from .project import fold
    return {strand for strand, _ in fold(facts).holder.values()}


def room_kept(home, facts, strand: str, now: float, force: bool = False,
              settled: set[str] = frozenset(), holding: set[str] | None = None) -> str:
    """Why ``rooms/<strand>`` stays, or empty when it may go. Cheapest checks first.

    ``force`` is a body's word that the room's work is settled: age, loose
    files and leftovers no longer keep it. ``holding`` is ``holders(facts)``,
    passed in by a pass over every room: the fold reads the whole ledger.
    """
    from .loom import body_alive
    room = home.room(strand)
    leases = [f for f in facts if f.kind == "lease" and f.data.get("strand") == strand]
    if not leases:
        return "no lease names it"
    gen = max(int(f.data.get("gen") or 0) for f in leases)
    if strand in (holders(facts) if holding is None else holding):
        return "it holds its thread"
    released = next((f for f in facts if f.kind == "released" and f.data.get("strand") == strand
                     and int(f.data.get("gen") or 0) == gen), None)
    if released is None:
        return f"gen {gen} is not released"
    if not any(f.kind == "port.drained" and f.data.get("strand") == strand
               and int(f.data.get("gen") or 0) == gen for f in facts):
        return f"gen {gen}'s port is not drained"
    if not force and _thread(facts, strand) not in settled and now - _seconds(released.at) < ROOM_AGE:
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
    if force:
        return ""
    found = loose(room)
    if found:
        return "loose files: " + ", ".join(found[:5]) + (", ..." if len(found) > 5 else "")
    from .leftovers import leftovers
    left = leftovers(home, strand)
    if left:
        item = left[0]
        why = item.error or f"{len(item.commits)} commits, {len(item.edits)} edits"
        return f"leftovers in {item.path.name}: {why}"
    return ""


def busy(rooms: Path) -> set[str]:
    """Rooms some process works in: its cwd, an open file or its command line under the room.

    One ``lsof`` and one ``ps`` for the whole pass. A failed probe raises: an
    unread machine is never read as idle.
    """
    prefix = str(rooms.resolve()) + os.sep
    plain = str(rooms) + os.sep
    out = subprocess.run(["lsof", "-n", "-w", "-Fn"], capture_output=True, text=True, timeout=300).stdout
    if not out:
        raise OSError("lsof printed nothing")
    out += subprocess.run(["ps", "-axww", "-o", "command="], capture_output=True, text=True,
                          check=True, timeout=60).stdout
    return {match for base in {prefix, plain}
            for match in re.findall(re.escape(base) + r"([^/\s]+)", out)}


def latest_strands(facts) -> set[str]:
    """Each thread's latest leased strand: its next wake lands in that room."""
    latest = {}
    for fact in facts:
        if fact.kind == "lease" and fact.data.get("thread"):
            latest[str(fact.data["thread"])] = str(fact.data.get("strand"))
    return set(latest.values())


def clone_kept(home, facts, strand: str, name: str, now: float, working: set[str],
               latest: set[str], settled: set[str] = frozenset()) -> str:
    """Why ``rooms/<strand>/<name>`` stays, or empty when the clone may go. Cheapest first."""
    from .loom import body_alive
    room = home.room(strand)
    checkout = room / name
    if strand in working:
        return "a process works in the room"
    if body_alive(room):
        return "a body holds it"
    done = _thread(facts, strand) in settled
    if strand in latest and not done:
        return "it is its thread's latest strand"
    leases = [f for f in facts if f.kind == "lease" and f.data.get("strand") == strand]
    if not leases:
        return "no lease names it"
    gen = max(int(f.data.get("gen") or 0) for f in leases)
    released = next((f for f in facts if f.kind == "released" and f.data.get("strand") == strand
                     and int(f.data.get("gen") or 0) == gen), None)
    if released is None:
        return f"gen {gen} is not released"
    if not done and now - _seconds(released.at) < CLONE_AGE:
        return "released less than 48 h ago"
    if (checkout / ".git").is_file():
        return "a linked worktree"
    from .selfrepo import git
    listed = git(checkout, "worktree", "list", "--porcelain").stdout
    linked = [line[len("worktree "):] for line in listed.splitlines() if line.startswith("worktree ")][1:]
    if any(Path(path).exists() for path in linked):
        return "linked worktrees: " + ", ".join(linked[:3])
    if git(checkout, "stash", "list").stdout.strip():
        return f"a stash in {name}"
    from .leftovers import leftovers
    left = leftovers(home, strand, only=name)
    if left:
        item = left[0]
        return f"leftovers in {name}: " + (item.error or f"{len(item.commits)} commits, {len(item.edits)} edits")
    return ""


def borrowers(home, path: Path) -> list[tuple[Path, list[str]]]:
    """Clones under ``rooms/`` outside ``path`` whose alternates name an object store inside it.

    Each with the alternates lines that do. A line may be relative to the
    borrower's ``objects`` dir; a ``.brr/worktrees`` run clone counts too.
    """
    inside = path.resolve()
    found = []
    rooms = home.root / "rooms"
    for alternates in sorted([*rooms.glob("*/*/.git/objects/info/alternates"),
                              *rooms.glob("*/*/.brr/worktrees/*/.git/objects/info/alternates")]):
        repo = alternates.parents[3]
        if repo.resolve().is_relative_to(inside):
            continue
        try:
            lines = alternates.read_text().splitlines()
        except OSError:
            continue
        lent = [line for line in lines if line.strip() and not line.startswith("#")
                and (alternates.parents[1] / line.strip()).resolve().is_relative_to(inside)]
        if lent:
            found.append((repo, lent))
    return found


def unlend(home, path: Path) -> list[Path]:
    """Make every borrower of ``path`` hold its own objects, so removing ``path`` breaks none.

    ``git repack -a -d`` without ``-l`` copies what the borrower reaches
    through its alternates into its own pack; the lent lines go only after
    the borrower's every ref still resolves without them. Idempotent: a crash
    between the two steps repacks again next pass. Raises when one cannot.
    """
    done = []
    for repo, lent in borrowers(home, path):
        alternates = repo / ".git" / "objects" / "info" / "alternates"
        repack = subprocess.run(["git", "repack", "-a", "-d", "-q"], cwd=repo, capture_output=True,
                                text=True, timeout=REPACK_TIMEOUT)
        if repack.returncode != 0:
            raise RuntimeError(f"repack of borrower {repo} failed: {repack.stderr.strip()}")
        before = alternates.read_text()
        kept = [line for line in before.splitlines() if line not in lent]
        if kept:
            alternates.write_text("\n".join(kept) + "\n")
        else:
            alternates.unlink()
        check = subprocess.run(["git", "rev-list", "--objects", "--all", "--quiet"], cwd=repo,
                               capture_output=True, text=True, timeout=REPACK_TIMEOUT)
        if check.returncode != 0:
            alternates.write_text(before)
            raise RuntimeError(f"borrower {repo} still needs {path}: {check.stderr.strip()[:200]}")
        done.append(repo)
    return done


def clones(room: Path) -> list[Path]:
    return sorted(p for p in room.iterdir() if p.name not in PLUMBING and not p.is_symlink()
                  and (p / ".git").exists())


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
            raise DiskFloor(free, least, space(self.home.root)[1], swap(self.home.root))

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
            free, least = floor(self.home.root)
            if free < least:   # measured after the reap: a reap that cleared the floor prunes nothing
                self.prune()
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

    def prune(self) -> None:
        """Still under the floor after the reap: prune the package caches, one fact for what freed."""
        ran, before = [], floor(self.home.root)[0]
        for command in PRUNE:
            if not shutil.which(command[0]):
                continue
            try:
                subprocess.run(command, capture_output=True, timeout=PRUNE_TIMEOUT, check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                self.log(self.home, f"prune: {' '.join(command)} failed: {exc}")
                continue
            ran.append(" ".join(command))
        if not ran:
            return
        from brr.daemon2.facts import Fact
        from .ledger import append
        freed = max(0, floor(self.home.root)[0] - before)
        install = self.home.install_id()
        append(self.home, Fact(kind="loom.pruned", by=f"loom:{install}",
                               at=datetime.fromtimestamp(time.time(), timezone.utc).isoformat(),
                               data={"install": install, "ran": ran, "bytes": freed}))
        self.log(self.home, f"prune: {', '.join(ran)} freed {freed / 1024 ** 3:.2f} GB")

    def reap(self) -> None:
        from .ledger import read_facts
        from .selfrepo import _pid_alive
        home = self.home
        rooms = home.root / "rooms"
        if not rooms.is_dir():
            return
        facts = read_facts(home)
        now = time.time()
        # A room.reaped fact finishes its path only until the strand is leased again:
        # a reopened thread wakes its latest strand into a fresh room at that path.
        leased = {str(f.data.get("strand")): i for i, f in enumerate(facts) if f.kind == "lease"}
        reaped = {str(f.data.get("path")) for i, f in enumerate(facts) if f.kind == "room.reaped"
                  and i > leased.get(str(f.data.get("strand")), -1)}
        asides = {Path(str(f.data["aside"])).resolve(): f.at for f in facts
                  if f.kind == "room.repaired" and f.data.get("aside")}
        key = self._key
        working = busy(rooms)
        latest = latest_strands(facts)
        settled = settled_threads(home, facts)
        holding = holders(facts)
        gone, kept, settle = 0, {}, {}
        cloned, freed, held = 0, 0, {}
        for room in sorted(rooms.iterdir()):
            if room.name.startswith(".") or not room.is_dir():
                continue
            try:
                if key(room) in reaped:
                    self._remove(room, "room", room.name, done=True)
                    continue
                why = room_kept(home, facts, room.name, now, settled=settled, holding=holding)
                if not why:
                    self._remove(room, "room", room.name)
                    gone += 1
                    continue
            except Exception as exc:
                why = f"inspection failed: {type(exc).__name__}: {exc}"
            kept[why.split(":")[0]] = kept.get(why.split(":")[0], 0) + 1
            if why.startswith(BODY_REASONS):
                settle[room.name] = why
            for name, done, bytes_, reason in self.clones(room, facts, reaped, now, working, latest, settled):
                if done:
                    cloned += 1
                    freed += bytes_
                    continue
                if not reason:
                    continue
                held[reason.split(":")[0]] = held.get(reason.split(":")[0], 0) + 1
                if reason.startswith(BODY_REASONS) and room.name not in settle:
                    settle[room.name] = reason
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
        self.log(home, f"reap: {cloned} clones reaped ({freed / 1024 ** 3:.2f} GB); kept " + (
            ", ".join(f"{n} ({why})" for why, n in sorted(held.items())) or "none"))
        self.tell(settle)

    def clones(self, room: Path, facts, reaped: set[str], now: float, working: set[str],
               latest: set[str], settled: set[str] = frozenset()):
        """Converge ``clone:<strand>/<name>`` for each code clone of a kept room.

        Holds when the clone is gone or ``clone_kept`` names a reason. ``make``
        writes ``room.reaped {what: clone}`` and then deletes, so a path still on
        disk under that fact is finished by the next pass's ``make``.
        Yields ``(name, removed, bytes, reason)``.
        """
        from .converge import Want, converge
        strand = room.name
        for checkout in clones(room):
            name, reason, removed = checkout.name, [""], [0]
            finishing = self._key(checkout) in reaped

            def holds(checkout=checkout, name=name, reason=reason, finishing=finishing):
                if not checkout.exists():
                    return True
                if finishing:
                    return False
                try:
                    reason[0] = clone_kept(self.home, facts, strand, name, now, working, latest, settled)
                except Exception as exc:
                    reason[0] = f"inspection failed: {type(exc).__name__}: {exc}"
                return bool(reason[0])

            def make(checkout=checkout, removed=removed, finishing=finishing):
                removed[0] = 0 if finishing else size(checkout)
                self._remove(checkout, "clone", strand, done=finishing)
                return None

            gen = max((int(f.data.get("gen") or 0) for f in facts
                       if f.kind == "lease" and f.data.get("strand") == strand), default=0)
            try:
                converge(self.home, strand, gen, [Want(f"clone:{strand}/{name}", holds, make)])
            except Exception as exc:
                reason[0] = f"removal failed: {type(exc).__name__}: {exc}"
            yield name, not checkout.exists(), removed[0], reason[0]

    def tell(self, settle: dict[str, str]) -> None:
        """One inbox notice naming every room a body must settle, when one is new to it."""
        from .home import atomic_write
        from .ledger import read_facts
        from .loom import notice
        from .project import sender_threads
        path = self.home.root / KEPT
        try:
            told = set(json.loads(path.read_text()).get("told") or ())
        except (OSError, ValueError, AttributeError):
            told = set()
        new = set(settle) - told

        def covers(old) -> bool:
            # An earlier notice is restated only if each room it names is listed again or gone.
            named = re.findall(r"^- (\S+) \(thread ", str(old.data.get("body", "")), re.M)
            return all(name in settle or not self.home.room(name).exists() for name in named)
        if new:
            facts = read_facts(self.home)
            threads = sender_threads(facts)
            notice(self.home, facts, "rooms-kept", ",".join(sorted(settle)),
                   f"{len(settle)} rooms are kept for work a body left in them ({len(new)} new). For each,"
                   " land what it holds, move it into the self, or discard it with a reason:\n" + "\n".join(
                       f"- {strand} (thread {threads.get(strand, '?')}): {why}"
                       for strand, why in sorted(settle.items())),
                   "then clear the room with `python -m brr.loom.runtime reap <strand> --force"
                   " --why \"<what you did with it>\"`", supersedes=covers)
        # Told rooms stay told while kept; a room that leaves and comes back is new again.
        atomic_write(path, json.dumps({"kept": settle, "told": sorted(set(settle) & (told | new))},
                                      sort_keys=True) + "\n")

    def _key(self, path: Path) -> str:
        return str(path.resolve().relative_to(self.home.root.resolve()))

    def _remove(self, path: Path, what: str, strand: str, done: bool = False, why: str = "") -> None:
        for repo in unlend(self.home, path):  # before the fact: a borrower that cannot be raises and keeps the path
            self.log(self.home, f"unlent {path}: {repo} now holds its own objects")
        if not done:
            from brr.daemon2.facts import Fact
            from .ledger import append
            install = self.home.install_id()
            data = {"what": what, "strand": strand, "path": self._key(path),
                    "bytes": size(path), "heads": heads(path)}
            if why:
                data["forced"] = why
            # The fact first: a crash mid-delete leaves a path the next pass finishes.
            append(self.home, Fact(kind="room.reaped", by=f"loom:{install}", id=f"room.reaped:{self._key(path)}",
                                   data=data))
            self.log(self.home, f"reaped {what} {path}")
        shutil.rmtree(path, ignore_errors=True)


def kept_line(home, thread: str, threads: dict[str, str]) -> str:
    """At wake: this thread's rooms the last pass kept for work a body left."""
    try:
        kept = json.loads((home.root / KEPT).read_text()).get("kept") or {}
    except (OSError, ValueError, AttributeError):
        return ""
    mine = sorted((s, why) for s, why in kept.items() if threads.get(s) == thread)
    if not mine:
        return ""
    return ("rooms of this thread kept for work a body left in them; land it, move it into the self, or"
            " discard it, then `python -m brr.loom.runtime reap <strand> --force --why \"…\"`:\n"
            + "".join(f"- {strand}: {why}\n" for strand, why in mine))


def main(argv: list[str]) -> int:
    """``reap <strand>`` says why a room is kept; ``--force --why`` clears one a body
    settled; ``--discard <name> --why`` removes a loose file from the caller's room."""
    from brr.daemon2.facts import Fact
    from .home import Home, home_of_room, mint
    from .ledger import append, read_facts
    parser = argparse.ArgumentParser(prog="python -m brr.loom.runtime reap")
    parser.add_argument("strand", nargs="?")
    parser.add_argument("--home")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--discard", metavar="NAME")
    parser.add_argument("--why", default="")
    args = parser.parse_args(argv)
    if (args.force or args.discard) and not args.why.strip():
        parser.error("--force and --discard need --why: what was done with the work")
    own = Path(os.environ["BRNRD_ROOM"]) if os.environ.get("BRNRD_ROOM") else None
    if args.home:
        home = Home(Path(args.home))
    elif own is not None:
        home = home_of_room(own)
    else:
        parser.error("no --home and BRNRD_ROOM is unset")
    if args.discard:
        if own is None:
            parser.error("--discard works in the caller's own room: BRNRD_ROOM is unset")
        if args.discard not in loose(own):
            sys.stderr.write(f"{args.discard} is not a loose file at {own}\n")
            return 1
        path = own / args.discard
        append(home, Fact(kind="room.discarded", by=f"strand:{own.name}", id=f"room.discarded:{own.name}/{mint(5)}",
                          data={"strand": own.name, "path": args.discard, "bytes": size(path),
                                "why": args.why.strip()}))
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
        sys.stdout.write(f"discarded {path}\n")
        return 0
    if not args.strand:
        parser.error("name a strand, or --discard a loose file")
    room = home.room(args.strand)
    if not room.is_dir():
        sys.stderr.write(f"no room {room}\n")
        return 1
    why = room_kept(home, read_facts(home), args.strand, time.time(), force=args.force)
    if why:
        sys.stdout.write(f"kept: {why}\n")
        return 1 if args.force else 0
    if not args.force:
        sys.stdout.write("reapable: the next pass removes it\n")
        return 0
    from .loom import _log
    Reaper(home, _log)._remove(room, "room", args.strand, why=args.why.strip())
    sys.stdout.write(f"reaped {room}\n")
    return 0

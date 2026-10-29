"""A room as a list of wants, each observed, made true once, observed again.

A want checks the disk (or the ledger) for its end state, makes it with one
idempotent act when it is missing, and writes a fact only when that act ended
somewhere other than expected. A retry after a crash runs the same list, so a
step that a crash cut short is redone because its end state is absent, not
because a flag computed before the crash said so (2026-10-23: a room cloned
before an OOM kill was never seeded, since ``fresh`` was false on retry).

A want that still does not hold after its act raises ``Unconverged``; the
attempt fails as the home's, and the inbox is told (``_end_attempt``).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from brr.daemon2.facts import Fact

from .home import Home, atomic_write
from .ledger import LedgerConflict, append
from .selfrepo import SelfError, git

# The kinds a want writes when it falls back; every one names the strand.
SEEDED = "room.seeded"
FALLBACK = "room.fallback"
WAKE_FALLBACK = "wake.fallback"


class Unconverged(SelfError):
    """A want whose act ran and whose end state is still absent."""

    def __init__(self, want: str, why: str = ""):
        super().__init__(f"room want {want} does not hold" + (f": {why}" if why else ""))
        self.want = want


@dataclass(frozen=True)
class Want:
    name: str
    holds: Callable[[], bool]                       # observe only
    make: Callable[[], tuple[str, dict] | None]     # one idempotent act; a fallback fact or None
    always: bool = False                            # the act is itself the observation (heal)


def record(home: Home, kind: str, strand: str, gen: int, want: str, data: dict) -> Fact:
    """The fallback fact, keyed on (strand, gen, want): a retry that falls back the same way adds nothing."""
    data = {"strand": strand, "gen": gen, **data}
    ident = f"{kind}:{strand}:{gen}:{want}"
    try:
        return append(home, Fact(kind=kind, by=f"loom:{home.install_id()}", data=data, id=ident))
    except LedgerConflict:
        digest = hashlib.sha1(json.dumps(data, sort_keys=True).encode()).hexdigest()[:8]
        return append(home, Fact(kind=kind, by=f"loom:{home.install_id()}", data=data,
                                 id=f"{ident}:{digest}"))


def converge(home: Home, strand: str, gen: int, wants: list[Want]) -> list[Fact]:
    written: list[Fact] = []
    for want in wants:
        if not want.always and want.holds():
            continue
        fallback = want.make()
        if fallback is not None:
            kind, data = fallback
            written.append(record(home, kind, strand, gen, want.name, data))
        if not want.holds():
            raise Unconverged(want.name)
    return written


def seeded(facts, strand: str) -> bool:
    """The ledger, not the disk, answers whether this strand's room was seeded.

    ``room.unseeded`` (written before this module) reads as seeded from main.
    """
    return any(f.kind in (SEEDED, "room.unseeded") and f.data.get("strand") == strand
               for f in facts)


def ran_before(facts, strand: str, gen: int) -> bool:
    """A body of this strand started in an earlier generation: its room is its own."""
    return any(f.kind == "body.started" and f.data.get("strand") == strand
               and int(f.data.get("gen", gen)) < gen for f in facts)


def seed(home: Home, clone: Path, strand: str, parent: str) -> dict:
    """Start where the parent stands: its strand branch, else its HEAD, else main.

    Strands share the self through their parent. A crash can leave a parent
    room on another branch: on 2026-10-23 an OOM killed a clone before its
    strand branch existed, its body worked on ``main``, and every successor
    failed to fetch ``strand/<parent>``. The room is reset only while it holds
    nothing of its own (no commit beyond ``origin/main``, no dirty path).
    Returns the ``room.seeded`` data; ``source`` names where it started.
    """
    source = home.room(parent) / "self"
    data: dict = {"parent": parent, "room": str(source)}
    errors = []
    fetched = None
    if (source / ".git").exists():
        for ref in (f"strand/{parent}", "HEAD"):
            proc = git(clone, "fetch", "-q", "--no-tags", str(source), ref, check=False)
            if proc.returncode == 0:
                fetched = ref
                break
            errors.append((proc.stderr.strip().splitlines() or ["fetch failed"])[-1])
    else:
        errors.append(f"parent room {parent} is gone")
    if errors:
        data["error"] = "; ".join(errors)
    if fetched is None:
        data.update(source="main", sha=_sha(clone, "HEAD"))
        return data
    tip = _sha(clone, "FETCH_HEAD")
    if tip == _sha(clone, "HEAD"):
        data.update(source=f"strand/{parent}" if fetched != "HEAD" else "HEAD", sha=tip)
        return data
    own = git(clone, "rev-list", "--count", "HEAD", "--not", "origin/main", check=False)
    dirty = git(clone, "status", "--porcelain", check=False).stdout.strip()
    if own.stdout.strip() not in ("", "0") or dirty:
        errors.append("the room already holds its own work; not reset to the parent's")
        data.update(source="kept", sha=_sha(clone, "HEAD"), error="; ".join(errors))
        return data
    git(clone, "reset", "-q", "--hard", "FETCH_HEAD")
    data.update(source=f"strand/{parent}" if fetched != "HEAD" else "HEAD", sha=tip)
    return data


def _sha(clone: Path, ref: str) -> str:
    proc = git(clone, "rev-parse", "--verify", "-q", ref, check=False)
    return proc.stdout.strip()


def room_wants(home: Home, strand: str, gen: int, parent: str | None, facts: list,
               ensure: Callable[[], None]) -> list[Want]:
    """``self`` then ``seed``: the clone, then where it starts.

    ``self`` always runs: ``heal_room`` touches only what it finds broken, and
    a sound room comes back unchanged. ``seed`` holds once the ledger has a
    ``room.seeded`` for the strand, or when there is nothing to seed from: no
    parent, or a body of this strand already ran in the room.
    """
    clone = home.room(strand) / "self"
    branch = f"refs/heads/strand/{strand}"
    done: list[bool] = []

    def sound() -> bool:
        return ((clone / ".git").exists()
                and git(clone, "rev-parse", "--verify", "-q", branch, check=False).returncode == 0)

    def self_make():
        ensure()
        return None

    def seed_holds() -> bool:
        return (bool(done) or parent is None or seeded(facts, strand)
                or ran_before(facts, strand, gen))

    def seed_make():
        data = seed(home, clone, strand, parent)
        done.append(True)
        return SEEDED, data

    return [Want("self", sound, self_make, always=True),
            Want("seed", seed_holds, seed_make)]


def port_want(room: Path, wait: str | None) -> Want:
    """``port/{in,out}`` exist, ``wait`` is the thread's, and no last-body file is left."""
    port = room / "port"
    stale = ("jack-state.json", "policy", "core.md")

    def holds() -> bool:
        if not ((port / "in").is_dir() and (port / "out").is_dir()):
            return False
        if wait is not None and ((port / "wait").read_text() if (port / "wait").is_file()
                                 else None) != wait + "\n":
            return False
        return not any((port / name).exists() for name in stale)

    def make():
        (port / "in").mkdir(parents=True, exist_ok=True)
        (port / "out").mkdir(parents=True, exist_ok=True)
        if wait is not None:
            atomic_write(port / "wait", wait + "\n")
        for name in stale:
            (port / name).unlink(missing_ok=True)
        return None

    return Want("port", holds, make)


def entry_want() -> Want:
    """The shim exists and ``current`` runs (entry.py); every launcher below names it.

    A missing or broken ``current`` is linked to the loom's own prefix and
    the fallback fact says why; one that cannot be linked fails the attempt.
    """
    from . import entry

    def make():
        try:
            done = entry.ensure()
        except OSError as exc:
            raise Unconverged("entry", str(exc)) from None
        return (FALLBACK, {"want": "entry", **done}) if done and "current" in done else None

    return Want("entry", entry.sound, make)


def driver_want(clone: Path) -> Want:
    """The clone's README merge driver runs through the shim, so a room made
    before it converges at its next wake."""
    from .merge_driver import driver_command, install_merge_driver

    def holds() -> bool:
        if not (clone / ".git").exists():
            return True
        proc = git(clone, "config", "--get", "merge.loom-readme.driver", check=False)
        return proc.stdout.strip() == driver_command()

    def make():
        install_merge_driver(clone)
        return None

    return Want("driver", holds, make)

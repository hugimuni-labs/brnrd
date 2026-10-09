"""Append-only facts. One writer, the loom; a test may inject a letter."""

from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path

from brr.daemon2.facts import Fact, union

from .home import Home, mint, thread_of


class LedgerConflict(RuntimeError):
    """The same fact id already exists with different content."""


def _content(fact: Fact) -> str:
    return json.dumps(
        [fact.kind, fact.by, fact.data], sort_keys=True, separators=(",", ":"),
    )


def _freeze(data: dict) -> dict:
    frozen = {}
    for key, value in data.items():
        frozen[key] = list(value) if isinstance(value, (list, tuple)) else value
    return frozen


# The loom's own file, not a strand's. One writer: the router, except a
# strand's loom still records that strand's fuse.
_LOOM_KINDS = {
    "lease", "released", "attention", "router", "router.renewed",
    "speech", "attention.cleared",
}


def fact_filename(fact: Fact) -> str:
    """loom.jsonl holds leases, releases, attention, and injected letters."""
    if fact.kind in _LOOM_KINDS:
        return "loom.jsonl"
    if fact.kind == "letter" and "gen" not in fact.data:
        return "loom.jsonl"
    strand = fact.data.get("strand")
    if not strand and fact.by.startswith("strand:"):
        strand = fact.by.split(":", 1)[1]
    if not strand and fact.kind == "letter":
        sender = fact.data.get("from")
        if isinstance(sender, str) and sender.startswith("s-"):
            strand = sender
    if not isinstance(strand, str) or not strand or "/" in strand:
        raise ValueError(f"no strand file for {fact.kind} {fact.id}")
    return f"{strand}.jsonl"


def read_file(path: Path) -> list[Fact]:
    """Parse one jsonl file, skipping a trailing line the process didn't finish."""
    raw = path.read_bytes()
    if not raw:
        return []
    if not raw.endswith(b"\n"):
        cut = raw.rfind(b"\n")
        raw = raw[:cut + 1] if cut >= 0 else b""
    facts = []
    for line in raw.decode().splitlines():
        if line.strip():
            facts.append(Fact.from_row(json.loads(line)))
    return facts


def read_facts(home: Home) -> list[Fact]:
    """Union every install's folder. Appending stays inside the caller's own."""
    root = home.root / "ledger" / "facts"
    if not root.is_dir():
        return []
    streams = [read_file(path) for path in sorted(root.glob("*/*.jsonl"))]
    if not streams:
        return []
    return union(*streams)


def _next_hlc(directory: Path, install: str) -> tuple[int, int, str]:
    ms = int(time.time() * 1000)
    best_ms = -1
    counter = -1
    for path in directory.glob("*.jsonl"):
        for fact in read_file(path):
            if not fact.hlc or fact.hlc[2] != install:
                continue
            fact_ms, fact_counter, _install = fact.hlc
            if (fact_ms > best_ms or
                    (fact_ms == best_ms and fact_counter > counter)):
                best_ms, counter = fact_ms, fact_counter
    if ms > best_ms:
        return (ms, 0, install)
    return (max(ms, best_ms), counter + 1, install)


def _existing(directory: Path) -> dict[str, Fact]:
    found: dict[str, Fact] = {}
    for path in directory.glob("*.jsonl"):
        for fact in read_file(path):
            old = found.get(fact.id)
            if old is not None and _content(old) != _content(fact):
                raise LedgerConflict(f"conflicting fact id: {fact.id}")
            found[fact.id] = fact
    return found


def _heal_tail(path: Path) -> None:
    """Cut a line a crashed writer left unfinished, so the next append starts clean.

    ``read_file`` skips such a tail, but appending after it would glue the new
    row onto the fragment and make the file unreadable for good. Caller holds
    the ledger lock.
    """
    if not path.is_file():
        return
    raw = path.read_bytes()
    if not raw or raw.endswith(b"\n"):
        return
    with open(path, "r+b") as handle:
        handle.truncate(raw.rfind(b"\n") + 1)
        handle.flush()
        os.fsync(handle.fileno())


def append(home: Home, fact: Fact) -> Fact:
    """Append ``fact``. Same id + same content is a no-op; different content raises."""
    directory = home.facts_dir()
    lock_path = directory / ".lock"
    with open(lock_path, "a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            existing = _existing(directory)
            old = existing.get(fact.id)
            data = _freeze(fact.data)
            if old is not None:
                probe = Fact(kind=fact.kind, by=fact.by, data=data, id=fact.id)
                if _content(old) == _content(probe):
                    return old
                raise LedgerConflict(f"conflicting fact id: {fact.id}")
            hlc = fact.hlc or _next_hlc(directory, home.install_id())
            stored = Fact(
                kind=fact.kind, by=fact.by, data=data, at=fact.at, id=fact.id,
                v=fact.v, hlc=hlc, after=fact.after,
            )
            line = json.dumps(stored.row(), sort_keys=True, separators=(",", ":"))
            path = directory / fact_filename(stored)
            _heal_tail(path)
            with open(path, "a") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            return stored
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def inject_letter(root: Path | str, *, to: str, body: str,
                  sender: str = "p-test", re: str | None = None) -> str:
    """A test or a person writes one letter. The sender is ``p-<name>``."""
    if not sender.startswith("p-"):
        raise ValueError(f"injected sender must be p-<name>, got {sender!r}")
    home = Home(root)
    thread = thread_of(to)
    if not home.thread_exists(thread):
        raise FileNotFoundError(
            f"no thread {thread} ({home.thread_dir(thread) / 'README.md'})")
    letter_id = f"{sender}/{mint(5)}"
    data: dict = {"id": letter_id, "to": to, "body": body, "from": sender}
    if re:
        data["re"] = re
    append(home, Fact(kind="letter", by=f"person:{sender}", data=data, id=letter_id))
    return letter_id

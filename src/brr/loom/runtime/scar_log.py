"""Append scar receipts from hooks and compact them off the hook path."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Iterable, Iterator

from brr.redact import redact_detail

from .home import atomic_write, home_of_room
from .notes import SCAR_PREFIX

LOG = "scar-displays.jsonl"
DAY = 86400
WINDOW = 7 * DAY
RECENT_CALLS = 32


def _call(room: Path, payload: dict) -> str | None:
    if not payload.get("tool_use_id"):
        return None
    raw = json.dumps([room.name, payload.get("session_id"), payload["tool_use_id"]])
    return hashlib.sha256(raw.encode()).hexdigest()


def _line(row: dict) -> bytes:
    return (json.dumps(row, separators=(",", ":")) + "\n").encode()


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _line(row)
    while True:
        fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o644)
        try:
            identity = os.fstat(fd)
            if os.write(fd, data) != len(data):
                raise OSError("short scar log write")
        finally:
            os.close(fd)
        try:
            if os.path.samestat(identity, path.stat()):
                return
        except FileNotFoundError:
            pass
        # A report detached this inode. Retry on the live file. The report
        # deduplicates receipts that made it into both files.


def record_displays(room: Path, payload: dict, paths: list[str]) -> None:
    scars = [path for path in paths if path.startswith(SCAR_PREFIX)]
    if not scars:
        return
    call = _call(room, payload)
    if call is None:
        return
    _append(home_of_room(room).root / "loom" / LOG, {"at": time.time(), "call": call, "paths": scars})

    # Room-local jack state. One marker per call lets overlapping delegates
    # retain and consume their own receipts without replacing a shared map.
    recent = room / "port" / "scar-calls"
    recent.mkdir(parents=True, exist_ok=True)
    (recent / call).touch()
    entries = []
    for marker in recent.iterdir():
        try:
            entries.append((marker.stat().st_mtime_ns, marker))
        except FileNotFoundError:
            continue
    for _, marker in sorted(entries, reverse=True)[RECENT_CALLS:]:
        marker.unlink(missing_ok=True)


def record_outcome(room: Path, payload: dict, failed: bool, why: str) -> None:
    call = _call(room, payload)
    if call is None:
        return
    try:
        (room / "port" / "scar-calls" / call).unlink()
    except FileNotFoundError:
        return
    row = {"at": time.time(), "call": call}
    if failed:
        row["failure"] = redact_detail(why)[:200]
    _append(home_of_room(room).root / "loom" / LOG, row)


def _rows(path: Path) -> Iterator[dict]:
    try:
        stream = path.open("rb")
    except FileNotFoundError:
        return
    with stream:
        for line in stream:
            if not line.endswith(b"\n"):
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


def _collect(rows: Iterable[dict], now: float) -> tuple[float | None, list[dict]]:
    since = None
    kept = {}
    for row in rows:
        display = "paths" in row
        if display or "since" in row:
            began = row["at"] if display else row["since"]
            if began is not None and began <= now:
                since = began if since is None else min(since, began)
        if "call" in row and row["at"] >= now - WINDOW:
            kept.setdefault((row["call"], display), row)
    return since, list(kept.values())


def history(path: Path, now: float) -> tuple[float | None, list[dict]]:
    """Read a rolling window. Readers and the reaper lock to serialize rotation."""
    if not path.parent.is_dir():
        return None, []
    archive = path.with_suffix(".archive.jsonl")
    pending = path.with_suffix(".pending.jsonl")
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        archived = list(_rows(archive))
        rotated = archived[0]["rotated"] if archived else None
        if pending.exists():
            archived = _compact(archive, pending, archived, now)
            rotated = now

        live = list(_rows(path))
        due = rotated is None or now - rotated >= DAY
        if due and any(row["at"] < now - WINDOW for row in (*archived[1:], *live)):
            # Detach before reading for compaction. An append to the old inode
            # after this point is also retried against the new live file.
            try:
                path.rename(pending)
            except FileNotFoundError:
                pass
            archived = _compact(archive, pending, archived, now)
            live = list(_rows(path))
        return _collect([*archived, *live], now)


def _compact(archive: Path, pending: Path, archived: list[dict], now: float) -> list[dict]:
    since, kept = _collect([*archived, *_rows(pending)], now)
    header = {"since": since, "rotated": now}
    atomic_write(archive, _line(header) + b"".join(map(_line, kept)), sync_directory=True)
    pending.unlink(missing_ok=True)
    return [header, *kept]

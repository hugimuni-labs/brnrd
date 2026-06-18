"""Presence registry — who is awake in the repo right now.

A lightweight, gitignored registry under ``.brr/presence/`` so overlapping
thoughts can see who else is active and rarely collide on the same work —
the collision-avoidance half of the Society-of-Mind concurrency model
(``kb/design-agent-dominion.md`` §4). The daemon is single-flight, but the
system is *already* multi-thought because ad-hoc sessions (Cursor, Codex,
a hand-run agent) work alongside the daemon, so knowing who's present is
useful even with one daemon worker.

Design: each participant owns exactly one JSON file (``<id>.json``) and
only ever writes its own, so the registry needs **no lock** — concurrent
participants touch disjoint files. Reads prune entries whose process is
gone (same-host pid check) or whose heartbeat went stale, so a crashed
participant leaves no ghost behind. Eventual consistency is fine: a reader
sees presence as of the last heartbeat, which is exactly what a
collision-avoidance hint needs.
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

# A participant is considered gone if its heartbeat is older than this.
# Sized comfortably above the daemon heartbeat interval (30s) so a busy
# worker that simply hasn't ticked recently isn't pruned as dead.
DEFAULT_STALE_AFTER_S = 300.0
PRESENCE_DIRNAME = "presence"


def _presence_dir(brr_dir: Path) -> Path:
    return brr_dir / PRESENCE_DIRNAME


def _host() -> str:
    try:
        return socket.gethostname()
    except OSError:  # pragma: no cover - hostname lookups rarely fail
        return ""


def _pid_alive(pid: int) -> bool:
    """Return True if *pid* is a live process on this host."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    except OSError:
        return True
    return True


def _atomic_write(path: Path, content: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.write(fd, content.encode("utf-8"))
        os.close(fd)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def register(
    brr_dir: Path,
    *,
    kind: str,
    stream: str | None = None,
    run_id: str | None = None,
    entry_id: str | None = None,
    pid: int | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Record a participant as present; return its entry (with ``id``).

    *kind* is the participant class (``daemon``, ``session``, …), *stream*
    the work it's on (a conversation key or label) so others can tell
    whether they'd collide. The returned ``id`` is the handle for
    :func:`heartbeat` and :func:`deregister`.
    """
    pdir = _presence_dir(brr_dir)
    pdir.mkdir(parents=True, exist_ok=True)
    eid = entry_id or uuid.uuid4().hex[:12]
    ts = now if now is not None else time.time()
    entry = {
        "id": eid,
        "kind": kind,
        "stream": stream or "",
        "run_id": run_id or "",
        "pid": int(pid if pid is not None else os.getpid()),
        "host": _host(),
        "started_at": ts,
        "last_seen": ts,
    }
    _atomic_write(pdir / f"{eid}.json", json.dumps(entry))
    return entry


def heartbeat(brr_dir: Path, entry_id: str, *, now: float | None = None) -> bool:
    """Refresh a participant's ``last_seen``. Returns False if it's gone."""
    path = _presence_dir(brr_dir) / f"{entry_id}.json"
    entry = _read(path)
    if entry is None:
        return False
    entry["last_seen"] = now if now is not None else time.time()
    try:
        _atomic_write(path, json.dumps(entry))
    except OSError:
        return False
    return True


def deregister(brr_dir: Path, entry_id: str) -> None:
    """Remove a participant's entry. Best-effort; a stale entry self-prunes."""
    path = _presence_dir(brr_dir) / f"{entry_id}.json"
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def list_active(
    brr_dir: Path,
    *,
    stale_after_s: float = DEFAULT_STALE_AFTER_S,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Return live participants, oldest first, pruning ghosts as it goes.

    An entry is pruned (its file deleted) when its process is gone (same
    host, dead pid) or its heartbeat is older than *stale_after_s*. The
    prune-on-read keeps the registry self-healing without a separate
    sweeper: whoever reads it cleans it.
    """
    pdir = _presence_dir(brr_dir)
    if not pdir.exists():
        return []
    cutoff = (now if now is not None else time.time()) - stale_after_s
    host = _host()
    live: list[dict[str, Any]] = []
    for path in pdir.iterdir():
        if path.suffix != ".json":
            continue
        entry = _read(path)
        if entry is None:
            path.unlink(missing_ok=True)
            continue
        last_seen = float(entry.get("last_seen") or 0)
        same_host = bool(entry.get("host")) and entry.get("host") == host
        dead_pid = same_host and not _pid_alive(int(entry.get("pid") or 0))
        if dead_pid or last_seen < cutoff:
            path.unlink(missing_ok=True)
            continue
        live.append(entry)
    live.sort(key=lambda e: float(e.get("started_at") or 0))
    return live


def _read(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

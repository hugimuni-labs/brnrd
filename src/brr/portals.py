"""Portal surfaces — the daemon-owned control files a wake reads.

Two files sit in every run's outbox dir and are *written by the driver,
read by the wake*: ``inbox.json`` (what else is waiting) and
``portal-state.json`` (posture, notices, pending events). They are not
deliverables — nothing in this module speaks to a user.

Extracted from ``daemon`` (#507 L3) because ``init`` now plays the
driver's part for exactly one run: it needs the same file names, the same
top-level keys, and the same atomic-write discipline, without the ~1400
lines of run lifecycle that surround them in the daemon. The daemon
delegates the inbox writer here; the full daemon portal-state writer stays
where it is (it is deeply ``Run``-coupled), and init writes its own thin
capsule through :func:`write_portal_state` with the keys a wake's
discipline actually reads.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import protocol

LIVE_INBOX_NAME = "inbox.json"
LIVE_PORTAL_STATE_NAME = "portal-state.json"

#: Control files a drain loop must never mistake for a chat message.
CONTROL_NAMES = frozenset({LIVE_INBOX_NAME, LIVE_PORTAL_STATE_NAME})


def is_staging_name(name: str | Path) -> bool:
    """True when *name* is an in-progress atomic-write staging file.

    The outbox contract is "write to a staging name, rename = commit", so a
    drain must be blind to the staging half or it can deliver a half-written
    message. The obvious predicate — ``Path(name).suffix == ".tmp"`` — only
    matches when ``.tmp`` is the *last* component, and real writers do not
    oblige: Claude's editor stages as ``note.md.tmp.<pid>.<rand>``, whose
    suffix is ``.<rand>``. That file was drained and delivered mid-stage in
    ``run-260723-1239-zjqc`` (#590); the resident's own rename then failed
    with ENOENT on a message the user had already received.

    So: any suffix component of ``.tmp`` marks the file as staging, wherever
    it sits. ``notes.md`` still delivers; ``note.md.tmp.1.abc`` never does.
    """
    return ".tmp" in Path(name).suffixes


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _write_json(path: Path, payload: dict[str, Any]) -> Path | None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        protocol._atomic_write(
            path,
            json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        )
        return path
    except OSError:
        return None


def write_live_inbox(
    outbox_dir: Path | None,
    current_event_id: str,
    events: list[dict[str, Any]],
) -> Path | None:
    """Refresh the live inbox view exposed to the running wake.

    The file sits in the run outbox because that directory is already
    mounted into every run environment. It is driver-owned control state,
    not a deliverable outbox message.

    *events* is computed by the caller — the daemon applies its own
    visibility rules (worker isolation, respawn-origin exclusion, dispatch
    edges); init has exactly one rule (everything pending that is not the
    current event), and neither belongs in a file writer.
    """
    if not outbox_dir:
        return None
    return _write_json(
        Path(outbox_dir) / LIVE_INBOX_NAME,
        {
            "version": 1,
            "generated_at": _utc_now(),
            "current_event": current_event_id,
            "events": events,
        },
    )


def write_portal_state(
    outbox_dir: Path | None,
    payload: dict[str, Any],
) -> Path | None:
    """Write a ``portal-state.json`` capsule verbatim (plus a timestamp).

    Deliberately dumb: the *shape* of a capsule is the caller's business.
    :func:`init_portal_state` builds init's.
    """
    if not outbox_dir:
        return None
    body = dict(payload)
    body.setdefault("version", 1)
    body["generated_at"] = _utc_now()
    return _write_json(Path(outbox_dir) / LIVE_PORTAL_STATE_NAME, body)


def init_portal_state(
    *,
    current_event_id: str,
    events: list[dict[str, Any]],
    phase: str,
    notices: list[dict[str, Any]] | None = None,
    change_token: str | None = None,
) -> dict[str, Any]:
    """The reduced capsule an init wake gets (spec §3.2).

    Same file name and the same top-level keys the wake's discipline reads
    (``events``, ``notices``, ``resources``, ``change_token``) — but the
    daemon facets that have no meaning before a gate exists say so
    explicitly rather than being absent. ``unimplemented`` is an honest
    answer; a missing key reads as "not measured yet" and invites a
    resident to wait for it.
    """
    return {
        "version": 1,
        "stage": "brnrd init wake",
        "phase": phase,
        "current_event": current_event_id,
        "events": events,
        "notices": list(notices or []),
        "resources": {
            "quota": "unimplemented",
            "spend": "unimplemented",
            "context_window": "unimplemented",
            "coexisting_runs": "unimplemented",
            "remote_scm": "unimplemented",
        },
        "change_token": change_token or "",
    }

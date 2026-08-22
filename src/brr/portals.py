"""Portal surfaces — the control files shared by a wake and its driver.

Three files may sit in a run's outbox dir. The driver writes ``inbox.json``
(what else is waiting) and ``portal-state.json`` (posture, notices, pending
events); the resident writes ``menu.json`` (the one composed live menu for
its thread), which the driver validates and promotes through :mod:`brr.menus`.
They are control state, not deliverable outbox messages.

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

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from . import protocol
from . import schedule as schedule_mod

LIVE_INBOX_NAME = "inbox.json"
LIVE_PORTAL_STATE_NAME = "portal-state.json"
LIVE_MENU_NAME = "menu.json"
#: The agent-written liveness extension. A dotfile, so the drain never sees
#: it as a message and it needs no entry in :data:`CONTROL_NAMES`.
KEEPALIVE_NAME = ".keepalive"
#: A conscious close of a live conversation without the default linger.
#: First non-empty line is the reason; an empty marker is not an opt-out.
LINGER_OPT_OUT_NAME = ".linger-opt-out"

#: Control files a drain loop must never mistake for a chat message.
CONTROL_NAMES = frozenset(
    {LIVE_INBOX_NAME, LIVE_PORTAL_STATE_NAME, LIVE_MENU_NAME}
)


def keepalive_until(keepalive_path: Path | None) -> float | None:
    """Read an agent-written keepalive into an absolute epoch deadline.

    The file is a control dotfile in the run outbox carrying one line: an
    ISO-8601 timestamp ("busy until T"), or ``+<duration>`` (e.g. ``+30m``)
    interpreted from the file's mtime ("busy for N from when I wrote this", so
    re-reads don't slide). Returns epoch seconds, or ``None`` when the file is
    absent, empty, or unparseable.

    Lives here rather than in ``daemon`` because two readers now need it and
    they must never disagree: the daemon extends the run budget from it, and
    ``hooks`` asks the same file whether a claimed vigil is actually armed
    (#947). Two implementations of one rule is the shape where the copies agree
    and are wrong together (#722), so the daemon delegates to this one.
    """
    if keepalive_path is None or not keepalive_path.exists():
        return None
    try:
        raw = keepalive_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    first = raw.splitlines()[0].strip()
    if first.startswith("+"):
        secs = schedule_mod.parse_duration(first[1:].strip())
        if secs is None:
            return None
        try:
            mtime = keepalive_path.stat().st_mtime
        except OSError:
            return None
        return mtime + secs
    return schedule_mod.parse_iso(first)


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
    visibility rules (strand isolation, respawn-origin exclusion, dispatch
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


def content_token(payload: dict[str, Any]) -> str:
    """A digest of everything in *payload* that is not bookkeeping.

    The daemon's token (``daemon._change_token``) is a hash of its whole
    capsule minus the fields that tick on their own, precisely so that a
    poller keyed on the token sees *any* real change. Init used to hand
    :func:`init_portal_state` ``str(len(events))``, which is a token for one
    field: everything else about the capsule could move — the phase, and
    later ``awaiting_reply`` — with the token frozen, so a wake that polls
    the token instead of diffing the file observed nothing.
    """
    stable = {
        key: value
        for key, value in payload.items()
        if key not in {"generated_at", "change_token"}
    }
    encoded = json.dumps(
        stable, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def init_portal_state(
    *,
    current_event_id: str,
    events: list[dict[str, Any]],
    phase: str,
    notices: list[dict[str, Any]] | None = None,
    change_token: str | None = None,
    awaiting_reply: bool = False,
) -> dict[str, Any]:
    """The reduced capsule an init wake gets (spec §3.2).

    Same file name and the same top-level keys the wake's discipline reads
    (``events``, ``notices``, ``resources``, ``change_token``) — but the
    daemon facets that have no meaning before a gate exists say so
    explicitly rather than being absent. ``unimplemented`` is an honest
    answer; a missing key reads as "not measured yet" and invites a
    resident to wait for it.

    ``awaiting_reply`` is the one fact init has that no daemon run does: the
    driver is blocked on this human's keyboard *right now*. It is a boolean
    on purpose — "is the person still there" must be readable without
    pattern-matching a phase string, because the phase is prose for humans
    and this is a branch condition for a model.

    ``change_token`` defaults to a digest of the capsule, so the flip from
    waiting to not-waiting moves the token even when the event list has not
    changed at all.
    """
    body = {
        "version": 1,
        "stage": "brnrd init wake",
        "phase": phase,
        "awaiting_reply": bool(awaiting_reply),
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
    }
    body["change_token"] = change_token or content_token(body)
    return body

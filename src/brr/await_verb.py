"""``await:`` — the outbox verb for a select, not a sleep (#959).

The measured failure this closes: a resident waiting on a dispatched strand
or a background gate wrote ``until <condition>; do sleep 25; done`` as one
shell call. By the letter of the older liveness contract that "survives the
closeout" — ``.keepalive`` was armed, the thought never ended — but it
emitted zero tool boundaries for the whole span, and the daemon only ever
reaches a resident *at* a tool boundary (``daemon-substrate.md`` §boundary
tempo). Three of the maintainer's messages queued behind a wait that was
doing exactly what it was told.

``await:`` replaces the hand-rolled loop with a declared condition set the
daemon evaluates on its own heartbeat tick (every ``daemon._HEARTBEAT_INTERVAL``
seconds, independent of whether the resident calls anything at all) —
so the *daemon* is always listening, not just the resident. **``event`` is
always a member of the set, whether or not the resident names it**: a
resident cannot construct a blind wait by forgetting to mention "did a
message arrive", because forgetting is no longer an available state.

v1 is the verb and the hold path only — see #959. The daemon never ends the
run to service an ``await:``; it holds the slot (extending ``.keepalive``)
and surfaces a three-way outcome (``condition`` / ``event`` / ``timeout``)
in ``portal-state.json`` for the resident's own poll loop, or via
``brnrd portal await`` (a single bounded poll tick, see ``cli.py``).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import schedule as schedule_mod

#: The structural member no parse of ``await:`` can omit (see module note).
EVENT_CONDITION: dict[str, Any] = {"kind": "event", "raw": "event", "value": None}

_KNOWN_PREFIXES = ("file:", "pid:", "spawn:")


def _parse_condition(token: str) -> dict[str, Any] | str:
    """One ``await:`` token → a condition dict, or an error string."""
    token = token.strip()
    if not token:
        return "an empty condition token"
    if token == "event":
        return dict(EVENT_CONDITION)
    if token.startswith("file:"):
        value = token[len("file:"):].strip()
        if not value:
            return f"{token!r} names no path"
        return {"kind": "file", "raw": token, "value": value}
    if token.startswith("pid:"):
        raw_pid = token[len("pid:"):].strip()
        if not raw_pid.isdigit():
            return f"{token!r} is not a numeric pid"
        return {"kind": "pid", "raw": token, "value": int(raw_pid)}
    if token.startswith("spawn:"):
        value = token[len("spawn:"):].strip()
        if not value:
            return f"{token!r} names no run/event id"
        return {"kind": "spawn", "raw": token, "value": value}
    return (
        f"unrecognised condition {token!r} — known forms are "
        f"file:<path>, pid:<n>, spawn:<run-or-event-id>, event"
    )


def parse_await(
    fm: dict[str, Any],
) -> tuple[list[dict[str, Any]] | None, float | None, str | None]:
    """Parse an ``await:`` + ``timeout:`` outbox directive.

    Returns ``(conditions, timeout_seconds, error)``. On any refusal,
    ``error`` is a one-line reason meant for a notice and the first two
    values are ``None``. On success ``error`` is ``None`` and ``conditions``
    always ends with (or already contains) the structural ``event`` member —
    see :data:`EVENT_CONDITION`.
    """
    raw_timeout = str(fm.get("timeout") or "").strip()
    if not raw_timeout:
        return None, None, "timeout: is required — a wait with no ceiling is a hang"
    timeout_seconds = schedule_mod.parse_duration(raw_timeout)
    if timeout_seconds is None:
        return None, None, f"timeout: {raw_timeout!r} is not a parseable duration"
    if timeout_seconds <= 0:
        return None, None, "timeout: must be positive"

    raw_await = str(fm.get("await") or "").strip()
    conditions: list[dict[str, Any]] = []
    if raw_await:
        for token in raw_await.split("|"):
            parsed = _parse_condition(token)
            if isinstance(parsed, str):
                return None, None, f"await: {parsed}"
            conditions.append(parsed)

    # Structural guarantee, not a suggestion: a resident cannot build an
    # await: that never hears from a correspondent. Neutering this — making
    # the set exactly what the resident typed — is the guard #959 asks to
    # be neutered and watched fail: test_await_verb.py's
    # test_parse_await_structurally_always_includes_event goes red without it.
    if not any(c["kind"] == "event" for c in conditions):
        conditions.append(dict(EVENT_CONDITION))

    return conditions, timeout_seconds, None


def pid_alive(pid: int) -> bool:
    """Best-effort liveness check for a ``pid:`` condition.

    ``os.kill(pid, 0)`` sends no signal, only probes existence/permission.
    Fails closed (reports "alive") on anything ambiguous — a ``pid:``
    condition should fire on a confirmed exit, never on a permission quirk
    or a platform that can't answer.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _spawn_condition_fired(target: str, pending_events: list[dict[str, Any]]) -> bool:
    """True when *target* (a run id or the original spawn event id) finished.

    A concurrent spawn's completion always lands back as a ``spawn_completed``
    pending event, carrying either the child's own run id (``spawned_by_run``,
    once one was assigned) or the event id the spawn was dispatched under
    (``spawned_by_event`` — the same field the ``stop:``-cancelled and
    crashed-before-finishing paths already used, in
    ``daemon._notify_spawn_parent`` / ``_notify_spawn_parent_of_crash`` /
    ``_apply_run_stop``) — the two names a resident might plausibly have on
    hand when it writes ``spawn:<run-or-event-id>``.
    """
    for ev in pending_events:
        if ev.get("source") != "spawn_completed":
            continue
        if str(ev.get("spawned_by_run") or "") == target:
            return True
        if str(ev.get("spawned_by_event") or "") == target:
            return True
    return False


def evaluate(
    conditions: list[dict[str, Any]],
    pending_events: list[dict[str, Any]],
) -> tuple[str | None, str | None]:
    """Check every named condition once; ``(outcome, which)`` or ``(None, None)``.

    ``outcome`` is ``"condition"`` (a named ``file:``/``pid:``/``spawn:`` term
    fired — ``which`` names it) or ``"event"`` (some other pending event
    arrived that no named condition already explains) or ``None`` (still
    waiting; the caller decides separately whether the deadline has passed —
    that is the third outcome, ``"timeout"``, and it is not this function's
    to declare since it has no clock of its own).
    """
    for cond in conditions:
        kind = cond["kind"]
        if kind == "event":
            continue
        if kind == "file":
            if Path(cond["value"]).exists():
                return "condition", cond["raw"]
        elif kind == "pid":
            if not pid_alive(cond["value"]):
                return "condition", cond["raw"]
        elif kind == "spawn":
            if _spawn_condition_fired(cond["value"], pending_events):
                return "condition", cond["raw"]
    if pending_events:
        return "event", None
    return None, None

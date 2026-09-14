"""``await:`` — the wait with nothing to forget (#959, collapsed by #1187).

The measured failure #959 closed: a resident waiting on a dispatched strand
or a background gate wrote ``until <condition>; do sleep 25; done`` as one
shell call. By the letter of the older liveness contract that "survives the
closeout" — the thought never ended — but it
emitted zero tool boundaries for the whole span, and the daemon only ever
reaches a resident *at* a tool boundary (``daemon-substrate.md`` §boundary
tempo). Three of the maintainer's messages queued behind a wait that was
doing exactly what it was told.

**The evaluation half was right; the caller-facing half was not.** v1 asked
the caller to enumerate a condition set — ``file:`` / ``pid:`` /
``spawn:<id>`` — and #1187 measured what that costs: five child ids, one
typo, the whole directive silently discarded and a `resolved: true` left
standing that looked like an answer. A verb that asks the caller to restate
what the daemon already tracks is the bug, not the ergonomics.

So the grammar collapsed to what cannot be got wrong:

- **``event`` is no longer a condition; it is the semantics.** "Wake me when
  the daemon has something for me" is the entire meaning of the verb. A
  strand finishing already lands as a ``spawn_completed`` pending event, so
  ``spawn:<id>`` never added capability — it *subtracted* it, filtering out
  precisely what a dispatcher usually wants (whichever child finishes first).
- **``pid:`` is gone.** It duplicated the Shell's own background-process
  notification.
- **``file:`` survives as an optional composing trigger**, and only that: the
  one thing the daemon genuinely cannot observe (an external CI run, a human
  dropping a file). It *adds* a resolution trigger alongside the daemon's
  own; it can never narrow the wait to only that file, because a wait a
  correspondent cannot interrupt is the failure #959 exists to end.

The asymmetry is the whole design: **omitting the file gives you the correct
default**, where omitting a ``spawn:`` id used to give you a broken wait.

Everything a caller supplies is optional, ceiling included: a run with no
configured budget defaults to ``timeout: none`` — the seat stays open until
the daemon has something, with no clock of its own. A run with a budget
still defaults its ceiling from that remaining budget, and an explicit
``--timeout`` always wins either way. The daemon evaluates on its own
heartbeat tick (every ``daemon._HEARTBEAT_INTERVAL`` seconds, independent of
whether the resident calls anything at all) — that is what makes this a
*listening* wait rather than a *sleeping* one — and never ends the run to
service one: it holds the slot and surfaces the outcome in
``portal-state.json`` for ``brnrd await``'s own poll.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from . import schedule as schedule_mod

#: Values of the ``await:`` key that mean "arm the wait" and nothing more.
#: The key is a marker, not a grammar — ``brnrd await`` writes ``true``; an
#: empty value is accepted for the same reason (both say only "wait").
_MARKER_VALUES = {"", "true", "1", "yes", "on"}

#: What a directive still carrying v1's condition grammar is refused with.
#: Never silent, never by ignoring the extra terms: a resident who typed a
#: condition believes it is filtering the wait, and a wait that silently
#: means something other than what was typed is exactly #1187.
CONDITIONS_RETIRED = (
    "await: no longer takes conditions — `event` is the semantics now "
    "(the daemon wakes you for anything it has), `spawn:` and `pid:` are "
    "deleted, and a file trigger is `file: <path>` on its own line. "
    "Use `brnrd await [--timeout <duration>] [--file <path>]`"
)


#: Values of the ``timeout:`` key that mean "no ceiling — the seat stays
#: open until the next pending event (or a configured hard-cap deadline)."
#: Only an explicit ``none`` opens this; a *missing* key stays refused below
#: — an omission is not a decision, and the earlier sentence here ("a wait
#: with no ceiling is a hang") was only ever true of an unreachable one. This
#: wait resolves on any pending event on every heartbeat, and a configured
#: hard cap still bounds it when one exists.
_NO_CEILING_VALUES = {"none", "null"}


def parse_await(
    fm: dict[str, Any],
) -> tuple[str | None, float | None, str | None]:
    """Parse an ``await:`` directive's frontmatter.

    Returns ``(file_path, timeout_seconds, error)``. On any refusal *error*
    is a one-line reason meant for a notice and the first two values are
    ``None``. On success *error* is ``None``, *file_path* is the optional
    composing trigger (``None`` when the caller named none — the ordinary
    case, and the one the prose teaches), and *timeout_seconds* is either a
    positive ceiling or ``None`` — an explicit ``timeout: none``, meaning the
    seat stays open: this wait resolves only on a pending event or a
    configured hard-cap deadline, never on a clock of its own.
    """
    marker = str(fm.get("await") or "").strip()
    if marker.lower() not in _MARKER_VALUES:
        return None, None, CONDITIONS_RETIRED

    raw_timeout = str(fm.get("timeout") or "").strip()
    if not raw_timeout:
        return None, None, "timeout: is required — write `timeout: none` for no ceiling"
    if raw_timeout.lower() in _NO_CEILING_VALUES:
        raw_file = str(fm.get("file") or "").strip()
        if raw_file.startswith("file:"):
            return None, None, CONDITIONS_RETIRED
        return (raw_file or None), None, None
    timeout_seconds = schedule_mod.parse_duration(raw_timeout)
    if timeout_seconds is None:
        return None, None, f"timeout: {raw_timeout!r} is not a parseable duration"
    if timeout_seconds <= 0:
        return None, None, "timeout: must be positive"

    raw_file = str(fm.get("file") or "").strip()
    if raw_file.startswith("file:"):
        # v1's condition spelling, arriving under the new key. Refuse rather
        # than half-accept: `file: file:/tmp/x` would otherwise arm a wait on
        # a path that cannot exist, which is a silent never-fires.
        return None, None, CONDITIONS_RETIRED
    return (raw_file or None), timeout_seconds, None


def evaluate(
    file_path: str | None,
    pending_events: list[dict[str, Any]],
) -> tuple[str | None, str | None]:
    """One evaluation pass; ``(outcome, which)`` or ``(None, None)``.

    ``outcome`` is ``"event"`` (the daemon has something for the caller — a
    message, a child finishing, a schedule fire; all of them arrive as
    pending events) or ``"condition"`` (the optional ``file:`` trigger
    exists — *which* names it) or ``None`` (still waiting; the deadline is
    the caller's to check, and ``"timeout"`` is the third outcome this
    function cannot declare because it has no clock of its own).

    **Pending events win a tie, deliberately.** When the file appeared *and*
    a message is waiting, reporting the file would mask the correspondent —
    the one thing this verb exists to never do. The file trigger composes
    with the default; it never outranks it.
    """
    if pending_events:
        return "event", None
    if file_path and Path(file_path).exists():
        return "condition", f"file:{file_path}"
    return None, None


# ── The lease (move 2c) ──────────────────────────────────────────────────
#
# Before this, one ``brnrd await`` call blocked for at most ~9m20s and then
# returned ``pending — call again``; every re-call was a full model turn that
# re-read the whole scroll to learn nothing had happened (measured on the seat
# 2026-09-13/14: ~60 such turns in one night, ~350–580k tokens each). The
# daemon already evaluated the wait on its own heartbeat — the only thing
# returning every ten minutes was the *caller*, because the Shell's per-call
# cap forced it to.
#
# The bound, found rather than assumed (claude 2.1.269, read out of the
# binary): the Bash tool's per-call ceiling is ``BASH_MAX_TIMEOUT_MS`` from
# the Shell's own environment, default 600000 (10 min), with no hard upper
# limit above that; the per-call ``timeout`` parameter is clamped to it and
# defaults to ``BASH_DEFAULT_TIMEOUT_MS`` (120000). A PreToolUse hook may
# rewrite the call's input (``hookSpecificOutput.updatedInput``) before it
# runs. So the lease survives the bound in three pieces, none of which asks
# the resident to remember anything:
#
# 1. the daemon raises ``BASH_MAX_TIMEOUT_MS`` for a claude runner to cover
#    :data:`LEASE_MAX_SECONDS` plus margin (``worker/prepare.py``);
# 2. the pre-tool hook sees a ``brnrd await`` Bash call, sets its ``timeout``
#    to that maximum and stamps the cap it set into the command's own
#    environment (:data:`CALL_CAP_ENV`) — the CLI cannot otherwise know what
#    ``timeout`` its own call was given;
# 3. the CLI blocks until the daemon resolves the wait or the lease ceiling
#    passes, and returns early only when the stamped cap would otherwise kill
#    it. No stamp (codex, a hookless profile, an older daemon) ⇒ the old
#    per-Shell slice and ``pending — call again``, unchanged.

#: The lease's hard ceiling: one call never blocks longer than this. A wait
#: that outlives it still stands on the daemon's side — the call returns
#: ``pending`` and the next call continues the same arming.
LEASE_MAX_SECONDS = 6 * 3600.0

#: Env var the pre-tool hook exports into a rewritten ``brnrd await`` call:
#: the ``timeout`` (ms) it gave that call. The CLI's only honest source for
#: its own kill deadline.
CALL_CAP_ENV = "BRNRD_AWAIT_CALL_CAP_MS"

#: How far under the stamped call cap the CLI returns, counted from the call's
#: start. Covers the staging + drain-verdict wait (≤30s) already inside the
#: slice plus process exit.
CALL_CAP_MARGIN_SECONDS = 45.0

#: What the daemon sets ``BASH_MAX_TIMEOUT_MS`` to for a claude runner: the
#: lease ceiling plus ten minutes, so the hook's timeout always covers a full
#: lease with the margin above.
CLAUDE_BASH_MAX_TIMEOUT_MS = int((LEASE_MAX_SECONDS + 600.0) * 1000)

#: The Shell's own default per-call maximum when ``BASH_MAX_TIMEOUT_MS`` is
#: unset — what the hook stamps on a runner the daemon did not widen.
CLAUDE_BASH_DEFAULT_MAX_TIMEOUT_MS = 600_000

#: The record the CLI leaves when a lease ends, read once by the next
#: post-tool boundary for the chip's ``slept … · woke: …`` segment. Lives in
#: the run's outbox beside ``.hook-state.json``. Deliberately not named
#: ``*_NAME``: it is not a control file and must not be swept into the
#: closeout's control-file discovery.
LEASE_RECORD_FILE = ".await-lease.json"

_BRNRD_AWAIT_RE = re.compile(r"(?:^|[\s;&|(])(?:\S*/)?brnrd\s+await(?:\s|$)")


def is_await_command(command: object) -> bool:
    """Whether a shell command line invokes ``brnrd await``."""
    return isinstance(command, str) and bool(_BRNRD_AWAIT_RE.search(command))


def lease_ceiling(
    budget: dict[str, Any] | None, requested: float | None = None,
) -> float:
    """Seconds one ``brnrd await`` call may hold its lease.

    *requested* (``--ceiling``) wins when given; otherwise the run's remaining
    budget (``budget_seconds - elapsed_seconds``). Either way capped at
    :data:`LEASE_MAX_SECONDS`, and a run with no readable budget gets the cap.
    """
    if requested is not None and requested > 0:
        return min(float(requested), LEASE_MAX_SECONDS)
    budget = budget if isinstance(budget, dict) else {}
    try:
        remaining = float(budget.get("budget_seconds")) - float(
            budget.get("elapsed_seconds")
        )
    except (TypeError, ValueError):
        return LEASE_MAX_SECONDS
    return max(1.0, min(remaining, LEASE_MAX_SECONDS))


def call_cap_seconds(env: dict[str, str]) -> float | None:
    """The stamped per-call cap in seconds, or ``None`` when no hook stamped one."""
    raw = str(env.get(CALL_CAP_ENV) or "").strip()
    if not raw:
        return None
    try:
        value = float(raw) / 1000.0
    except ValueError:
        return None
    return value if value > 0 else None


def format_slept(seconds: float) -> str:
    """``12s`` · ``41m`` · ``3h12m`` — the chip's duration, whole units only."""
    total = max(0, int(round(seconds)))
    if total < 60:
        return f"{total}s"
    minutes, _ = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m" if minutes else f"{hours}h"
    return f"{minutes}m"


def write_lease_record(
    outbox_dir: Path, *, generation: object, slept_seconds: float, outcome: str,
) -> None:
    """Leave the lease's end for the next boundary. Best-effort, never raises."""
    record = {
        "generation": str(generation or ""),
        "slept_seconds": round(float(slept_seconds), 3),
        "outcome": outcome,
        "ended_at": time.time(),
    }
    try:
        target = Path(outbox_dir) / LEASE_RECORD_FILE
        tmp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(record), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        return


def read_lease_record(outbox_dir: Path | None) -> dict[str, Any] | None:
    """The last lease's end record, or ``None`` when absent/unreadable."""
    if outbox_dir is None:
        return None
    try:
        payload = json.loads(
            (Path(outbox_dir) / LEASE_RECORD_FILE).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        float(payload.get("slept_seconds"))
    except (TypeError, ValueError):
        return None
    if not str(payload.get("outcome") or "").strip():
        return None
    return payload


def lease_record_key(record: dict[str, Any]) -> str:
    """Identity of one lease's end — what the hook remembers having shown."""
    return f"{record.get('generation') or ''}@{record.get('ended_at') or ''}"


def lease_chip(record: dict[str, Any]) -> str:
    """``slept 3h12m · woke: event`` for one lease record."""
    return (
        f"slept {format_slept(float(record['slept_seconds']))} · "
        f"woke: {record['outcome']}"
    )

"""``await:`` — the wait with nothing to forget (#959, collapsed by #1187).

The measured failure #959 closed: a resident waiting on a dispatched strand
or a background gate wrote ``until <condition>; do sleep 25; done`` as one
shell call. By the letter of the older liveness contract that "survives the
closeout" — ``.keepalive`` was armed, the thought never ended — but it
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

Everything a caller supplies is now optional except the ceiling, and
``brnrd await`` fills that in from the run's own remaining budget. The
daemon still evaluates on its own heartbeat tick (every
``daemon._HEARTBEAT_INTERVAL`` seconds, independent of whether the resident
calls anything at all) — that is what makes this a *listening* wait rather
than a *sleeping* one — and never ends the run to service one: it holds the
slot, extending ``.keepalive``, and surfaces the outcome in
``portal-state.json`` for ``brnrd await``'s own poll.
"""

from __future__ import annotations

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


def parse_await(
    fm: dict[str, Any],
) -> tuple[str | None, float | None, str | None]:
    """Parse an ``await:`` directive's frontmatter.

    Returns ``(file_path, timeout_seconds, error)``. On any refusal *error*
    is a one-line reason meant for a notice and the first two values are
    ``None``. On success *error* is ``None``, *timeout_seconds* is positive,
    and *file_path* is the optional composing trigger (``None`` when the
    caller named none — the ordinary case, and the one the prose teaches).
    """
    marker = str(fm.get("await") or "").strip()
    if marker.lower() not in _MARKER_VALUES:
        return None, None, CONDITIONS_RETIRED

    raw_timeout = str(fm.get("timeout") or "").strip()
    if not raw_timeout:
        return None, None, "timeout: is required — a wait with no ceiling is a hang"
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

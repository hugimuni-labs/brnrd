"""``hold:`` — the resident's own resource-hold request.

Sibling to ``await_verb.py``, same shape: a pure parse/validate function the
daemon's outbox loop calls, refusing anything malformed with a one-line
notice rather than half-accepting it. Unlike ``await:`` (which keeps the
run's process alive, genuinely blocked, for a wait of unknown-but-bounded
length) ``hold:`` is for a pause the resident itself chose to name —
"notify near quota exhaustion, await a manual reset" being the case this
exists for (design-the-continuous-seat.md, design-the-allowance.md) — and it
ends the run's process the way any other terminal outcome does; see
``resource_hold.py``'s module docstring for why that distinction is the
whole fix.

Grammar, everything but the marker optional:

    hold: true
    reason: quota running low        # free text; default "resident_requested"
    resume: operator | reset | strands | any   # default "operator"; strands =
                                      # wake when one of this run's own
                                      # children submits/completes/asks
                                      # (a correspondent message wakes any
                                      # hold); refused when no strand is live;
                                      # any = the seat's resting state: a
                                      # message, an own strand, or a tick
    reset: 2026-09-12T00:00:00Z      # only meaningful with resume: reset —
                                      # an explicit deadline the resident
                                      # already has reason to believe (told
                                      # by the provider, read from its own
                                      # quota facet); the daemon falls back
                                      # to its own one-time measured read
                                      # when this is absent, and downgrades
                                      # to operator-only when neither is
                                      # available — never a guessed rate.

``resume: reset`` with no ``reset:`` and no measurable provider deadline is
not a refusal here — parsing succeeds with ``reset_deadline=None`` and the
daemon-side apply step is the one that decides whether to honour ``reset``
or downgrade to ``operator``, because only it can attempt the measured
read. This module never touches a clock or a filesystem.
"""

from __future__ import annotations

from typing import Any

from . import resource_hold

_MARKER_VALUES = {"", "true", "1", "yes", "on"}

_RESUME_ALIASES = {
    "operator": resource_hold.RESUME_OPERATOR,
    "manual": resource_hold.RESUME_OPERATOR,
    "reset": resource_hold.RESUME_RESET,
    "quota_reset": resource_hold.RESUME_RESET,
    "strands": resource_hold.RESUME_STRANDS,
    "children": resource_hold.RESUME_STRANDS,
    "spawn": resource_hold.RESUME_STRANDS,
    "any": resource_hold.RESUME_ANY,
    "anything": resource_hold.RESUME_ANY,
}


def parse_hold(fm: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Parse a ``hold:`` directive's frontmatter.

    Returns ``(spec, error)``. On success *spec* is
    ``{"reason", "provider", "resume_condition", "reset_deadline_hint"}``
    (``reset_deadline_hint`` is the *parsed* ``reset:`` epoch, or ``None``
    when absent — a hint the daemon may use as-is or refine, never a
    command). On refusal *spec* is ``None`` and *error* is a one-line
    reason meant for a notice.
    """
    marker = str(fm.get("hold") or "").strip()
    if marker.lower() not in _MARKER_VALUES:
        return None, (
            "hold: takes no value beyond the marker — write `hold: true` "
            "plus optional `reason:`/`resume:`/`reset:` lines"
        )
    raw_resume = str(fm.get("resume") or "").strip().lower()
    if not raw_resume:
        resume_condition = resource_hold.RESUME_OPERATOR
    elif raw_resume in _RESUME_ALIASES:
        resume_condition = _RESUME_ALIASES[raw_resume]
    else:
        return None, (
            f"resume: {raw_resume!r} is not recognised — use "
            "`resume: operator`, `resume: reset`, `resume: strands`, or `resume: any`"
        )
    reset_deadline_hint: float | None = None
    raw_reset = str(fm.get("reset") or "").strip()
    if raw_reset:
        from . import protocol as protocol_mod

        reset_deadline_hint = protocol_mod.parse_iso_epoch(raw_reset)
        if reset_deadline_hint is None:
            return None, f"reset: {raw_reset!r} is not a parseable timestamp"
    reason = str(fm.get("reason") or "").strip() or (
        resource_hold.REASON_WAITING_ON_STRANDS
        if resume_condition == resource_hold.RESUME_STRANDS
        else resource_hold.REASON_RESIDENT_REQUESTED
    )
    provider = str(fm.get("provider") or "").strip() or None
    return (
        {
            "reason": reason,
            "provider": provider,
            "resume_condition": resume_condition,
            "reset_deadline_hint": reset_deadline_hint,
        },
        None,
    )

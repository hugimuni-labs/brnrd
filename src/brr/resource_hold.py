"""Resource hold — daemon-owned parking for a provider resource limit.

design-the-continuous-seat.md / design-the-allowance.md: a resident that
hits a hard provider limit (measured 2026-09-05: a Codex thread's own
``task_complete.error.codex_error_info == "usage_limit_exceeded"``) needs a
durable *pause*, not a retry, an automatic provider switch, or an opaque
failed run. The incident this closes was never the limit itself — it was
what the daemon did about it: kept a Shell subprocess alive spinning on
``brnrd await``'s own call-again loop, which for a Shell whose per-call
ceiling is short means *the model itself* gets re-invoked just to restate
the same wait, at full context cost, until the very quota it is waiting out
is exhausted a second time (measured: ~1.6m weighted tokens spent *after*
the resident had already said it was parking).

The fix this module encodes: don't keep anything alive. A resource hold
ends the run's process (same as any other terminal outcome) and records the
one fact set a resume needs — reason, provider, the native session id
(so a resume can be a real ``codex exec resume <id>``, not a cold restart
wearing the same clothes), and which of two resume conditions the resident
or the automatic-detection path chose:

- ``RESUME_OPERATOR`` — released only by an explicit addressed reply
  (an ordinary correspondent message reaching the held conversation; see
  ``daemon.py``'s dispatch-time interception). The default, and the only
  condition an *automatic* detection ever selects on its own — "silence is
  neither permission nor a reset" (design-the-continuous-seat.md): nothing
  here guesses that quota has recovered.
- ``RESUME_STRANDS`` — additionally released by one of the held run's
  *own* children reporting back (:data:`STRAND_RELEASE_SOURCES`, matched on
  the event's ``spawn_parent_run_id`` or the run's recorded
  ``child_run_ids``). The park a parent takes on live strands: nothing
  spends while they work, the first one to submit wakes the seat. The
  bolt arms this itself when a live strand is dispositioned ``handoff``
  (``daemon.py``'s cut path) — a run with children still running lands
  ``held``, never ``done``.
- ``RESUME_ANY`` — the seat's resting state: released by a correspondent
  message, one of its own strands, or a ``schedule`` firing. Chosen by the
  daemon itself when a user-woken seat's turn ends with nothing armed
  (``seat.park_on_turn_end``, design-the-seat-that-never-quits.md), or by
  the resident with ``resume: any``.
- ``RESUME_RESET`` — additionally released once a *measured* provider
  reset deadline passes (a plain clock comparison against a timestamp the
  provider itself stated, captured once at arm time — never a guessed
  rate or a repeated poll). Only reachable through an explicit resident
  choice (the ``hold:`` outbox verb's own ``resume: reset`` field).

Every function here is pure: no filesystem, no daemon state, no clock
side-effects beyond an injectable ``now``. ``daemon.py`` owns persistence
(``Run.meta["resource_hold"]``, which rides the existing ``run.md``
frontmatter round-trip for free) and every daemon-facing effect (ending the
process, deferring sibling events, publishing portal-state).
"""

from __future__ import annotations

import time
from typing import Any

#: The run status this hold rides on (``run.py``'s ``STATUSES``) —
#: deliberately excluded from ``daemon.py``'s ``_UNFINISHED_RUN_STATUSES``,
#: so every boot-time janitor leaves a held run alone without needing to
#: know this module exists.
RUN_STATUS = "held"

REASON_QUOTA_EXHAUSTED = "quota_exhausted"
REASON_RESIDENT_REQUESTED = "resident_requested"
#: design-the-seat-that-never-quits.md §"The machinery, in slices" #3: an
#: `await:` idling with nothing pending, past `seat.park_after_boot_ratio`
#: (default 12.0) of this run's own recorded boot cost — **only when
#: `seat.park_on_hold_cost` opts the behaviour on** (default off, 2026-09-08:
#: the process stays open until the user releases it or execution is forced
#: to stop, never on a cost heuristic alone; the ratio still renders on the
#: chip either way). Distinct from `REASON_TURN_ENDED` — that one fires on
#: an *ordinary* clean turn end with nothing armed; this one fires while an
#: await is still armed, the moment the daemon's own heartbeat measures
#: holding as the dearer of the two.
REASON_HOLD_COSTLIER_THAN_BOOT = "hold_costlier_than_boot"
#: The starvation park (2026-09-08, his ask: "let you park safely … until a
#: refill, so that a user cannot wake you up when there is <2% of either
#: quota available"). Armed when the seat's binding quota (session, week,
#: or its own Core's bucket — whichever is lowest) reads below
#: ``seat.starve_floor_pct``; released only by :data:`RESUME_REFILL`.
REASON_QUOTA_STARVED = "quota_starved"

RESUME_OPERATOR = "operator"
RESUME_RESET = "reset"
#: Released only by a *measured* refill: the daemon reads the provider's
#: quota again and the binding bucket is back at or above
#: ``seat.refill_floor_pct`` (a window reset, or a manual reset the daemon
#: can see). The one condition a correspondent message does **not**
#: release — a message arriving while starved is accumulated and answered
#: with the reading; the user's ways out are a refill, or release/respawn
#: on another Core from the dashboard.
RESUME_REFILL = "refill"
#: Released by one of this run's own strands reporting back — the hold a
#: parent takes while its children are still working (2026-09-06: a seat
#: closed on three live strands because the only park verb slept through
#: their submits; "there is no reason to stop the run, especially if there
#: are living strands that the run should be waiting on"). A correspondent
#: message releases this one too — the operator always outranks the wait.
RESUME_STRANDS = "strands"
#: The seat's resting state (design-the-seat-that-never-quits.md): released
#: by *anything addressed to this seat* — a correspondent message, one of
#: its own strands, a schedule firing. The daemon parks a seat here on its
#: own when a turn ends with nothing armed (``seat.park_on_turn_end``), so
#: "the run ended" stops being a thing that happens to a user-woken seat.
RESUME_ANY = "any"
RESUME_CONDITIONS = frozenset({
    RESUME_OPERATOR, RESUME_RESET, RESUME_STRANDS, RESUME_ANY, RESUME_REFILL,
})

REASON_WAITING_ON_STRANDS = "waiting_on_strands"
REASON_TURN_ENDED = "turn_ended"

#: The child-event sources that release a ``strands``-condition hold. Not
#: ``spawn_queued`` (admission, nothing to read yet) and never
#: ``schedule`` (a recurring tick is not a child reporting back).
STRAND_RELEASE_SOURCES = frozenset({
    "spawn_submitted",
    "spawn_completed",
    "spawn_allowance_requested",
})

#: Whether a resume can be a real native-session continuation
#: (``codex exec resume <thread-id>``) or must be an honestly-labelled cold
#: restart — "Unknown or unsupported native resume capability must be
#: explicit, with a concrete boundary rather than a silent cold boot."
RESUME_NATIVE = "native"
RESUME_UNSUPPORTED = "unsupported"


def _stamp(now: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if now is None else now))


def build(
    *,
    reason: str,
    provider: str,
    detail: str | None = None,
    native_session_id: str | None = None,
    resume_kind: str = RESUME_UNSUPPORTED,
    resume_condition: str = RESUME_OPERATOR,
    reset_deadline: float | None = None,
    conversation_key: str = "",
    generation: int = 1,
    now: float | None = None,
    quota: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A fresh ``resource_hold`` record for ``Run.meta``.

    *quota* rides only on a :data:`RESUME_REFILL` hold: the reading that
    armed it (``binding_remaining_pct``), the two floors it was judged
    against (``starve_floor_pct`` / ``refill_floor_pct``), and the
    ``runner`` / ``model`` whose bucket binds — so the refill check reads
    the same bucket that starved.

    ``resume_condition`` outside :data:`RESUME_CONDITIONS` is folded to
    :data:`RESUME_OPERATOR` — an unrecognised condition must degrade to the
    conservative, always-safe choice, never silently accept a typo as
    "release on anything."
    """
    if resume_condition not in RESUME_CONDITIONS:
        resume_condition = RESUME_OPERATOR
    if resume_condition not in (RESUME_RESET, RESUME_REFILL):
        reset_deadline = None
    if resume_condition != RESUME_REFILL:
        quota = None
    return {
        "reason": reason,
        "provider": provider,
        "detail": detail,
        "native_session_id": native_session_id,
        "resume_kind": resume_kind if native_session_id else RESUME_UNSUPPORTED,
        "resume_condition": resume_condition,
        "reset_deadline": reset_deadline,
        "conversation_key": conversation_key,
        "armed_at": _stamp(now),
        "generation": int(generation),
        "released": False,
        "released_at": None,
        "released_by": None,
        "accumulated_event_ids": [],
        "quota": dict(quota) if quota else None,
    }


def is_active(meta: dict[str, Any] | None) -> bool:
    """Whether *meta* names a hold still awaiting its resume."""
    return bool(meta) and not meta.get("released")


def mark_released(meta: dict[str, Any], *, by: str, now: float | None = None) -> dict[str, Any]:
    """Return a copy of *meta* stamped as released — never mutates in place.

    Idempotent by construction: a caller that races a second release sees
    ``released`` already ``True`` and ``is_active`` already ``False``, so
    "explicit resume consumes the hold once" is a property of the read, not
    of this function needing a lock.
    """
    updated = dict(meta)
    updated["released"] = True
    updated["released_by"] = by
    updated["released_at"] = _stamp(now)
    return updated


def accumulate_event(meta: dict[str, Any], event_id: str) -> dict[str, Any]:
    """Record *event_id* as folded into this hold rather than dispatched.

    Append-only, de-duplicated — a re-deferred event (the main loop revisits
    an already-deferred candidate on a later tick) must not grow the list
    once per tick.
    """
    updated = dict(meta)
    ids = list(updated.get("accumulated_event_ids") or [])
    if event_id not in ids:
        ids.append(event_id)
    updated["accumulated_event_ids"] = ids
    return updated


def reset_condition_met(meta: dict[str, Any] | None, *, now: float | None = None) -> bool:
    """Whether a measured provider reset has passed for a ``reset``-condition hold.

    Always ``False`` for an ``operator``-condition hold, and for a
    ``reset``-condition hold with no captured deadline (an explicit,
    honest "cannot self-release" rather than treating an absent number as
    zero, which would release immediately).
    """
    if not is_active(meta):
        return False
    if (meta or {}).get("resume_condition") != RESUME_RESET:
        return False
    deadline = (meta or {}).get("reset_deadline")
    if deadline is None:
        return False
    try:
        deadline = float(deadline)
    except (TypeError, ValueError):
        return False
    timestamp = time.time() if now is None else now
    return timestamp >= deadline


def refill_floor_pct(meta: dict[str, Any] | None) -> float | None:
    """The remaining-percent a :data:`RESUME_REFILL` hold thaws at, or ``None``."""
    quota = (meta or {}).get("quota")
    if not isinstance(quota, dict):
        return None
    try:
        return float(quota.get("refill_floor_pct"))
    except (TypeError, ValueError):
        return None


def refill_condition_met(
    meta: dict[str, Any] | None, remaining_pct: float | None,
) -> bool:
    """Whether a fresh *remaining_pct* reading thaws a ``refill``-condition hold.

    ``False`` for any other condition, for an inactive hold, for a reading
    the daemon could not prove (``None`` — "no evidence of a refill" is not
    a refill), and below the floor the record was armed with.
    """
    if not is_active(meta):
        return False
    if (meta or {}).get("resume_condition") != RESUME_REFILL:
        return False
    floor = refill_floor_pct(meta)
    if floor is None or remaining_pct is None:
        return False
    try:
        return float(remaining_pct) >= floor
    except (TypeError, ValueError):
        return False


def refuses_correspondent(meta: dict[str, Any] | None) -> bool:
    """Whether a correspondent message alone leaves this hold armed.

    Only a :data:`RESUME_REFILL` hold — every other condition treats the
    operator's word as the release that outranks the wait.
    """
    return is_active(meta) and (meta or {}).get("resume_condition") == RESUME_REFILL


def schedule_event_releases(meta: dict[str, Any] | None, event: dict[str, Any] | None) -> bool:
    """Whether a ``schedule`` firing wakes this hold — only on the ``any`` condition.

    A tick is a reason to wake a parked seat (design-the-seat-that-never-quits.md:
    "a tick is a reason to wake, not a new life"); under every other
    condition it accumulates as before.
    """
    if not is_active(meta) or (meta or {}).get("resume_condition") != RESUME_ANY:
        return False
    return bool(event) and str(event.get("source") or "") == "schedule"


def strand_event_releases(
    meta: dict[str, Any] | None,
    event: dict[str, Any] | None,
    *,
    held_run_id: str,
    child_run_ids: Any = (),
) -> bool:
    """Whether *event* is one of the held run's own strands reporting back.

    ``False`` for any hold not on the ``strands`` condition, for a source
    outside :data:`STRAND_RELEASE_SOURCES`, and for a child event whose
    parent is some *other* run — a sibling seat's strand finishing must not
    wake a seat that never dispatched it. Parentage is read from the event
    (``spawn_parent_run_id``, the field ``spawn_*`` events carry) first,
    then from the run's own ``child_run_ids`` (``spawned_by_run``), so an
    adopted or re-parented child still counts.
    """
    if not is_active(meta) or (meta or {}).get("resume_condition") not in (RESUME_STRANDS, RESUME_ANY):
        return False
    if not event:
        return False
    if str(event.get("source") or "") not in STRAND_RELEASE_SOURCES:
        return False
    owner = str(held_run_id or "").strip()
    if not owner:
        return False
    if str(event.get("spawn_parent_run_id") or "").strip() == owner:
        return True
    child = str(event.get("spawned_by_run") or event.get("spawn_run_id") or "").strip()
    if not child:
        return False
    if isinstance(child_run_ids, str):
        known = {part.strip() for part in child_run_ids.split(",")}
    else:
        known = {str(part).strip() for part in (child_run_ids or ())}
    return child in known


def hold_boot_ratio(
    hold_so_far: "int | float | None", boot_cost: "int | float | None",
) -> "float | None":
    """``hold_so_far / boot_cost``, or ``None`` when either side is unknown.

    The rule this closes (design-the-seat-that-never-quits.md §"The
    machinery, in slices" #3): a seat idling on ``brnrd await`` parks itself
    once holding has cost more than a boot. Both terms are weighted tokens —
    *hold_so_far* is this run's own live-metered spend accrued since the
    wait last had nothing pending (the caller's baseline bookkeeping, not
    this function's job); *boot_cost* is ``spend.json``'s recorded
    ``boot.weighted`` (brnrd#1816, ``daemon._record_boot_cost``).

    ``None`` on either missing input — never a fabricated ratio. A caller
    with no boot cost yet (Codex, or a Claude transcript with no usage row)
    must read that as "no ratio, no park", not as zero cost.
    """
    if hold_so_far is None or boot_cost is None or boot_cost <= 0:
        return None
    return float(hold_so_far) / float(boot_cost)


def portal_projection(meta: dict[str, Any] | None) -> dict[str, Any] | None:
    """The subset of a hold record safe/useful to publish on portal-state.

    A thin passthrough today — every field here is already meant for the
    correspondent's eyes (design-the-continuous-seat.md: "the UI shows why
    the seat is waiting and the one action that would release it") — kept
    as its own function so a future redaction need has one call site to
    change rather than every ``_write_live_portal_state`` caller.
    """
    if not meta:
        return None
    return dict(meta)

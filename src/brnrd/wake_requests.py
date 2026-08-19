"""#328 tap-to-request — server-side lifecycle of a spool-rack tap.

One shared module so the two surfaces stay in lockstep: the dashboard
(``brnrd/routers/dashboard.py``: mint / cancel / render the chip) and
the daemon (``routers/daemons.py``: ``put_runners`` piggybacks the
pending request onto the catalog publish response; ``claim_wake_request``
decides its fate).

State machine: ``pending`` → ``consumed`` (a wake claimed it and will run
on the requested profile) | ``canceled`` (chip tap) | ``expired``
(lazily, on read or on claim — no sweeper). One pending request per
account: a new tap supersedes the old one rather than queueing.

**This module is the only opinion about a tap's lifecycle** (#733). The
daemon used to keep its own: a 900 s mirror TTL and a 120 s claim window,
both invisible to the dashboard chip that truthfully reported the 24 h
row TTL, and the smallest horizon decided. A local replica of a fact its
source owns can disagree with that source, and did — twice, live. So
:func:`claim` answers every rung in one transaction and the daemon keeps
no second opinion; ``src/brr/wake_request.py`` is now a presence bit and
a receipt, nothing more.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import ids
from .models import RunnerWakeRequest

# A tap means "the next wake" — if no wake fires for a day, the intent has
# gone stale and a silent flip days later would surprise more than help.
WAKE_REQUEST_TTL_S = 24 * 3600

# #1492: a tap refused by the "parked after this event existed" rung this
# many times stops losing to older events — the next claim applies it
# outright, however the age comparison reads. Bounds a starvation the rung
# has no other bound on: a re-queued event can survive in the dispatch queue
# for hours, and every survival hour is another refusal for any tap that
# happened to park behind it. Two, not one: a single refusal is the rung
# working as designed (the very next wake after a fresh tap is often the one
# it was parked for), so the bound has to separate "lost the normal race
# once" from "is being starved."
PARKED_AFTER_REFUSAL_BOUND = 2


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def view(row: RunnerWakeRequest) -> dict:
    created = _aware(row.created_at)
    return {
        "request_id": row.id,
        "profile": row.profile,
        "repo_label": row.repo_label,
        "environment": row.environment,
        "requested_at": created.isoformat() if created else None,
        "status": row.status,
        # Only meaningful while pending — a decided row's story is in the
        # claim/cancel response, not this mirror.
        "blocked_reason": row.blocked_reason if row.status == RunnerWakeRequest.STATUS_PENDING else None,
    }


def pending_for_account(db: Session, account_id: str) -> RunnerWakeRequest | None:
    """Newest pending request, lazily expiring anything past its TTL."""
    rows = (
        db.execute(
            select(RunnerWakeRequest)
            .where(
                RunnerWakeRequest.account_id == account_id,
                RunnerWakeRequest.status == RunnerWakeRequest.STATUS_PENDING,
            )
            .order_by(RunnerWakeRequest.created_at.desc())
        )
        .scalars()
        .all()
    )
    now = datetime.now(timezone.utc)
    newest: RunnerWakeRequest | None = None
    dirty = False
    for row in rows:
        expires = _aware(row.expires_at)
        if expires is not None and expires < now:
            row.status = RunnerWakeRequest.STATUS_EXPIRED
            row.decided_at = now
            dirty = True
            continue
        if newest is None:
            newest = row
    if dirty:
        db.commit()
    return newest


def create(
    db: Session,
    account_id: str,
    profile: str,
    *,
    repo_label: str | None = None,
    environment: str | None = None,
) -> RunnerWakeRequest:
    """Mint a pending request, superseding any earlier pending one."""
    now = datetime.now(timezone.utc)
    existing = (
        db.execute(
            select(RunnerWakeRequest).where(
                RunnerWakeRequest.account_id == account_id,
                RunnerWakeRequest.status == RunnerWakeRequest.STATUS_PENDING,
            )
        )
        .scalars()
        .all()
    )
    for row in existing:
        row.status = RunnerWakeRequest.STATUS_CANCELED
        row.decided_at = now
    row = RunnerWakeRequest(
        id=ids.runner_wake_request_id(),
        account_id=account_id,
        profile=profile,
        repo_label=repo_label,
        environment=environment,
        status=RunnerWakeRequest.STATUS_PENDING,
        expires_at=now + timedelta(seconds=WAKE_REQUEST_TTL_S),
    )
    db.add(row)
    db.commit()
    return row


def cancel(db: Session, account_id: str, request_id: str) -> RunnerWakeRequest | None:
    """Cancel a pending request; a decided row is returned as-is.

    Returning the already-consumed row (rather than 409ing) lets the UI say
    "that wake already fired" instead of erroring — the race between a tap's
    cancel and a dispatching daemon is inherent and ~seconds wide.
    """
    row = db.get(RunnerWakeRequest, request_id)
    if row is None or row.account_id != account_id:
        return None
    if row.status == RunnerWakeRequest.STATUS_PENDING:
        row.status = RunnerWakeRequest.STATUS_CANCELED
        row.decided_at = datetime.now(timezone.utc)
        db.commit()
    return row


def _refuse(row: RunnerWakeRequest | None, request_id: str, reason: str) -> dict:
    return {
        "apply": False,
        "reason": reason,
        "request_id": request_id,
        "status": row.status if row is not None else "absent",
        "profile": row.profile if row is not None else None,
        "repo_label": row.repo_label if row is not None else None,
        "environment": row.environment if row is not None else None,
    }


def claim(
    db: Session,
    account_id: str,
    *,
    request_id: str,
    event_id: str | None = None,
    source: str | None = None,
    event_created: datetime | None = None,
    daemon_now: datetime | None = None,
    known_profiles: set[str] | None = None,
) -> dict:
    """Decide, in one transaction, whether a dispatching wake spends a tap.

    #733: the whole guard ladder, at one place, on the side that owns the
    row. The daemon calls this once at dispatch (only when its presence-bit
    mirror says a tap exists) and does what it is told — it no longer holds
    any rule of its own about expiry, staleness, or profile availability.

    The rungs, and crucially *what each one leaves behind*:

    - **already decided** — consumed / canceled / expired. No change; the
      daemon's mirror is simply one publish tick stale, which after #733 is
      the *only* staleness left and it is now harmless.
    - **expired** (``expires_at`` past) — moved to ``STATUS_EXPIRED`` here.
      This is the rung whose local counterpart was invisible: a lapsed tap
      lands in ``expired``, **never** in ``consumed``, so the chip and the
      row agree about what happened to it.
    - **``schedule`` source** (#564) — refused, row **stays pending**. A tap
      is a promise to the next wake the account owner is about to *cause*;
      a director tick or an ``every:`` firing isn't that wake, so it must
      not spend the tap — and must not burn it either.
    - **unknown profile** — refused, row **stays pending**. A drop is not a
      spend: the rack this daemon published no longer carries the profile
      that was tapped, and the 24 h TTL (or a chip tap) reclaims the row.
    - **parked after the event existed** (#577's one surviving rule) — the
      daemon's mirror lags its source by up to a publish tick, so a tap
      minted seconds ago may not be on the daemon's disk yet. A tap parked
      *after* the event's own ``created`` stamp was parked for a wake that
      hasn't happened yet, so it stays pending for that one. The lag and the
      rule are the same rule.

      Judged as **ages, not instants**: the tap's age comes off the server's
      clock (``now - created_at``) and the event's off the daemon's
      (``daemon_now - event_created``), so each subtraction happens within
      one machine and the comparison never crosses clocks. Comparing the two
      absolute stamps instead is skew-sensitive in the one direction that
      matters — a daemon clock a few seconds behind the server would make
      *every* tap look parked-after, so the tap would be deferred not once
      but forever, which reads exactly like the invisible expiry #733 is
      about. ``daemon_now`` absent (an older daemon) ⇒ the rung is skipped
      rather than guessed: this module never silently drops a tap.

      **Bounded** (#1492): a tap loses this rung no more than
      :data:`PARKED_AFTER_REFUSAL_BOUND` times. A re-queued event can sit in
      the dispatch queue for hours with nothing to age it out on its own, so
      an unbounded rung starves any tap that happened to park behind one —
      refused fresh at every wake in between, for as long as the event
      survives. Past the bound the rung stops asserting itself and the claim
      falls through to apply. The healthy case is unaffected: the bound only
      trips on *repeat* refusal, so a freshly parked tap still loses the
      ordinary first race exactly as before.

    Any rung that leaves the row **pending** (schedule-source, unknown
    profile, or a parked-after-event refusal still under its bound) stamps
    ``row.blocked_reason`` with why, so the tap's story survives between
    refusals instead of the row just going quiet — the account's
    tap-parked-here surface (``view()``) reads it back (#1492's "make the
    deferral visible" half).

    Anything left standing applies: the row goes ``consumed`` here, in this
    transaction, before the daemon has done anything with the answer.
    ``event_id`` is recorded only in the daemon's local receipt (the row has
    no column for it) — it rides the payload so the server-side log of a
    claim can name the wake that made it.
    """
    request_id = str(request_id or "").strip()
    if not request_id:
        return _refuse(None, request_id, "claim carried no request id")
    row = db.get(RunnerWakeRequest, request_id)
    if row is None or row.account_id != account_id:
        return _refuse(None, request_id, "no such wake request")
    if row.status != RunnerWakeRequest.STATUS_PENDING:
        return _refuse(row, request_id, f"the tap is already {row.status}")

    now = datetime.now(timezone.utc)
    expires = _aware(row.expires_at)
    if expires is not None and expires < now:
        row.status = RunnerWakeRequest.STATUS_EXPIRED
        row.decided_at = now
        db.commit()
        return _refuse(row, request_id, "the tap expired before a wake claimed it")

    def _defer(reason: str) -> dict:
        """Refuse while the row stays pending, and remember why (#1492) —
        the tap's story survives to the next refusal (or the next reader of
        ``view()``) instead of the row just going quiet."""
        row.blocked_reason = reason
        db.commit()
        return _refuse(row, request_id, reason)

    if str(source or "") == "schedule":
        return _defer("a schedule-source wake never spends a dashboard tap")
    if known_profiles is not None and row.profile not in known_profiles:
        return _defer(
            f"profile '{row.profile}' is not in this daemon's published rack"
        )
    created = _aware(row.created_at)
    event_at = _aware(event_created)
    daemon_at = _aware(daemon_now)
    if created is not None and event_at is not None and daemon_at is not None:
        tap_age = (now - created).total_seconds()
        event_age = (daemon_at - event_at).total_seconds()
        if tap_age < event_age and row.parked_after_refusals < PARKED_AFTER_REFUSAL_BOUND:
            row.parked_after_refusals += 1
            return _defer(
                "the tap was parked after this event existed; it is for the next wake"
            )

    row.status = RunnerWakeRequest.STATUS_CONSUMED
    row.decided_at = now
    row.blocked_reason = None
    db.commit()
    return {
        "apply": True,
        "reason": None,
        "request_id": request_id,
        "status": row.status,
        "profile": row.profile,
        "repo_label": row.repo_label,
        "environment": row.environment,
    }

"""#476 wyrd §3 — server-side lifecycle of a user-issued run stop.

The user-facing half of the ``stop:`` mechanism PR #461 shipped. That PR
built the kill; what it could not build is the *button*, because a browser
has no path into a daemon's process table. Same shape of problem as #328's
tap-to-request, so the same shape of answer, and this module is deliberately
a close sibling of ``brnrd/wake_requests.py``: park a row, hand it down on
the daemon's next sync, retire it when the daemon acks.

State machine: ``pending`` → ``consumed`` (a daemon dispatched the kill and
acked it) | ``expired`` (lazily, on read — no sweeper). There is no
``canceled``: see ``models.RunStopRequest``.

The delivery tick is ``PUT /v1/daemons/live-runs`` (``routers/daemons.py::
put_live_runs``) rather than the runner-catalog publish — that endpoint is
already where a daemon reports which runs are burning, which makes it the
one place both halves of "which run, and stop it" are in hand at once.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import ids
from .models import RunStopRequest

# A stop names a run the user can see burning *now*. If no daemon consumes
# it within this window the run has almost certainly ended on its own, and
# a stop landing on a much later run that happens to reuse the handle would
# be worse than the tap silently going stale. Deliberately far shorter than
# WAKE_REQUEST_TTL_S — a wake request is about the future, a stop is about
# a process that exists this minute.
RUN_STOP_REQUEST_TTL_S = 15 * 60


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def view(row: RunStopRequest) -> dict:
    created = _aware(row.created_at)
    return {
        "request_id": row.id,
        "run_id": row.run_id,
        "requested_at": created.isoformat() if created else None,
        "status": row.status,
    }


def pending_for_account(db: Session, account_id: str) -> list[RunStopRequest]:
    """Every pending stop for this account, lazily expiring stale rows.

    A list, not the single newest row: unlike a wake request (one account,
    one "next wake"), two runs can burn concurrently and each may have its
    own pending stop.
    """
    rows = (
        db.execute(
            select(RunStopRequest)
            .where(
                RunStopRequest.account_id == account_id,
                RunStopRequest.status == RunStopRequest.STATUS_PENDING,
            )
            .order_by(RunStopRequest.created_at.asc())
        )
        .scalars()
        .all()
    )
    now = datetime.now(timezone.utc)
    live: list[RunStopRequest] = []
    dirty = False
    for row in rows:
        expires = _aware(row.expires_at)
        if expires is not None and expires < now:
            row.status = RunStopRequest.STATUS_EXPIRED
            row.decided_at = now
            dirty = True
            continue
        live.append(row)
    if dirty:
        db.commit()
    return live


def pending_run_ids(db: Session, account_id: str) -> set[str]:
    """Run handles with a stop in flight — what the UI renders as "stopping"."""
    return {row.run_id for row in pending_for_account(db, account_id)}


def create(db: Session, account_id: str, run_id: str) -> RunStopRequest:
    """Park a pending stop for *run_id*, idempotent per run.

    A second tap on a run that already has one in flight returns the
    existing row rather than minting a duplicate: the intent is identical,
    and two rows would mean two acks for one kill.
    """
    existing = (
        db.execute(
            select(RunStopRequest).where(
                RunStopRequest.account_id == account_id,
                RunStopRequest.run_id == run_id,
                RunStopRequest.status == RunStopRequest.STATUS_PENDING,
            )
        )
        .scalars()
        .first()
    )
    now = datetime.now(timezone.utc)
    if existing is not None:
        expires = _aware(existing.expires_at)
        if expires is None or expires >= now:
            return existing
        existing.status = RunStopRequest.STATUS_EXPIRED
        existing.decided_at = now
    row = RunStopRequest(
        id=ids.run_stop_request_id(),
        account_id=account_id,
        run_id=run_id,
        status=RunStopRequest.STATUS_PENDING,
        expires_at=now + timedelta(seconds=RUN_STOP_REQUEST_TTL_S),
    )
    db.add(row)
    db.commit()
    return row


def mark_consumed(db: Session, account_id: str, request_ids: list[str]) -> None:
    """Daemon ack: these stops were dispatched into the kill path."""
    if not request_ids:
        return
    now = datetime.now(timezone.utc)
    dirty = False
    for request_id in request_ids:
        row = db.get(RunStopRequest, str(request_id))
        if row is None or row.account_id != account_id:
            continue
        if row.status == RunStopRequest.STATUS_PENDING:
            row.status = RunStopRequest.STATUS_CONSUMED
            row.decided_at = now
            dirty = True
    if dirty:
        db.commit()

"""the-parked-seat-has-two-buttons — server-side lifecycle of a user-issued
run *release*.

Close sibling of ``run_stop_requests.py`` (#476 wyrd §3) — same park /
serve / ack state machine, same reasoning throughout. A release targets a
*held* seat rather than a burning one: no process exists to kill, only a
``resource_hold`` record for the daemon to mark released and a run to end.

State machine: ``pending`` → ``consumed`` (a daemon marked the hold
released) | ``expired`` (lazily, on read). No ``canceled`` — see
``models.RunReleaseRequest``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import ids
from .models import RunReleaseRequest

# Same reasoning as `run_stop_requests.RUN_STOP_REQUEST_TTL_S`: a release
# names a seat the user can see parked *now*; a daemon that hasn't consumed
# it within this window has almost certainly already resumed or ended that
# seat some other way.
RUN_RELEASE_REQUEST_TTL_S = 15 * 60


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def view(row: RunReleaseRequest) -> dict:
    created = _aware(row.created_at)
    return {
        "request_id": row.id,
        "run_id": row.run_id,
        "requested_at": created.isoformat() if created else None,
        "status": row.status,
    }


def pending_for_account(db: Session, account_id: str) -> list[RunReleaseRequest]:
    """Every pending release for this account, lazily expiring stale rows."""
    rows = (
        db.execute(
            select(RunReleaseRequest)
            .where(
                RunReleaseRequest.account_id == account_id,
                RunReleaseRequest.status == RunReleaseRequest.STATUS_PENDING,
            )
            .order_by(RunReleaseRequest.created_at.asc())
        )
        .scalars()
        .all()
    )
    now = datetime.now(timezone.utc)
    live: list[RunReleaseRequest] = []
    dirty = False
    for row in rows:
        expires = _aware(row.expires_at)
        if expires is not None and expires < now:
            row.status = RunReleaseRequest.STATUS_EXPIRED
            row.decided_at = now
            dirty = True
            continue
        live.append(row)
    if dirty:
        db.commit()
    return live


def pending_run_ids(db: Session, account_id: str) -> set[str]:
    """Run handles with a release in flight — what the UI renders as "releasing"."""
    return {row.run_id for row in pending_for_account(db, account_id)}


def create(db: Session, account_id: str, run_id: str) -> RunReleaseRequest:
    """Park a pending release for *run_id*, idempotent per run."""
    existing = (
        db.execute(
            select(RunReleaseRequest).where(
                RunReleaseRequest.account_id == account_id,
                RunReleaseRequest.run_id == run_id,
                RunReleaseRequest.status == RunReleaseRequest.STATUS_PENDING,
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
        existing.status = RunReleaseRequest.STATUS_EXPIRED
        existing.decided_at = now
    row = RunReleaseRequest(
        id=ids.run_release_request_id(),
        account_id=account_id,
        run_id=run_id,
        status=RunReleaseRequest.STATUS_PENDING,
        expires_at=now + timedelta(seconds=RUN_RELEASE_REQUEST_TTL_S),
    )
    db.add(row)
    db.commit()
    return row


def mark_consumed(db: Session, account_id: str, request_ids: list[str]) -> None:
    """Daemon ack: these releases were dispatched into the hold-release path."""
    if not request_ids:
        return
    now = datetime.now(timezone.utc)
    dirty = False
    for request_id in request_ids:
        row = db.get(RunReleaseRequest, str(request_id))
        if row is None or row.account_id != account_id:
            continue
        if row.status == RunReleaseRequest.STATUS_PENDING:
            row.status = RunReleaseRequest.STATUS_CONSUMED
            row.decided_at = now
            dirty = True
    if dirty:
        db.commit()

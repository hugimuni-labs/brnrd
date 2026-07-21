"""#328 tap-to-request — server-side lifecycle of a spool-rack tap.

One shared module so the two surfaces stay in lockstep: the dashboard
(``brnrd/routers/dashboard.py``: mint / cancel / render the chip) and
the daemon mirror (``routers/daemons.py::put_runners``: piggyback the
pending request on the catalog publish response, retire consumed ones).

State machine: ``pending`` → ``consumed`` (a wake dispatched on the
requested profile — the daemon acks it) | ``canceled`` (chip tap) |
``expired`` (lazily, on read — no sweeper). One pending request per
account: a new tap supersedes the old one rather than queueing.
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


def mark_consumed(db: Session, account_id: str, request_ids: list[str]) -> None:
    """Daemon ack: these requests were spent on a dispatched wake."""
    if not request_ids:
        return
    now = datetime.now(timezone.utc)
    dirty = False
    for request_id in request_ids:
        row = db.get(RunnerWakeRequest, str(request_id))
        if row is None or row.account_id != account_id:
            continue
        # A cancel that lost the race to a real dispatch stays truthful:
        # the wake did fire on the requested profile.
        if row.status in (
            RunnerWakeRequest.STATUS_PENDING,
            RunnerWakeRequest.STATUS_CANCELED,
        ):
            row.status = RunnerWakeRequest.STATUS_CONSUMED
            row.decided_at = now
            dirty = True
    if dirty:
        db.commit()

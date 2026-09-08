"""the-parked-seat-has-two-buttons — server-side lifecycle of a user-issued
run *respawn on another core*.

Close sibling of ``run_release_requests.py``, with one addition: the
runner the user picked (``shell``/``core``) rides the row and the served
payload, so the daemon knows what to respawn on without a second request.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import ids
from .models import RunRespawnRequest

RUN_RESPAWN_REQUEST_TTL_S = 15 * 60


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def view(row: RunRespawnRequest) -> dict:
    created = _aware(row.created_at)
    return {
        "request_id": row.id,
        "run_id": row.run_id,
        "shell": row.shell or None,
        "core": row.core or None,
        "requested_at": created.isoformat() if created else None,
        "status": row.status,
    }


def pending_for_account(db: Session, account_id: str) -> list[RunRespawnRequest]:
    """Every pending respawn for this account, lazily expiring stale rows."""
    rows = (
        db.execute(
            select(RunRespawnRequest)
            .where(
                RunRespawnRequest.account_id == account_id,
                RunRespawnRequest.status == RunRespawnRequest.STATUS_PENDING,
            )
            .order_by(RunRespawnRequest.created_at.asc())
        )
        .scalars()
        .all()
    )
    now = datetime.now(timezone.utc)
    live: list[RunRespawnRequest] = []
    dirty = False
    for row in rows:
        expires = _aware(row.expires_at)
        if expires is not None and expires < now:
            row.status = RunRespawnRequest.STATUS_EXPIRED
            row.decided_at = now
            dirty = True
            continue
        live.append(row)
    if dirty:
        db.commit()
    return live


def pending_run_ids(db: Session, account_id: str) -> set[str]:
    """Run handles with a respawn in flight — what the UI renders as "respawning"."""
    return {row.run_id for row in pending_for_account(db, account_id)}


def create(
    db: Session, account_id: str, run_id: str, *, shell: str = "", core: str = "",
) -> RunRespawnRequest:
    """Park a pending respawn for *run_id*, idempotent per run.

    A second tap replaces the pending row's runner choice rather than
    minting a duplicate — same idempotency posture as a stop/release, and
    the more useful one here: a user who taps "respawn on X" then
    immediately "respawn on Y" before the daemon's next tick means Y, not a
    second respawn.
    """
    existing = (
        db.execute(
            select(RunRespawnRequest).where(
                RunRespawnRequest.account_id == account_id,
                RunRespawnRequest.run_id == run_id,
                RunRespawnRequest.status == RunRespawnRequest.STATUS_PENDING,
            )
        )
        .scalars()
        .first()
    )
    now = datetime.now(timezone.utc)
    if existing is not None:
        expires = _aware(existing.expires_at)
        if expires is None or expires >= now:
            existing.shell = shell
            existing.core = core
            db.commit()
            return existing
        existing.status = RunRespawnRequest.STATUS_EXPIRED
        existing.decided_at = now
    row = RunRespawnRequest(
        id=ids.run_respawn_request_id(),
        account_id=account_id,
        run_id=run_id,
        shell=shell,
        core=core,
        status=RunRespawnRequest.STATUS_PENDING,
        expires_at=now + timedelta(seconds=RUN_RESPAWN_REQUEST_TTL_S),
    )
    db.add(row)
    db.commit()
    return row


def mark_consumed(db: Session, account_id: str, request_ids: list[str]) -> None:
    """Daemon ack: these respawns were dispatched into the hold-release path."""
    if not request_ids:
        return
    now = datetime.now(timezone.utc)
    dirty = False
    for request_id in request_ids:
        row = db.get(RunRespawnRequest, str(request_id))
        if row is None or row.account_id != account_id:
            continue
        if row.status == RunRespawnRequest.STATUS_PENDING:
            row.status = RunRespawnRequest.STATUS_CONSUMED
            row.decided_at = now
            dirty = True
    if dirty:
        db.commit()

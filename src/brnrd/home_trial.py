"""Account-held home trial clock. No entitlement or price changes here."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

from .models import Account
from .schemas import HomeTrialOut

TRIAL_LENGTH = timedelta(days=14)


def start_once(db: Session, account_id: str) -> None:
    """Join the caller's successful-connect transaction; concurrent connects cannot reset it."""
    db.execute(
        update(Account)
        .where(Account.id == account_id, Account.home_trial_started_at.is_(None), Account.deleted_at.is_(None))
        .values(home_trial_started_at=datetime.now(timezone.utc).replace(tzinfo=None))
    )


def status(account: Account, *, now: datetime | None = None) -> HomeTrialOut:
    started = account.home_trial_started_at
    if started is None:
        return HomeTrialOut(status="not_started")
    # Database timestamps are UTC, including SQLite's naive round-trip.
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    ends = started + TRIAL_LENGTH
    return HomeTrialOut(status="active" if (now or datetime.now(timezone.utc)) < ends else "expired",
                        started_at=started, ends_at=ends)

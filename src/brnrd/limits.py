"""Free-tier headroom limits + abuse ceilings (#501 repo cap half; account
decision ledger 2026-07-21).

Two distinct kinds of bound, deliberately kept apart:

- **Headroom limits** bind only the free tier and lift entirely for any
  account with a live subscription (the supporter tier's one concrete
  entitlement). Decided by :func:`brnrd.billing.entitlements` — enforcement
  points never inspect subscription state themselves.
- **Abuse ceilings** bind every tier, sit far above real use, and exist as
  protection, not product.

Failure posture when subscription state is unreadable (billing outage):
headroom fails *open* (a billing outage must not brick paying users'
ingress), abuse ceilings fail *closed* (they never depended on billing
state to begin with — the counts come straight from this database).

All numeric bounds live in :class:`brnrd.config.Settings` (the
``limit_*`` block); nothing here carries a scattered numeral. Counters
read real tables only: connected repos = ``repos`` rows, event rates =
``events`` rows by ``created_at`` (kept accurate well past these windows
by the #502 GC's 90-day row TTL).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import billing
from .config import Settings
from .models import Account, Event, Repo

# One line, no marketing (ledger 2026-07-21): name the limit, name the path.
_UPGRADE_HINT = "the supporter plan lifts free-tier limits (dashboard billing page)."


@dataclass(frozen=True)
class LimitDecision:
    """Outcome of one limit check.

    ``reason`` is the machine-readable handle (stable, snake_case);
    ``message`` is the one-line human text naming the limit (and, for
    free-tier headroom rejections, the upgrade path). ``status`` is the
    HTTP status an HTTP ingress should map the rejection to — platform
    webhooks ignore it and do a polite logged drop instead.
    """

    allowed: bool
    reason: str = ""
    message: str = ""
    status: int = 429


_ALLOW = LimitDecision(True)


def _naive_utcnow() -> datetime:
    # Model DateTime columns hold naive UTC (models._utcnow via SQLite).
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _entitlements(db: Session, account: Account | str | None) -> billing.Entitlements:
    """Read the enablement seam; unreadable ⇒ degraded (headroom open)."""
    try:
        return billing.entitlements(db, account)
    except Exception:
        return billing.DEGRADED_ENTITLEMENTS


def _account_event_count(db: Session, account_id: str, *, since: datetime) -> int:
    return int(
        db.execute(
            select(func.count())
            .select_from(Event)
            .join(Repo, Event.repo_id == Repo.id)
            .where(Repo.account_id == account_id, Event.created_at >= since)
        ).scalar_one()
    )


def check_event_admission(
    db: Session,
    settings: Settings,
    account: Account | None,
    *,
    body: str = "",
    attachment_count: int = 0,
) -> LimitDecision:
    """May this account enqueue one more inbox event right now?

    Order matters: abuse ceilings (payload shape, then rate) are checked
    before any billing read, so they bind identically for every tier and
    never depend on subscription state being readable.
    """
    if account is None:
        # FK integrity makes this unreachable from live ingress; if a
        # dangling row ever gets here, admitting one event beats a 500.
        return _ALLOW
    if len((body or "").encode("utf-8")) > settings.limit_max_event_body_bytes:
        kb = settings.limit_max_event_body_bytes // 1000
        return LimitDecision(
            False,
            "event_body_too_large",
            f"Message exceeds the {kb} kB per-event limit; send something shorter.",
            status=413,
        )
    if attachment_count > settings.limit_max_event_attachments:
        return LimitDecision(
            False,
            "too_many_attachments",
            f"Too many attachments (limit {settings.limit_max_event_attachments} per message).",
            status=413,
        )

    now = _naive_utcnow()
    minute_count = _account_event_count(db, account.id, since=now - timedelta(seconds=60))
    if minute_count >= settings.limit_abuse_events_per_minute:
        return LimitDecision(
            False,
            "abuse_event_rate",
            f"Account event-rate ceiling reached ({settings.limit_abuse_events_per_minute}/min); wait a moment and retry.",
        )
    day_count = _account_event_count(db, account.id, since=now - timedelta(days=1))
    if day_count >= settings.limit_abuse_events_per_day:
        return LimitDecision(
            False,
            "abuse_daily_events",
            f"Account daily event ceiling reached ({settings.limit_abuse_events_per_day}/day); retry tomorrow.",
        )

    ent = _entitlements(db, account)
    if ent.headroom_lifted:
        return _ALLOW
    if minute_count >= settings.limit_free_events_per_minute:
        return LimitDecision(
            False,
            "free_event_burst",
            f"Free-tier burst limit reached ({settings.limit_free_events_per_minute} events/min); {_UPGRADE_HINT}",
        )
    if day_count >= settings.limit_free_events_per_day:
        return LimitDecision(
            False,
            "free_daily_events",
            f"Free-tier daily event limit reached ({settings.limit_free_events_per_day}/day); {_UPGRADE_HINT}",
        )
    return _ALLOW


def check_repo_connect(db: Session, settings: Settings, account: Account) -> LimitDecision:
    """May this account connect one more (new) repo?

    Callers invoke this only when about to create a *new* ``repos`` row —
    reconnecting an already-connected repo stays idempotent and uncapped.
    """
    count = int(
        db.execute(
            select(func.count()).select_from(Repo).where(Repo.account_id == account.id)
        ).scalar_one()
    )
    if count >= settings.limit_abuse_repos:
        return LimitDecision(
            False,
            "abuse_repo_ceiling",
            f"Connected-repo ceiling reached ({settings.limit_abuse_repos} repos).",
            status=403,
        )
    ent = _entitlements(db, account)
    if not ent.headroom_lifted and count >= settings.limit_free_repos:
        return LimitDecision(
            False,
            "free_repo_limit",
            f"Free-tier repo limit reached ({settings.limit_free_repos} connected repos); {_UPGRADE_HINT}",
            status=403,
        )
    return _ALLOW


def raise_if_denied(decision: LimitDecision) -> None:
    """HTTP-ingress mapping: 4xx with a structured, machine-readable detail."""
    if decision.allowed:
        return
    raise HTTPException(
        status_code=decision.status,
        detail={"reason": decision.reason, "message": decision.message},
    )

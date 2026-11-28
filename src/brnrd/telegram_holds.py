"""Durable Telegram burst overflow, released into the inbox by the relay poll."""

from __future__ import annotations

import json
from collections.abc import Collection
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from . import inbox, limits
from .config import Settings
from .models import Account, Event, Repo, TelegramHold


def lock_account(db: Session, account_id: str) -> bool:
    # Serialize capacity checks and release on the same durable row in
    # PostgreSQL and SQLite. An account deleted in the meantime owns no hold.
    return db.execute(
        update(Account).where(Account.id == account_id, Account.deleted_at.is_(None)).values(id=Account.id)
    ).rowcount == 1


def _retry_at(db: Session, account_id: str, now: datetime, reason: str) -> datetime:
    if reason in {"free_event_burst", "abuse_event_rate"}:
        window = timedelta(minutes=1)
    elif reason in {"free_daily_events", "abuse_daily_events"}:
        window = timedelta(days=1)
    else:
        # An operator may have reduced the payload limit since acceptance.
        return now + timedelta(minutes=1)
    oldest = db.scalar(
        select(func.min(Event.created_at))
        .join(Repo, Event.repo_id == Repo.id)
        .where(Repo.account_id == account_id, Event.created_at >= now - window)
    )
    return (oldest or now) + window + timedelta(microseconds=1)


def hold(
    db: Session,
    settings: Settings,
    *,
    account_id: str,
    repo_id: str,
    body: str,
    reply_to: dict,
    attachments: list[dict],
) -> bool:
    if not lock_account(db, account_id) or db.get(Repo, repo_id, populate_existing=True) is None:
        return False
    pending = select(TelegramHold).where(TelegramHold.account_id == account_id)
    duplicate = db.scalar(
        pending.with_only_columns(TelegramHold.seq).where(
            TelegramHold.chat_id == str(reply_to["chat_id"]),
            TelegramHold.message_id == reply_to["message_id"],
        )
    )
    if duplicate is not None:
        db.commit()
        return True
    count = db.scalar(pending.with_only_columns(func.count(TelegramHold.seq)))
    if count >= settings.limit_telegram_held_events:
        return False
    now = limits.naive_utcnow()
    db.add(TelegramHold(
        account_id=account_id,
        repo_id=repo_id,
        chat_id=str(reply_to["chat_id"]),
        message_id=reply_to["message_id"],
        body=body,
        reply_to=json.dumps(reply_to),
        attachments_json=json.dumps(attachments),
        retry_at=_retry_at(db, account_id, now, "free_event_burst"),
    ))
    db.commit()
    return True


def release_due(
    db: Session, settings: Settings, account_id: str, repo_ids: Collection[str],
) -> None:
    now = limits.naive_utcnow()
    due = select(TelegramHold).where(
        TelegramHold.account_id == account_id,
        TelegramHold.repo_id.in_(repo_ids),
        TelegramHold.retry_at <= now,
    ).order_by(TelegramHold.seq)
    if db.scalar(due.with_only_columns(TelegramHold.seq).limit(1)) is None:
        return
    if not lock_account(db, account_id):
        return
    account = db.get(Account, account_id)
    for held in db.scalars(due).all():
        reply_to = inbox.decode_reply_to(held.reply_to)
        group = reply_to.get("media_group_id")
        attachments = json.loads(held.attachments_json)
        decision = limits.check_event_admission(
            db, settings, account, body=held.body,
            attachment_count=len(attachments),
            telegram_album=(held.chat_id, group) if group else None,
        )
        if not decision.allowed:
            held.retry_at = _retry_at(db, account_id, now, decision.reason)
            continue
        # Commit the album update and its hold deletion together.
        inbox.enqueue(
            db,
            repo_id=held.repo_id,
            body=held.body,
            source="telegram",
            created_at=now,
            reply_to=reply_to,
            attachments=attachments,
            media_group_id=group,
            commit=False,
        )
        db.delete(held)
        db.flush()
    db.commit()

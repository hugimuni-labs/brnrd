"""Shared helpers for projecting activity records into dashboard/API views."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

from .models import ActivityRecord


ACTIVITY_STALE_TTL = timedelta(minutes=10)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def fresh_activity_records(rows: Iterable[ActivityRecord], *, now: datetime | None = None) -> list[ActivityRecord]:
    """Keep only daemon activity rows with a recent report timestamp."""
    cutoff = _utc(now or datetime.now(timezone.utc)) - ACTIVITY_STALE_TTL
    fresh: list[ActivityRecord] = []
    for row in rows:
        reported_at = row.reported_at
        if reported_at is None:
            continue
        if _utc(reported_at) >= cutoff:
            fresh.append(row)
    return fresh


def dedupe_activity_records(rows: Iterable[ActivityRecord]) -> list[ActivityRecord]:
    """Keep the freshest row per repo/record identity.

    Daemon activity snapshots are last-write-wins per daemon token, so a repo
    can briefly accumulate the same ``record_id`` again under a new token after
    a reconnect/re-pair. The dashboard and the account activity API should
    collapse those replays rather than showing the same row multiple times.
    """
    seen: set[tuple[str, str]] = set()
    deduped: list[ActivityRecord] = []
    for row in rows:
        key = (row.repo_id, row.record_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped

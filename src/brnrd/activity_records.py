"""Shared helpers for projecting activity records into dashboard/API views."""

from __future__ import annotations

from collections.abc import Iterable

from .models import ActivityRecord


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

"""Inbox queue service — enqueue, long-poll drain, response forward.

This is the heart of the spine. The forwarder seam is where the
body leaves brnrd without being persisted: ``record_response``
stores only metadata on the event row and hands the body to a
``Forwarder`` callable (a no-op in the prototype; the real
Telegram / GitHub post in production; a capturing list in tests).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from . import ids
from .models import Event


@dataclass
class ForwardItem:
    event_id: str
    reply_to: dict[str, Any]
    body: str
    status: str


@dataclass
class CapturingForwarder:
    """Test/dev forwarder that records what would be sent to a platform.

    Stands in for the platform message — it is *not* brnrd storage;
    in production the forwarder is the live Telegram / GitHub post and
    brnrd keeps nothing of the body.
    """

    items: list[ForwardItem] = field(default_factory=list)

    def __call__(self, item: ForwardItem) -> None:
        self.items.append(item)


Forwarder = Callable[[ForwardItem], None]


def default_forwarder(item: ForwardItem) -> None:
    """Production seam. The prototype has no real platform wired, so
    this is a no-op; ``_dev/enqueue`` flows are observed via a
    capturing forwarder installed on ``app.state``."""


def _loads(blob: str) -> dict[str, Any]:
    if not blob:
        return {}
    try:
        value = json.loads(blob)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def enqueue(
    db: Session,
    *,
    project_id: str,
    body: str,
    source: str = "dev",
    reply_to: dict[str, Any] | None = None,
) -> Event:
    event = Event(
        event_id=ids.event_id(),
        project_id=project_id,
        source=source,
        body=body,
        reply_to=json.dumps(reply_to or {}),
        status=Event.STATUS_QUEUED,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def fetch_since(db: Session, project_id: str, since: int) -> list[Event]:
    return list(
        db.execute(
            select(Event)
            .where(Event.project_id == project_id, Event.seq > since)
            .order_by(Event.seq)
        ).scalars()
    )


def long_poll(
    session_factory: sessionmaker,
    project_id: str,
    since: int,
    *,
    max_wait_s: float,
    interval_s: float,
) -> list[Event]:
    """Block up to ``max_wait_s`` for events with ``seq > since``.

    Re-queries on a fresh short-lived session each tick so a commit
    from a concurrent enqueue is visible (SQLite snapshot hygiene).
    Read-only and idempotent: the cursor lives client-side, so the
    same ``since`` re-poll returns the same rows.
    """
    deadline = time.monotonic() + max(0.0, max_wait_s)
    while True:
        with session_factory() as db:
            events = fetch_since(db, project_id, since)
            for event in events:
                db.expunge(event)
        if events or time.monotonic() >= deadline:
            return events
        time.sleep(interval_s)


def record_response(
    db: Session,
    *,
    project_id: str,
    event_id: str,
    body_markdown: str,
    status: str,
    forwarder: Forwarder,
) -> Event | None:
    """Record response metadata, drop + forward the body.

    Returns the event, or None if it does not belong to this project.
    The response body is never written to the database — only its
    length, status, and end-to-end latency are kept.
    """
    event = db.execute(
        select(Event).where(
            Event.event_id == event_id, Event.project_id == project_id
        )
    ).scalar_one_or_none()
    if event is None:
        return None

    now = datetime.now(timezone.utc)
    created = event.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    event.response_status = status
    event.response_len = len(body_markdown)
    event.response_ms = int((now - created).total_seconds() * 1000)
    event.responded_at = now
    event.status = Event.STATUS_RESPONDED
    # Drop the inbound task body once the task is answered.
    event.body = None
    reply_to = _loads(event.reply_to)
    db.commit()

    forwarder(
        ForwardItem(
            event_id=event_id,
            reply_to=reply_to,
            body=body_markdown,
            status=status,
        )
    )
    return event


def event_to_dict(event: Event) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "seq": event.seq,
        "source": event.source,
        "body": event.body,
        "reply_to": _loads(event.reply_to),
        "created_at": event.created_at,
    }

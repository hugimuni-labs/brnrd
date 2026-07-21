"""Inbox queue service — enqueue, long-poll drain, response forward."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Collection

from sqlalchemy import delete, func, select, update
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
    items: list[ForwardItem] = field(default_factory=list)

    def __call__(self, item: ForwardItem) -> None:
        self.items.append(item)


Forwarder = Callable[[ForwardItem], None]


class DeliveryError(RuntimeError):
    pass


def default_forwarder(item: ForwardItem) -> None:
    pass


def make_default_forwarder(settings) -> Forwarder:
    def coerce_int(value: object) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def github_body(reply_to: dict, body: str) -> str:
        url = str(reply_to.get("html_url") or "").strip()
        if not url:
            return body
        author = str(reply_to.get("author") or "").strip()
        if author:
            return f"> Replying to [@{author}'s comment]({url})\n\n" + body
        return f"> Replying to [the source comment]({url})\n\n" + body

    def forward(item: ForwardItem) -> None:
        reply_to = item.reply_to or {}
        if reply_to.get("platform") == "telegram" and settings.telegram_bot_token:
            from .platforms import telegram
            telegram.send_message(
                settings.telegram_bot_token,
                reply_to["chat_id"],
                item.body,
                topic_id=reply_to.get("topic_id") or None,
                reply_to_message_id=reply_to.get("message_id") or None,
            )
            return

        if reply_to.get("platform") == "github" and settings.github_bot_token:
            from .platforms import github
            repo = str(reply_to.get("repo") or "")
            issue_number = coerce_int(reply_to.get("issue_number"))
            if not repo or issue_number is None:
                return
            kind = str(reply_to.get("kind") or "")
            comment_id = coerce_int(reply_to.get("comment_id"))
            pr_number = coerce_int(reply_to.get("pr_number") or reply_to.get("issue_number"))
            body = github_body(reply_to, item.body)
            if kind == "pr-review-comment" and comment_id and pr_number:
                github.post_review_reply(settings.github_bot_token, settings.github_api_base_url, settings.github_api_version, repo, pr_number, comment_id, body)
            else:
                github.post_issue_comment(settings.github_bot_token, settings.github_api_base_url, settings.github_api_version, repo, issue_number, body)

    return forward


def _loads(blob: str) -> dict[str, Any]:
    if not blob:
        return {}
    try:
        value = json.loads(blob)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def reply_to_of(event: Event) -> dict[str, Any]:
    return _loads(event.reply_to)


def _loads_list(blob: str | None) -> list[dict[str, Any]]:
    if not blob:
        return []
    try:
        value = json.loads(blob)
    except json.JSONDecodeError:
        return []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def attachments_of(event: Event) -> list[dict[str, Any]]:
    return _loads_list(event.attachments_json)


def enqueue(db: Session, *, repo_id: str, body: str, source: str = "dev", reply_to: dict[str, Any] | None = None, attachments: list[dict[str, Any]] | None = None) -> Event:
    event = Event(
        event_id=ids.event_id(),
        repo_id=repo_id,
        source=source,
        body=body,
        reply_to=json.dumps(reply_to or {}),
        attachments_json=json.dumps(attachments or []),
        status=Event.STATUS_QUEUED,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def clamp_since(db: Session, repo_id: str, since: int) -> int:
    """Guard a daemon's inbox cursor against a DB-epoch break.

    A cursor is always derived from event seqs the daemon actually
    received, so a legitimate cursor can never exceed the repo's max seq.
    One that does is from an older DB epoch (table recreated / renumbered)
    — trusting it silently skips every queued event until new traffic
    outruns the stale number. Seen live 2026-07-09: a daemon carrying
    ``since=4`` against a fresh events table swallowed seqs 1-4 (a week of
    messages, "do you hear me?" included) with no error anywhere.

    On a proven break, reset to just below the oldest still-queued event so
    the backlog delivers; skip responded husks (their bodies are nulled —
    redelivering them would spawn empty runs). No queued backlog ⇒ the max
    seq itself. The poll response's cursor then carries the healed value
    back to the daemon.
    """
    ceiling = int(db.execute(select(func.max(Event.seq)).where(Event.repo_id == repo_id)).scalar() or 0)
    if since <= ceiling:
        return since
    oldest_queued = db.execute(
        select(func.min(Event.seq)).where(Event.repo_id == repo_id, Event.status == Event.STATUS_QUEUED)
    ).scalar()
    return int(oldest_queued) - 1 if oldest_queued is not None else ceiling


def fetch_since(db: Session, repo_id: str, since: int) -> list[Event]:
    return list(
        db.execute(
            select(Event).where(Event.repo_id == repo_id, Event.seq > since).order_by(Event.seq)
        ).scalars()
    )


def long_poll(session_factory: sessionmaker, repo_id: str, since: int, *, max_wait_s: float, interval_s: float) -> list[Event]:
    deadline = time.monotonic() + max(0.0, max_wait_s)
    while True:
        with session_factory() as db:
            events = fetch_since(db, repo_id, since)
            for event in events:
                db.expunge(event)
        if events or time.monotonic() >= deadline:
            return events
        time.sleep(interval_s)


def clamp_since_many(db: Session, repo_ids: Collection[str], since: int) -> int:
    """Account-scoped variant of clamp_since over one global event cursor."""

    ids_set = set(repo_ids)
    if not ids_set:
        return 0
    ceiling = int(
        db.execute(select(func.max(Event.seq)).where(Event.repo_id.in_(ids_set))).scalar()
        or 0
    )
    if since <= ceiling:
        return since
    oldest_queued = db.execute(
        select(func.min(Event.seq)).where(
            Event.repo_id.in_(ids_set),
            Event.status == Event.STATUS_QUEUED,
        )
    ).scalar()
    return int(oldest_queued) - 1 if oldest_queued is not None else ceiling


def fetch_since_many(
    db: Session, repo_ids: Collection[str], since: int,
) -> list[Event]:
    ids_set = set(repo_ids)
    if not ids_set:
        return []
    return list(
        db.execute(
            select(Event)
            .where(Event.repo_id.in_(ids_set), Event.seq > since)
            .order_by(Event.seq)
        ).scalars()
    )


def long_poll_many(
    session_factory: sessionmaker,
    repo_ids: Collection[str],
    since: int,
    *,
    max_wait_s: float,
    interval_s: float,
) -> list[Event]:
    deadline = time.monotonic() + max(0.0, max_wait_s)
    while True:
        with session_factory() as db:
            events = fetch_since_many(db, repo_ids, since)
            for event in events:
                db.expunge(event)
        if events or time.monotonic() >= deadline:
            return events
        time.sleep(interval_s)


def _body_sha(body_markdown: str) -> str:
    return hashlib.sha256(body_markdown.encode("utf-8")).hexdigest()


def record_response(db: Session, *, repo_id: str, event_id: str, body_markdown: str, status: str, forwarder: Forwarder) -> Event | None:
    """Forward one daemon message for *event_id*; close the event on ``done``.

    The streaming protocol posts interim messages with a non-``done`` status
    (``processing``): those forward to the platform but leave the event open,
    so the terminal reply still owns the close. Only ``status="done"`` marks
    the event responded.

    A *responded* event still forwards — it dedupes instead of dropping.
    A respawn continuation run inherits its parent's ``cloud_event_id`` (that
    reuse is what keeps its replies in the same chat thread), so the parent's
    terminal ``done`` must not mute the child. The only post a closed event
    swallows is a byte-identical retry of the last forwarded body — the
    daemon-crashed-before-marking-delivered window — matched via
    ``response_sha``.

    History, both directions of the overshoot: every post used to carry
    ``done``, so the first interim closed the event and silently swallowed
    the final reply while ACKing 200 (2026-07-18). The fix was a hard
    responded-guard — which then swallowed an entire continuation run's
    output the same way: parent closed the shared event, every child post
    got 200-ACKed and dropped (2026-07-21, the mega-run loss). ACK-without-
    forward is only ever safe for an exact duplicate.
    """
    event = db.execute(select(Event).where(Event.event_id == event_id, Event.repo_id == repo_id)).scalar_one_or_none()
    if event is None:
        return None
    sha = _body_sha(body_markdown)
    if event.status == Event.STATUS_RESPONDED and sha == event.response_sha:
        # Idempotent retry of the last forwarded message: quiet ACK.
        return event

    try:
        forwarder(ForwardItem(event_id=event_id, reply_to=_loads(event.reply_to), body=body_markdown, status=status))
    except Exception as e:
        raise DeliveryError(str(e)) from e

    if event.status == Event.STATUS_RESPONDED:
        # Continuation speech into an already-closed event: forwarded above,
        # event stays closed; remember the body for retry dedupe.
        event.response_sha = sha
        event.response_len = len(body_markdown)
        db.commit()
        return event

    if status != "done":
        # Interim: forwarded, event stays open for the terminal close.
        return event

    now = datetime.now(timezone.utc)
    created = event.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    event.response_status = status
    event.response_len = len(body_markdown)
    event.response_ms = int((now - created).total_seconds() * 1000)
    event.response_sha = sha
    event.responded_at = now
    event.status = Event.STATUS_RESPONDED
    event.body = None
    # #525 — attachment pointers die with the body: nothing serves a closed
    # event's media, and the mirror stays bounded (#543).
    event.attachments_json = "[]"
    db.commit()
    return event


# ── #502 event GC — the queue is a relay, not an archive ──
#
# Responded events already null their body at close (`record_response`); the
# two leaks this sweep closes are the never-responded body (a dead queued
# event kept its full text forever) and the row itself (routing metadata
# accreting without bound). `/v1/stats/public` reads live counts of accounts
# and subscriptions — nothing derives history from event rows — so pruning
# needs no rollup.
_EVENT_BODY_TTL = timedelta(days=14)
_EVENT_ROW_TTL = timedelta(days=90)
_GC_INTERVAL_S = 3600.0
_gc_state = {"at": 0.0}


def reset_gc_throttle() -> None:
    """Test seam: allow the next gc_events call to run."""
    _gc_state["at"] = 0.0


def gc_events(db: Session, *, now: datetime | None = None, force: bool = False) -> None:
    """Opportunistic sweep, throttled process-wide to once an hour.

    Piggybacks on the activity publish (`PUT /v1/daemons/activity`) the same
    way the stale-activity delete does — any online daemon keeps the table
    bounded, and a deployment with no daemons has nothing accreting anyway.
    Deleting old rows is cursor-safe: `clamp_since` only cares about the
    per-repo max seq, and rows this old sit far below any live cursor.
    """
    tick = time.monotonic()
    if not force and tick - _gc_state["at"] < _GC_INTERVAL_S:
        return
    _gc_state["at"] = tick
    now = now or datetime.now(timezone.utc)
    db.execute(delete(Event).where(Event.created_at < now - _EVENT_ROW_TTL))
    db.execute(
        update(Event)
        .where(
            Event.status == Event.STATUS_QUEUED,
            Event.created_at < now - _EVENT_BODY_TTL,
            Event.body.is_not(None),
        )
        .values(body=None, attachments_json="[]")
    )
    db.commit()


def event_to_dict(event: Event, *, repo_label: str | None = None) -> dict[str, Any]:
    payload = {
        "event_id": event.event_id,
        "seq": event.seq,
        "source": event.source,
        "body": event.body,
        "reply_to": _loads(event.reply_to),
        "attachments": _loads_list(event.attachments_json),
        "created_at": event.created_at,
    }
    if repo_label:
        payload["repo_label"] = repo_label
    return payload

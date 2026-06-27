"""Inbox queue service — enqueue, long-poll drain, response forward."""

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


def enqueue(db: Session, *, repo_id: str, body: str, source: str = "dev", reply_to: dict[str, Any] | None = None) -> Event:
    event = Event(
        event_id=ids.event_id(),
        repo_id=repo_id,
        source=source,
        body=body,
        reply_to=json.dumps(reply_to or {}),
        status=Event.STATUS_QUEUED,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


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


def record_response(db: Session, *, repo_id: str, event_id: str, body_markdown: str, status: str, forwarder: Forwarder) -> Event | None:
    event = db.execute(select(Event).where(Event.event_id == event_id, Event.repo_id == repo_id)).scalar_one_or_none()
    if event is None:
        return None
    if event.status == Event.STATUS_RESPONDED:
        return event

    try:
        forwarder(ForwardItem(event_id=event_id, reply_to=_loads(event.reply_to), body=body_markdown, status=status))
    except Exception as e:
        raise DeliveryError(str(e)) from e

    now = datetime.now(timezone.utc)
    created = event.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    event.response_status = status
    event.response_len = len(body_markdown)
    event.response_ms = int((now - created).total_seconds() * 1000)
    event.responded_at = now
    event.status = Event.STATUS_RESPONDED
    event.body = None
    db.commit()
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

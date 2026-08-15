"""Inbox queue service — enqueue, long-poll drain, response forward."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Collection

import anyio
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from . import github_summons, ids
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
        summons_prefix = github_summons.github_summons_reply_prefix(
            str(reply_to.get("kind") or ""),
            author,
            url,
        )
        if summons_prefix is not None:
            return summons_prefix + body
        if author:
            return f"> Replying to [@{author}'s comment]({url})\n\n" + body
        return f"> Replying to [the source comment]({url})\n\n" + body

    def github_token(reply_to: dict) -> str:
        installation_id = str(reply_to.get("installation_id") or "").strip()
        if installation_id:
            from .platforms import github_app

            repo = str(reply_to.get("repo") or "")
            repo_name = repo.rsplit("/", 1)[-1] if "/" in repo else ""
            credential = github_app.installation_access_credential(
                settings,
                installation_id,
                repositories=[repo_name] if repo_name else None,
            )
            return credential["token"]
        return settings.github_bot_token

    def forward_telegram(item: ForwardItem, reply_to: dict) -> None:
        if not settings.telegram_bot_token:
            return
        # `.get`, not `[...]` — and an early return, the way both sibling
        # handlers below already do it. A bare subscript here turns a
        # routable-but-incomplete `reply_to` into a KeyError raised from
        # inside the forwarder, and the caller cannot tell that apart from
        # a platform that genuinely refused the message. An event whose
        # `reply_to` lost its chat is not a delivery *failure* — there is
        # simply nowhere to deliver, which is exactly what the github and
        # whatsapp handlers say by returning.
        chat_id = reply_to.get("chat_id")
        if not chat_id:
            return
        from .platforms import telegram
        telegram.send_message(
            settings.telegram_bot_token,
            chat_id,
            item.body,
            topic_id=reply_to.get("topic_id") or None,
            reply_to_message_id=reply_to.get("message_id") or None,
        )

    def forward_github(item: ForwardItem, reply_to: dict) -> None:
        from .platforms import github
        repo = str(reply_to.get("repo") or "")
        issue_number = coerce_int(reply_to.get("issue_number"))
        if not repo or issue_number is None:
            return
        token = github_token(reply_to)
        if not token:
            return
        kind = str(reply_to.get("kind") or "")
        comment_id = coerce_int(reply_to.get("comment_id"))
        pr_number = coerce_int(reply_to.get("pr_number") or reply_to.get("issue_number"))
        body = github_body(reply_to, item.body)
        if kind == "pr-review-comment" and comment_id and pr_number:
            github.post_review_reply(token, settings.github_api_base_url, settings.github_api_version, repo, pr_number, comment_id, body)
        else:
            github.post_issue_comment(token, settings.github_api_base_url, settings.github_api_version, repo, issue_number, body)

    def forward_whatsapp(item: ForwardItem, reply_to: dict) -> None:
        if not (settings.whatsapp_access_token and settings.whatsapp_phone_number_id):
            return
        from .platforms import whatsapp
        chat_id = reply_to.get("chat_id")
        if not chat_id:
            return
        whatsapp.send_message(
            settings.whatsapp_access_token,
            settings.whatsapp_phone_number_id,
            str(chat_id),
            item.body,
            api_base_url=settings.whatsapp_api_base_url,
            api_version=settings.whatsapp_api_version,
            reply_to_message_id=reply_to.get("message_id") or None,
        )

    # The routing table this refactor exists to introduce (#the-forwarder-
    # learns-a-table): one platform name -> handler mapping, replacing what
    # used to be an if/elif chain that grew by one clause per platform. The
    # table lives here rather than in ``platforms/__init__.py`` because each
    # handler closes over ``settings`` (credential lookup, API base URLs,
    # even a per-installation GitHub token) — the platform modules
    # themselves stay transport-only (see their own docstrings), so the
    # thing doing the *dispatching* is the thing already holding the
    # settings closure. Adding a platform is adding one entry here plus its
    # ``platforms/<name>.py`` transport module — no other call site knows
    # this table exists.
    handlers: dict[str, Callable[[ForwardItem, dict], None]] = {
        "telegram": forward_telegram,
        "github": forward_github,
        "whatsapp": forward_whatsapp,
    }

    def forward(item: ForwardItem) -> None:
        reply_to = item.reply_to or {}
        handler = handlers.get(reply_to.get("platform"))
        if handler is not None:
            handler(item, reply_to)

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


def decode_reply_to(blob: str | None) -> dict[str, Any]:
    """:func:`reply_to_of`'s decode, off the raw column value.

    For a caller that only pulled ``Event.reply_to`` itself (a scan over
    many rows, e.g. #1205's most-recently-active-conversation lookup) rather
    than a full ``Event`` — the two exist so neither has to instantiate the
    other just to reach ``_loads``.
    """
    return _loads(blob or "")


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


#: #1389 — how long a still-queued event stays eligible to absorb a later
#: item from the same Telegram album. Telegram delivers every item of one
#: send as a burst of separate webhook calls; the measured spread (7 events
#: in 3s) is well inside this, so the window is generous rather than tight
#: — a merge that *doesn't* happen because the window was too short is a
#: silent regression to today's one-event-per-photo behaviour, never a lost
#: message (matched against a **queued** row only, see below); a window
#: that's too generous just risks matching a media_group_id Telegram
#: reused, which it does not do.
_MEDIA_GROUP_MERGE_WINDOW = timedelta(seconds=15)

#: Bound on compare-and-swap retries for a concurrent album merge (see
#: ``_merge_into_open_media_group`` below). Exhausting it is not an error —
#: it degrades to "no open event found", the same outcome as a merge that
#: misses its window, so the caller mints a fresh event. Generous relative
#: to the largest real album (Telegram caps at 10 items) since a spurious
#: extra event costs nothing and a dropped attachment is the one outcome
#: this exists to rule out.
_MEDIA_GROUP_MERGE_MAX_ATTEMPTS = 12


def _find_open_media_group(
    db: Session, *, repo_id: str, media_group_id: str,
) -> Event | None:
    """The most recent still-**queued** event carrying *media_group_id*.

    Queued-only is the load-bearing choice: an event that already answered
    (``responded``) is done, and merging into it would silently discard
    whatever the reply already said. Matching against a queued row instead
    means the worst case for a merge that misses its window is *today's*
    behaviour — one more event, never a dropped attachment (the #1389
    guardrail against a daemon- or server-side fluke rendering a message
    unanswerable).
    """
    cutoff = datetime.now(timezone.utc) - _MEDIA_GROUP_MERGE_WINDOW
    candidates = db.execute(
        select(Event)
        .where(
            Event.repo_id == repo_id,
            Event.status == Event.STATUS_QUEUED,
            Event.source == "telegram",
            Event.created_at >= cutoff,
        )
        .order_by(Event.seq.desc())
    ).scalars()
    for event in candidates:
        if _loads(event.reply_to).get("media_group_id") == media_group_id:
            return event
    return None


def _merge_into_open_media_group(
    db: Session,
    *,
    repo_id: str,
    media_group_id: str,
    body: str,
    attachments: list[dict[str, Any]] | None,
) -> Event | None:
    """Fold *body* / *attachments* into the open media-group event, or
    ``None`` (no open event — the caller mints a fresh one).

    #1396 — ``_find_open_media_group``'s SELECT carries no row lock, and
    Telegram delivers each album item as its own webhook call that
    ``telegram_webhook`` (a sync ``def``) runs genuinely concurrently in
    Starlette's threadpool: two overlapping calls could both read the same
    ``attachments_json``, both append, and the later commit silently
    overwrite the earlier — one photo gone. Measured: 1-5/20 concurrent
    5-item-album trials lost an attachment before this fix
    (``tests/test_brnrd_telegram.py::test_concurrent_album_webhooks_do_not_lose_attachments``).

    ``with_for_update()`` is not the fix here: this service runs on SQLite
    in dev/test (``run_startup_migrations`` skips entirely for any
    non-Postgres dialect, so SQLite is a live target, not just a CI
    artifact) and SQLite has no row-level locking at all — a FOR UPDATE
    clause is silently dropped, which would make this file's own tests
    pass while proving nothing about the box that ran them.

    So: compare-and-swap instead of a lock. The UPDATE's WHERE re-checks
    ``attachments_json`` against the exact value just read; when two
    merges race, at most one UPDATE's WHERE still matches (rowcount 1),
    the other's matches nothing (rowcount 0, its computed merge is
    discarded, never written) and it retries against freshly re-read
    state. Atomic on every backend this service runs — a single
    row-scoped UPDATE is race-free relative to any other statement,
    SQLite and Postgres alike, with no reliance on isolation level or
    backend-specific locking syntax.
    """
    for _ in range(_MEDIA_GROUP_MERGE_MAX_ATTEMPTS):
        existing = _find_open_media_group(
            db, repo_id=repo_id, media_group_id=media_group_id,
        )
        if existing is None:
            return None
        new_body = existing.body
        if body and not (existing.body or "").strip():
            new_body = body
        new_attachments_json = existing.attachments_json
        if attachments:
            merged = attachments_of(existing)
            merged.extend(attachments)
            new_attachments_json = json.dumps(merged)
        if new_body == existing.body and new_attachments_json == existing.attachments_json:
            return existing
        result = db.execute(
            update(Event)
            .where(
                Event.seq == existing.seq,
                Event.attachments_json == existing.attachments_json,
            )
            .values(body=new_body, attachments_json=new_attachments_json)
        )
        db.commit()
        if result.rowcount == 1:
            db.refresh(existing)
            return existing
        # Lost the race: another merge landed between our read and our
        # write. Its commit is authoritative — never overwrite it with a
        # merge computed from data that's now stale. Re-read and retry.
        db.expire(existing)
    return None


def enqueue(
    db: Session,
    *,
    repo_id: str,
    body: str,
    source: str = "dev",
    reply_to: dict[str, Any] | None = None,
    attachments: list[dict[str, Any]] | None = None,
    media_group_id: str | None = None,
) -> Event:
    """Queue one inbound event, or fold it into an open Telegram album.

    *media_group_id*, when given, is Telegram's own album marker (#1389):
    a still-queued event already carrying the same marker in its
    ``reply_to`` absorbs this message's body (only if it hadn't one — a
    caption can land on any item of an album) and attachment pointers
    instead of minting a second event, so a five-photo send becomes one
    event with five attachments rather than five events with one photo
    each. No match ⇒ an ordinary new event, with the marker folded into
    its own ``reply_to`` so a *later* item in the same album can find it.
    """
    media_group_id = str(media_group_id or "").strip() or None
    if media_group_id:
        merged_event = _merge_into_open_media_group(
            db,
            repo_id=repo_id,
            media_group_id=media_group_id,
            body=body,
            attachments=attachments,
        )
        if merged_event is not None:
            return merged_event
    reply_to = dict(reply_to or {})
    if media_group_id:
        reply_to["media_group_id"] = media_group_id
    event = Event(
        event_id=ids.event_id(),
        repo_id=repo_id,
        source=source,
        body=body,
        reply_to=json.dumps(reply_to),
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
            select(Event)
            .where(
                Event.repo_id == repo_id,
                Event.seq > since,
                # A responded event is answered; its body was nulled at close
                # (`record_response`). Redelivering it produces an empty run.
                # See `_QUEUED_ONLY_RATIONALE`.
                Event.status == Event.STATUS_QUEUED,
            )
            .order_by(Event.seq)
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


_QUEUED_ONLY_RATIONALE = """Why the fetch filters on status, not just on the cursor.

The cursor is the *only* thing that used to keep an answered event off the
wire — there is no server-side delivery state, no per-daemon ack: "pending"
means `seq > since`, and `since` is an integer the daemon sends up from a
local JSON file. So the cursor is a claim about the past held by the one
party that can lose it.

Lost live on 2026-07-30: `brnrd account connect` after the Scaleway cutover
wrote `since: 0` (`brr/gates/cloud.py`), and the daemon was handed the
account's entire event table back — 339 events in one 163 ms burst, 181 of
them responded husks with `body = None`. The daemon cannot tell a replay
from a new message, so each one became a pending event, and the queue
re-dispatched a run for the burst on every tick.

`clamp_since` was written for this shape and could not help twice over:
`routers/daemons.py` only calls it when `since > 0`, and its floor is
`oldest_queued - 1` — one never-closed event low in history pins that floor
at the beginning of time.

The filter is what makes the guarantee structural: an event is delivered
while it is queued and never after it is answered, whatever the cursor says.
It is a no-op on the healthy path (the cursor is already past every
responded event) and the whole fix on the broken one."""


def fetch_since_many(
    db: Session, repo_ids: Collection[str], since: int,
    *, limit: int | None = None,
) -> list[Event]:
    """One page of an account's queued backlog, oldest first.

    *limit* bounds the page. It exists because this query had no bound at
    all until 2026-08-14, when a freshly created account home polled with
    ``since = 0`` and was handed **1,226 events in a single response** —
    every queued event across the account, back to the 90-day row TTL. The
    daemon ingested them at ~10/s for two minutes and the next wake
    inherited all of them at once.

    A page is not a fix for the cursor (that is `clamp_since_many` and the
    status filter above); it is the ceiling that makes any future cursor
    fault survivable instead of terminal. The cursor advances per page, so
    a daemon that dies mid-drain resumes where it stopped rather than
    restarting from the beginning.
    """
    ids_set = set(repo_ids)
    if not ids_set:
        return []
    stmt = (
        select(Event)
        .where(
            Event.repo_id.in_(ids_set),
            Event.seq > since,
            # See `_QUEUED_ONLY_RATIONALE` — answered events never
            # redeliver, cursor or no cursor.
            Event.status == Event.STATUS_QUEUED,
        )
        .order_by(Event.seq)
    )
    if limit is not None and limit > 0:
        stmt = stmt.limit(limit)
    return list(db.execute(stmt).scalars())


def _fetch_since_many_detached(
    session_factory: sessionmaker,
    repo_ids: Collection[str],
    since: int,
    limit: int | None = None,
) -> list[Event]:
    """Read one account-wide poll without leaking session-bound rows."""

    with session_factory() as db:
        events = fetch_since_many(db, repo_ids, since, limit=limit)
        for event in events:
            db.expunge(event)
    return events


async def long_poll_many(
    session_factory: sessionmaker,
    repo_ids: Collection[str],
    since: int,
    *,
    max_wait_s: float,
    interval_s: float,
    limit: int | None = None,
) -> list[Event]:
    """Long-poll without occupying FastAPI's worker pool while waiting.

    Only each short SQL read enters a worker thread. The interval itself is
    asynchronous, so connected daemons no longer reserve one of AnyIO's
    default 40 worker tokens for the full 25-second request.
    """

    deadline = time.monotonic() + max(0.0, max_wait_s)
    while True:
        events = await anyio.to_thread.run_sync(
            _fetch_since_many_detached,
            session_factory,
            repo_ids,
            since,
            limit,
        )
        if events or time.monotonic() >= deadline:
            return events
        await anyio.sleep(interval_s)


def _body_sha(body_markdown: str) -> str:
    return hashlib.sha256(body_markdown.encode("utf-8")).hexdigest()


#: The response status that closes an event **without forwarding anything**.
#: The daemon's ``note:`` outbox verb — a resident retiring a letter
#: deliberately, no message going out — used to be an entirely local act:
#: ``status: noted`` in a file under the account home, and nothing on the
#: wire. The server therefore kept the row ``queued`` forever.
#:
#: That is not a cosmetic gap. ``_QUEUED_ONLY_RATIONALE`` above explains that
#: the queued set is the structural guarantee against a replay — but a set
#: that only ever *grows* makes the guarantee weaker every day, because the
#: only exit was a terminal ``done`` and most retired letters never get one.
#: Measured 2026-08-14: a fresh account home polled ``since = 0`` and was
#: handed 1,226 events, the overwhelming majority of them long since read and
#: deliberately closed on some other machine. ``clamp_since``'s floor is
#: ``oldest_queued - 1``, so those same never-closed rows also pinned the
#: clamp at the beginning of time for every future re-pair.
#:
#: A noted close is silent on the platform and terminal in the database.
RESPONSE_STATUS_NOTED = "noted"


def _close_noted(db: Session, event: Event) -> Event:
    """Retire *event* server-side with no platform forward.

    Idempotent: a second post for an already-closed event is a quiet ACK,
    the same shape ``record_response`` gives a duplicate terminal body. The
    daemon retries this on every poll until it sees a 2xx, so "already
    closed" must never be an error.
    """
    if event.status == Event.STATUS_RESPONDED:
        return event
    now = datetime.now(timezone.utc)
    created = event.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    event.response_status = RESPONSE_STATUS_NOTED
    event.response_len = 0
    event.response_ms = int((now - created).total_seconds() * 1000)
    event.response_sha = _body_sha("")
    event.responded_at = now
    event.status = Event.STATUS_RESPONDED
    event.body = None
    event.attachments_json = "[]"
    db.commit()
    return event


def record_response(db: Session, *, repo_id: str, event_id: str, body_markdown: str, status: str, forwarder: Forwarder, conversation_id: str | None = None) -> Event | None:
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
    # #61 — conversation identity is set-once: adopt the daemon's reported
    # conversation_key only when the event has none yet; never overwrite an
    # existing value (git trailers stay the source of truth). Committed
    # eagerly so interim posts — which return before the done-path commit —
    # still persist it.
    if conversation_id and not event.conversation_id:
        event.conversation_id = conversation_id
        db.commit()
    if status == RESPONSE_STATUS_NOTED:
        return _close_noted(db, event)
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

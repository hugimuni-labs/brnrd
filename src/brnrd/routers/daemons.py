"""Daemon-facing inbox endpoints — register, long-poll, respond, deregister."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import inbox as inbox_service
from .. import ids, schemas
from ..auth import Principal, get_db, require_daemon
from ..models import Daemon, Event

router = APIRouter(prefix="/v1/daemons", tags=["daemons"])


@router.post("/register", response_model=schemas.DaemonRegistered)
def register(
    payload: schemas.DaemonRegister,
    principal: Principal = Depends(require_daemon),
    db: Session = Depends(get_db),
):
    project_id = principal.project_id
    caps = json.dumps(payload.capabilities)
    existing = db.execute(
        select(Daemon).where(
            Daemon.project_id == project_id,
            Daemon.daemon_name == payload.daemon_name,
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.online = True
        existing.last_seen_at = datetime.now(timezone.utc)
        existing.capabilities = caps
        existing.token_id = principal.token.id
        db.commit()
        return schemas.DaemonRegistered(daemon_id=existing.id, project_id=project_id)

    daemon = Daemon(
        id=ids.daemon_id(),
        project_id=project_id,
        token_id=principal.token.id,
        daemon_name=payload.daemon_name,
        capabilities=caps,
        online=True,
    )
    db.add(daemon)
    db.commit()
    return schemas.DaemonRegistered(daemon_id=daemon.id, project_id=project_id)


@router.get("/inbox", response_model=schemas.InboxResponse)
def inbox(
    request: Request,
    since: int | None = Query(default=None),
    wait: float | None = Query(default=None),
    principal: Principal = Depends(require_daemon),
):
    settings = request.app.state.settings
    session_factory = request.app.state.SessionLocal
    since_seq = since if since is not None else 0
    max_wait = (
        settings.inbox_long_poll_max_s
        if wait is None
        else max(0.0, min(wait, settings.inbox_long_poll_max_s))
    )
    events = inbox_service.long_poll(
        session_factory,
        principal.project_id,
        since_seq,
        max_wait_s=max_wait,
        interval_s=settings.inbox_poll_interval_s,
    )
    cursor = max((e.seq for e in events), default=since_seq)
    return schemas.InboxResponse(
        events=[schemas.EventOut(**inbox_service.event_to_dict(e)) for e in events],
        cursor=cursor,
    )


@router.post("/responses", response_model=schemas.ResponseAck)
def post_response(
    request: Request,
    payload: schemas.ResponsePost,
    principal: Principal = Depends(require_daemon),
    db: Session = Depends(get_db),
):
    forwarder = request.app.state.forwarder
    try:
        event = inbox_service.record_response(
            db,
            project_id=principal.project_id,
            event_id=payload.event_id,
            body_markdown=payload.body_markdown,
            status=payload.status,
            forwarder=forwarder,
        )
    except inbox_service.DeliveryError as e:
        # Upstream platform rejected the send (e.g. unreachable). The
        # event stays queued so the daemon can retry; signal 502 so the
        # failure isn't mistaken for a brnrd bug.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"forward to platform failed: {e}",
        ) from e
    if event is None:
        raise HTTPException(status_code=404, detail="event not found for this project")
    return schemas.ResponseAck(event_id=payload.event_id, forwarded=True)


@router.post("/card", response_model=schemas.CardAck)
def post_card(
    request: Request,
    payload: schemas.CardPost,
    principal: Principal = Depends(require_daemon),
    db: Session = Depends(get_db),
):
    """Relay a live progress card to the originating platform.

    The daemon's shared card driver decides send-vs-edit and owns the
    message id; brnrd just executes the platform call with the managed
    token, routing to the event's *own* ``reply_to`` (never a
    client-supplied target — that binding is the clamp that stops the
    relay being an open send-proxy). The card text is relayed, not
    stored. ``message_id`` absent → send a new card and return its id;
    present → edit it. A vanished card answers 409 so the driver resends.
    """
    settings = request.app.state.settings
    event = db.execute(
        select(Event).where(
            Event.event_id == payload.event_id,
            Event.project_id == principal.project_id,
        )
    ).scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=404, detail="event not found for this project")
    if event.status == Event.STATUS_RESPONDED:
        # The answer already went out; the card lifecycle is over.
        return schemas.CardAck(event_id=payload.event_id, message_id=payload.message_id)

    reply_to = inbox_service.reply_to_of(event)
    if reply_to.get("platform") != "telegram" or not settings.telegram_bot_token:
        # Unknown / unconfigured origin platform — nothing to relay.
        return schemas.CardAck(event_id=payload.event_id, message_id=None)

    from ..platforms import telegram as tg

    try:
        if payload.message_id is None:
            mid = tg.send_card(
                settings.telegram_bot_token,
                reply_to["chat_id"],
                payload.text,
                topic_id=reply_to.get("topic_id") or None,
                reply_to_message_id=reply_to.get("message_id") or None,
            )
            return schemas.CardAck(event_id=payload.event_id, message_id=mid)
        tg.edit_card(
            settings.telegram_bot_token,
            reply_to["chat_id"],
            payload.message_id,
            payload.text,
        )
        return schemas.CardAck(event_id=payload.event_id, message_id=payload.message_id)
    except tg.CardGone as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"card not editable: {e}"
        ) from e
    except Exception as e:  # noqa: BLE001 - normalize to a relay failure
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"card relay failed: {e}"
        ) from e


# A relayed pack is held in RAM only; cap the size so a daemon can't pin
# unbounded memory. Generous — real packs are KBs to low MBs.
_MAX_PACK_BYTES = 4 * 1024 * 1024


@router.post("/pack", response_model=schemas.PackRelayAck)
def post_pack(
    request: Request,
    payload: schemas.PackRelayPost,
    principal: Principal = Depends(require_daemon),
):
    """Relay a diffense review pack for a transient rendered fallback.

    The primary rich view is a user-owned gist plus ``GET /r?pack=...``.
    This endpoint remains for private / no-gist cases: brnrd stashes the
    pack in a RAM-only, TTL-bounded store behind an unguessable token and
    renders it on ``GET /r/{token}`` — it is **never** written to the
    database or disk (``kb/design-diffense.md`` → "Where packs live": the
    pack stays the producer's; brnrd is a transient relay, never a store).
    """
    blob = json.dumps(payload.pack, separators=(",", ":"))
    if len(blob.encode("utf-8")) > _MAX_PACK_BYTES:
        raise HTTPException(status_code=413, detail="review pack too large to relay")
    store = request.app.state.pack_relay
    token, expires_at = store.put(payload.pack, ttl_s=payload.ttl_s)
    base = request.app.state.settings.public_base_url.rstrip("/")
    return schemas.PackRelayAck(
        token=token, render_url=f"{base}/r/{token}", expires_at=expires_at,
    )


@router.post("/deregister")
def deregister(
    payload: schemas.DaemonDeregister,
    principal: Principal = Depends(require_daemon),
    db: Session = Depends(get_db),
):
    daemon = db.execute(
        select(Daemon).where(
            Daemon.project_id == principal.project_id,
            Daemon.daemon_name == payload.daemon_name,
        )
    ).scalar_one_or_none()
    if daemon is not None:
        daemon.online = False
        daemon.last_seen_at = datetime.now(timezone.utc)
        db.commit()
    return {"ok": True}

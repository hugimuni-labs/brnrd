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
from ..models import Daemon

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

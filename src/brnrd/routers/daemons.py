"""Daemon-facing inbox endpoints — register, long-poll, respond, deregister."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import ids, inbox as inbox_service, schemas
from ..auth import Principal, get_db, require_daemon
from ..models import Account, ActivityRecord, Daemon, Event, Repo

router = APIRouter(prefix="/v1/daemons", tags=["daemons"])


def _touch_daemon(db: Session, principal: Principal) -> None:
    daemon = db.execute(
        select(Daemon).where(
            Daemon.repo_id == principal.repo_id,
            Daemon.token_id == principal.token.id,
        )
    ).scalar_one_or_none()
    if daemon is None:
        return
    daemon.online = True
    daemon.last_seen_at = datetime.now(timezone.utc)
    db.commit()


def _current_daemon(db: Session, principal: Principal) -> Daemon | None:
    return db.execute(
        select(Daemon).where(
            Daemon.repo_id == principal.repo_id,
            Daemon.token_id == principal.token.id,
        )
    ).scalar_one_or_none()


def _activity_out(row: ActivityRecord) -> schemas.ActivityRecordOut:
    try:
        runner = json.loads(row.runner_json or "{}")
    except ValueError:
        runner = {}
    try:
        links = json.loads(row.links_json or "{}")
    except ValueError:
        links = {}
    return schemas.ActivityRecordOut(
        id=row.record_id,
        repo_id=row.repo_id,
        kind=row.kind,
        source=row.source,
        conversation_key=row.conversation_key,
        summary=row.summary,
        runner=runner if isinstance(runner, dict) else {},
        status=row.status,
        phase=row.phase,
        branch=row.branch,
        pr_number=row.pr_number,
        started_at=row.started_at,
        updated_at=row.updated_at,
        scheduled_for=row.scheduled_for,
        defer_until=row.defer_until,
        links=links if isinstance(links, dict) else {},
        reported_at=row.reported_at,
    )


@router.post("/register", response_model=schemas.DaemonRegistered)
def register(payload: schemas.DaemonRegister, principal: Principal = Depends(require_daemon), db: Session = Depends(get_db)):
    repo_id = principal.repo_id
    caps = json.dumps(payload.capabilities)
    existing = db.execute(select(Daemon).where(Daemon.repo_id == repo_id, Daemon.daemon_name == payload.daemon_name)).scalar_one_or_none()
    if existing is not None:
        existing.online = True
        existing.last_seen_at = datetime.now(timezone.utc)
        existing.capabilities = caps
        existing.token_id = principal.token.id
        db.commit()
        return schemas.DaemonRegistered(daemon_id=existing.id, repo_id=repo_id)
    daemon = Daemon(id=ids.daemon_id(), repo_id=repo_id, token_id=principal.token.id, daemon_name=payload.daemon_name, capabilities=caps, online=True)
    db.add(daemon)
    db.commit()
    return schemas.DaemonRegistered(daemon_id=daemon.id, repo_id=repo_id)


@router.put("/activity", response_model=schemas.ActivityList)
def put_activity(payload: schemas.ActivityReport, principal: Principal = Depends(require_daemon), db: Session = Depends(get_db)):
    """Replace this daemon token's current Activity snapshot for its repo."""
    daemon = _current_daemon(db, principal)
    now = datetime.now(timezone.utc)
    db.execute(
        delete(ActivityRecord).where(
            ActivityRecord.repo_id == principal.repo_id,
            ActivityRecord.token_id == principal.token.id,
        )
    )
    rows: list[ActivityRecord] = []
    seen: set[str] = set()
    for record in payload.records:
        if record.id in seen:
            continue
        seen.add(record.id)
        row = ActivityRecord(
            id=ids.activity_id(),
            repo_id=principal.repo_id,
            token_id=principal.token.id,
            daemon_id=daemon.id if daemon else None,
            record_id=record.id,
            kind=record.kind,
            source=record.source,
            conversation_key=record.conversation_key,
            summary=record.summary,
            runner_json=json.dumps(record.runner, separators=(",", ":")),
            status=record.status,
            phase=record.phase,
            branch=record.branch,
            pr_number=None if record.pr_number is None else str(record.pr_number),
            started_at=record.started_at,
            updated_at=record.updated_at,
            scheduled_for=record.scheduled_for,
            defer_until=record.defer_until,
            links_json=json.dumps(record.links, separators=(",", ":")),
            reported_at=now,
        )
        db.add(row)
        rows.append(row)
    if daemon is not None:
        daemon.online = True
        daemon.last_seen_at = now
    db.commit()
    return schemas.ActivityList(activity=[_activity_out(row) for row in rows])


@router.put("/plans", response_model=schemas.PlansOut)
def put_plans(payload: schemas.PlansReport, principal: Principal = Depends(require_daemon), db: Session = Depends(get_db)):
    """Replace this daemon's CPS snapshot: repo plan + account plan/ledger.

    Mirrors the account dominion's CS5/CS7 files (plans/<repo>/active.md,
    plans/_cross-repo/active.md, ledger/decisions.md) so the dashboard can
    render Current Planned State without the browser touching the local
    dominion repo directly. See kb/plan-brnrd-dashboard-mvp.md "Gap:
    Current Planned State view".
    """
    now = datetime.now(timezone.utc)
    repo = db.get(Repo, principal.repo_id) if principal.repo_id else None
    if repo is not None:
        repo.plan_md = payload.repo_plan_md
        repo.plan_updated_at = now
    account = db.get(Account, principal.account_id)
    if account is not None:
        account.cross_repo_plan_md = payload.cross_repo_plan_md
        account.decision_ledger_md = payload.decision_ledger_md
        account.plans_updated_at = now
    db.commit()
    return schemas.PlansOut(
        repo_plan_md=repo.plan_md if repo is not None else "",
        cross_repo_plan_md=account.cross_repo_plan_md if account is not None else "",
        decision_ledger_md=account.decision_ledger_md if account is not None else "",
        plans_updated_at=now,
    )


@router.put("/quota", response_model=schemas.QuotaOut)
def put_quota(payload: schemas.QuotaReport, principal: Principal = Depends(require_daemon), db: Session = Depends(get_db)):
    """Replace this daemon's runner-quota snapshot (#237).

    Mirrors the Activity/Plans publish shape: the daemon owns the read
    (`src/brr/gates/cloud.py::_quota_snapshot`), this endpoint just stores
    the latest report so `/dashboard` doesn't need to reach the daemon's
    own `.brr/` cache directly.
    """
    daemon = _current_daemon(db, principal)
    if daemon is None:
        raise HTTPException(status_code=404, detail="no daemon registered for this token")
    now = datetime.now(timezone.utc)
    daemon.quota_json = json.dumps([shell.model_dump() for shell in payload.shells], separators=(",", ":"))
    daemon.quota_updated_at = now
    daemon.online = True
    daemon.last_seen_at = now
    db.commit()
    return schemas.QuotaOut(shells=payload.shells, quota_updated_at=now)


@router.put("/live-runs", response_model=schemas.LiveRunsOut)
def put_live_runs(payload: schemas.LiveRunsReport, principal: Principal = Depends(require_daemon), db: Session = Depends(get_db)):
    """Replace this daemon's live/coexisting-runs snapshot (#258).

    Same last-write-wins shape as `put_quota` above: the daemon owns the
    read (`src/brr/gates/cloud.py::_live_runs_snapshot`, sourced from the
    local presence registry), this endpoint just stores the latest report.
    """
    daemon = _current_daemon(db, principal)
    if daemon is None:
        raise HTTPException(status_code=404, detail="no daemon registered for this token")
    now = datetime.now(timezone.utc)
    daemon.live_runs_json = json.dumps([run.model_dump() for run in payload.runs], separators=(",", ":"))
    daemon.live_runs_updated_at = now
    daemon.online = True
    daemon.last_seen_at = now
    db.commit()
    return schemas.LiveRunsOut(runs=payload.runs, live_runs_updated_at=now)


@router.put("/pr-review-queue", response_model=schemas.PRReviewQueueOut)
def put_pr_review_queue(payload: schemas.PRReviewQueueReport, principal: Principal = Depends(require_daemon), db: Session = Depends(get_db)):
    """Replace this daemon's open-PR review queue snapshot (#259).

    Same last-write-wins shape as `put_live_runs`: the daemon owns the
    `gh pr list` read, this endpoint stores the latest account-scoped queue
    for the dashboard.
    """
    daemon = _current_daemon(db, principal)
    if daemon is None:
        raise HTTPException(status_code=404, detail="no daemon registered for this token")
    now = datetime.now(timezone.utc)
    daemon.pr_review_queue_json = json.dumps([pr.model_dump() for pr in payload.prs], separators=(",", ":"))
    daemon.pr_review_queue_updated_at = now
    daemon.online = True
    daemon.last_seen_at = now
    db.commit()
    return schemas.PRReviewQueueOut(prs=payload.prs, pr_review_queue_updated_at=now)


@router.get("/inbox", response_model=schemas.InboxResponse)
def inbox(request: Request, since: int | None = Query(default=None), wait: float | None = Query(default=None), principal: Principal = Depends(require_daemon)):
    settings = request.app.state.settings
    since_seq = since if since is not None else 0
    max_wait = settings.inbox_long_poll_max_s if wait is None else max(0.0, min(wait, settings.inbox_long_poll_max_s))
    events = inbox_service.long_poll(request.app.state.SessionLocal, principal.repo_id, since_seq, max_wait_s=max_wait, interval_s=settings.inbox_poll_interval_s)
    cursor = max((e.seq for e in events), default=since_seq)
    with request.app.state.SessionLocal() as db:
        _touch_daemon(db, principal)
    return schemas.InboxResponse(events=[schemas.EventOut(**inbox_service.event_to_dict(e)) for e in events], cursor=cursor)


@router.post("/responses", response_model=schemas.ResponseAck)
def post_response(request: Request, payload: schemas.ResponsePost, principal: Principal = Depends(require_daemon), db: Session = Depends(get_db)):
    try:
        event = inbox_service.record_response(db, repo_id=principal.repo_id, event_id=payload.event_id, body_markdown=payload.body_markdown, status=payload.status, forwarder=request.app.state.forwarder)
    except inbox_service.DeliveryError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"forward to platform failed: {e}") from e
    if event is None:
        raise HTTPException(status_code=404, detail="event not found for this repo")
    _touch_daemon(db, principal)
    return schemas.ResponseAck(event_id=payload.event_id, forwarded=True)


@router.post("/card", response_model=schemas.CardAck)
def post_card(request: Request, payload: schemas.CardPost, principal: Principal = Depends(require_daemon), db: Session = Depends(get_db)):
    settings = request.app.state.settings
    event = db.execute(select(Event).where(Event.event_id == payload.event_id, Event.repo_id == principal.repo_id)).scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=404, detail="event not found for this repo")
    if event.status == Event.STATUS_RESPONDED:
        return schemas.CardAck(event_id=payload.event_id, message_id=payload.message_id)
    reply_to = inbox_service.reply_to_of(event)
    if reply_to.get("platform") != "telegram" or not settings.telegram_bot_token:
        return schemas.CardAck(event_id=payload.event_id, message_id=None)
    from ..platforms import telegram as tg
    try:
        if payload.message_id is None:
            mid = tg.send_card(settings.telegram_bot_token, reply_to["chat_id"], payload.text, topic_id=reply_to.get("topic_id") or None, reply_to_message_id=reply_to.get("message_id") or None)
            _touch_daemon(db, principal)
            return schemas.CardAck(event_id=payload.event_id, message_id=mid)
        tg.edit_card(settings.telegram_bot_token, reply_to["chat_id"], payload.message_id, payload.text)
        _touch_daemon(db, principal)
        return schemas.CardAck(event_id=payload.event_id, message_id=payload.message_id)
    except tg.CardGone as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"card not editable: {e}") from e
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"card relay failed: {e}") from e


_MAX_PACK_BYTES = 4 * 1024 * 1024


@router.post("/pack", response_model=schemas.PackRelayAck)
def post_pack(request: Request, payload: schemas.PackRelayPost, principal: Principal = Depends(require_daemon)):
    blob = json.dumps(payload.pack, separators=(",", ":"))
    if len(blob.encode("utf-8")) > _MAX_PACK_BYTES:
        raise HTTPException(status_code=413, detail="review pack too large to relay")
    token, expires_at = request.app.state.pack_relay.put(payload.pack, ttl_s=payload.ttl_s)
    base = request.app.state.settings.public_base_url.rstrip("/")
    return schemas.PackRelayAck(token=token, render_url=f"{base}/r/{token}", expires_at=expires_at)


@router.post("/deregister")
def deregister(payload: schemas.DaemonDeregister, principal: Principal = Depends(require_daemon), db: Session = Depends(get_db)):
    daemon = db.execute(select(Daemon).where(Daemon.repo_id == principal.repo_id, Daemon.daemon_name == payload.daemon_name)).scalar_one_or_none()
    if daemon is not None:
        daemon.online = False
        daemon.last_seen_at = datetime.now(timezone.utc)
        db.commit()
    return {"ok": True}

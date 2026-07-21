"""Daemon-facing inbox endpoints — register, long-poll, respond, deregister."""

from __future__ import annotations

import json
import mimetypes
from datetime import datetime, timezone
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import ids, inbox as inbox_service, run_stop_requests, schemas, wake_requests
from ..activity_records import ACTIVITY_STALE_TTL
from ..auth import Principal, get_db, require_daemon
from ..models import Account, ActivityRecord, Daemon, Event, GitHubInstallation, GitHubInstalledRepo, Repo
from ..platforms import github_app as github_app_client

router = APIRouter(prefix="/v1/daemons", tags=["daemons"])


def _touch_daemon(db: Session, principal: Principal) -> None:
    daemon = db.execute(
        select(Daemon).where(
            Daemon.account_id == principal.account_id,
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
            Daemon.account_id == principal.account_id,
            Daemon.token_id == principal.token.id,
        )
    ).scalar_one_or_none()


def _account_repos(db: Session, principal: Principal) -> list[Repo]:
    return list(
        db.execute(
            select(Repo).where(Repo.account_id == principal.account_id)
        ).scalars()
    )


def _account_event(
    db: Session, principal: Principal, event_id: str,
) -> Event | None:
    return db.execute(
        select(Event)
        .join(Repo, Repo.id == Event.repo_id)
        .where(
            Event.event_id == event_id,
            Repo.account_id == principal.account_id,
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
    existing = db.execute(
        select(Daemon).where(
            Daemon.account_id == principal.account_id,
            Daemon.daemon_name == payload.daemon_name,
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.online = True
        existing.last_seen_at = datetime.now(timezone.utc)
        existing.capabilities = caps
        existing.token_id = principal.token.id
        existing.repo_id = repo_id
        db.commit()
        return schemas.DaemonRegistered(daemon_id=existing.id, repo_id=repo_id)
    daemon = Daemon(id=ids.daemon_id(), account_id=principal.account_id, repo_id=repo_id, token_id=principal.token.id, daemon_name=payload.daemon_name, capabilities=caps, online=True)
    db.add(daemon)
    db.commit()
    return schemas.DaemonRegistered(daemon_id=daemon.id, repo_id=repo_id)


@router.post("/publishing-credential", response_model=schemas.PublishingCredential)
def publishing_credential(
    request: Request,
    response: Response,
    principal: Principal = Depends(require_daemon),
    db: Session = Depends(get_db),
):
    """Mint the repo-scoped App identity used by managed runner publishing."""
    repo = db.get(Repo, principal.repo_id)
    if repo is None or repo.forge != "github":
        raise HTTPException(status_code=404, detail="GitHub repo not found")
    installed = db.execute(
        select(GitHubInstalledRepo, GitHubInstallation)
        .join(
            GitHubInstallation,
            GitHubInstallation.id == GitHubInstalledRepo.github_installation_id,
        )
        .where(
            GitHubInstallation.account_id == principal.account_id,
            GitHubInstalledRepo.repo_full_name == repo.repo_full_name,
        )
        .order_by(GitHubInstallation.last_synced_at.desc())
    ).first()
    if installed is None:
        raise HTTPException(status_code=409, detail="GitHub App is not installed for this repo")
    installed_repo, installation = installed
    raw_repo_id = installed_repo.forge_repo_id or repo.forge_repo_id
    try:
        repository_id = int(raw_repo_id or "")
    except ValueError:
        repository_id = None
    credential = github_app_client.installation_access_credential(
        request.app.state.settings,
        installation.installation_id,
        repository_ids=[repository_id] if repository_id is not None else None,
        repositories=None if repository_id is not None else [repo.repo_name],
    )
    response.headers["Cache-Control"] = "no-store"
    return schemas.PublishingCredential(
        token=credential["token"],
        expires_at=credential["expires_at"],
        login=f"{request.app.state.settings.github_app_slug}[bot]",
    )


@router.put("/activity", response_model=schemas.ActivityList)
def put_activity(payload: schemas.ActivityReport, principal: Principal = Depends(require_daemon), db: Session = Depends(get_db)):
    """Replace this daemon token's current Activity snapshot for its repo.

    Delete-then-insert is atomic per transaction but not against a
    *concurrent* PUT for the same (repo, token): both delete, both insert,
    the second commit hits ``uq_activity_repo_token_record``. Snapshots are
    full replacements published every few seconds, so on that race the
    loser rolls back and defers to the winner instead of 500ing.
    """
    daemon = _current_daemon(db, principal)
    # #502: the event queue's hourly GC rides this publish tick — same
    # piggyback economics as the stale-activity delete below.
    inbox_service.gc_events(db)
    now = datetime.now(timezone.utc)
    db.execute(
        delete(ActivityRecord).where(
            ActivityRecord.repo_id == principal.repo_id,
            ActivityRecord.token_id == principal.token.id,
        )
    )
    db.execute(
        delete(ActivityRecord).where(
            ActivityRecord.repo_id == principal.repo_id,
            ActivityRecord.reported_at < now - ACTIVITY_STALE_TTL,
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
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        current = list(
            db.execute(
                select(ActivityRecord).where(
                    ActivityRecord.repo_id == principal.repo_id,
                    ActivityRecord.token_id == principal.token.id,
                )
            ).scalars()
        )
        return schemas.ActivityList(activity=[_activity_out(row) for row in current])
    return schemas.ActivityList(activity=[_activity_out(row) for row in rows])


@router.put("/surface", response_model=schemas.SurfaceOut)
def put_surface(payload: schemas.SurfaceReport, principal: Principal = Depends(require_daemon), db: Session = Depends(get_db)):
    """Replace the account's discovered corpus mirror (surface + knowledge).

    Paths are home-relative (``surface/…``, ``knowledge/…``) since the corpus
    join (#PR corpus-join); the traversal guard rejects absolute/``..``/hidden
    parts exactly as it did for the surface-relative convention.
    """

    seen: set[str] = set()
    files: list[dict[str, object]] = []
    for item in payload.files:
        path = PurePosixPath(item.path)
        if path.is_absolute() or ".." in path.parts or any(part.startswith(".") for part in path.parts):
            raise HTTPException(status_code=422, detail=f"invalid surface path: {item.path}")
        normalized = path.as_posix()
        if normalized in seen:
            raise HTTPException(status_code=422, detail=f"duplicate surface path: {normalized}")
        seen.add(normalized)
        files.append({"path": normalized, "markdown": item.markdown, "layer": item.layer, "truncated": item.truncated})

    now = datetime.now(timezone.utc)
    account = db.get(Account, principal.account_id)
    if account is not None:
        account.surface_json = json.dumps(files, separators=(",", ":"))
        account.surface_updated_at = now
    db.commit()
    return schemas.SurfaceOut(files=payload.files, surface_updated_at=now)


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
    daemon.gate_health_json = json.dumps(
        [gate.model_dump() for gate in payload.gates], separators=(",", ":")
    )
    daemon.quota_updated_at = now
    daemon.online = True
    daemon.last_seen_at = now
    db.commit()
    return schemas.QuotaOut(
        shells=payload.shells,
        gates=payload.gates,
        quota_updated_at=now,
    )


@router.put("/runners", response_model=schemas.RunnersOut)
def put_runners(payload: schemas.RunnersReport, principal: Principal = Depends(require_daemon), db: Session = Depends(get_db)):
    """Replace this daemon's runner-catalog snapshot (#328 spool rack).

    Same last-write-wins mirror shape as `put_quota` above: the daemon owns
    the discovery (`src/brr/gates/cloud.py::_runners_snapshot` reads the
    locally-probed catalog), this endpoint just stores the latest projection
    so the dashboard can render which bodies the account's daemons can
    actually wake, and which one the config currently pins.
    """
    daemon = _current_daemon(db, principal)
    if daemon is None:
        raise HTTPException(status_code=404, detail="no daemon registered for this token")
    now = datetime.now(timezone.utc)
    daemon.runners_json = json.dumps(
        [profile.model_dump(by_alias=True, exclude_none=True) for profile in payload.profiles],
        separators=(",", ":"),
    )
    daemon.runners_default = payload.default
    daemon.runners_updated_at = now
    daemon.online = True
    daemon.last_seen_at = now
    db.commit()
    # #328 tap-to-request piggyback: retire wake requests this daemon just
    # spent on a dispatched wake, then hand back the account's still-pending
    # one (if any) so the daemon learns of a tap within one publish tick.
    wake_requests.mark_consumed(
        db, principal.account_id, payload.consumed_wake_request_ids,
    )
    pending = wake_requests.pending_for_account(db, principal.account_id)
    return schemas.RunnersOut(
        profiles=payload.profiles,
        default=payload.default,
        runners_updated_at=now,
        pending_wake_request=(
            schemas.RunnerWakeRequestOut(**wake_requests.view(pending))
            if pending is not None
            else None
        ),
    )


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
    daemon.spawn_max_concurrent = payload.spawn_max_concurrent
    daemon.online = True
    daemon.last_seen_at = now
    db.commit()
    # #476 wyrd §3 stop piggyback, mirroring the #328 wake-request handshake
    # on `put_runners`: retire the stops this daemon just dispatched into the
    # kill path, then hand back the account's still-pending ones so a user's
    # tap reaches a burning run within one publish tick.
    run_stop_requests.mark_consumed(
        db, principal.account_id, payload.consumed_run_stop_request_ids,
    )
    pending_stops = run_stop_requests.pending_for_account(db, principal.account_id)
    return schemas.LiveRunsOut(
        runs=payload.runs,
        live_runs_updated_at=now,
        spawn_max_concurrent=payload.spawn_max_concurrent,
        pending_run_stop_requests=[
            schemas.RunStopRequestOut(**run_stop_requests.view(row))
            for row in pending_stops
        ],
    )


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


@router.put("/run-ledger", response_model=schemas.RunLedgerOut)
def put_run_ledger(payload: schemas.RunLedgerReport, principal: Principal = Depends(require_daemon), db: Session = Depends(get_db)):
    """Replace this daemon's closed-run receipt snapshot (#271)."""
    daemon = _current_daemon(db, principal)
    if daemon is None:
        raise HTTPException(status_code=404, detail="no daemon registered for this token")
    now = datetime.now(timezone.utc)
    daemon.run_ledger_json = json.dumps([row.model_dump() for row in payload.rows], separators=(",", ":"))
    daemon.run_ledger_updated_at = now
    daemon.online = True
    daemon.last_seen_at = now
    db.commit()
    return schemas.RunLedgerOut(rows=payload.rows, run_ledger_updated_at=now)


@router.get("/inbox", response_model=schemas.InboxResponse)
def inbox(request: Request, since: int | None = Query(default=None), wait: float | None = Query(default=None), principal: Principal = Depends(require_daemon)):
    settings = request.app.state.settings
    since_seq = since if since is not None else 0
    with request.app.state.SessionLocal() as db:
        repos = _account_repos(db, principal)
        repo_ids = {repo.id for repo in repos}
        repo_labels = {repo.id: repo.repo_full_name for repo in repos}
        if since_seq > 0:
            since_seq = inbox_service.clamp_since_many(db, repo_ids, since_seq)
    max_wait = settings.inbox_long_poll_max_s if wait is None else max(0.0, min(wait, settings.inbox_long_poll_max_s))
    events = inbox_service.long_poll_many(request.app.state.SessionLocal, repo_ids, since_seq, max_wait_s=max_wait, interval_s=settings.inbox_poll_interval_s)
    cursor = max((e.seq for e in events), default=since_seq)
    with request.app.state.SessionLocal() as db:
        _touch_daemon(db, principal)
    return schemas.InboxResponse(
        events=[
            schemas.EventOut(
                **inbox_service.event_to_dict(
                    event, repo_label=repo_labels.get(event.repo_id),
                )
            )
            for event in events
        ],
        cursor=cursor,
    )


@router.post("/responses", response_model=schemas.ResponseAck)
def post_response(request: Request, payload: schemas.ResponsePost, principal: Principal = Depends(require_daemon), db: Session = Depends(get_db)):
    owned_event = _account_event(db, principal, payload.event_id)
    if owned_event is None:
        raise HTTPException(status_code=404, detail="event not found for this account")
    try:
        event = inbox_service.record_response(db, repo_id=owned_event.repo_id, event_id=payload.event_id, body_markdown=payload.body_markdown, status=payload.status, forwarder=request.app.state.forwarder)
    except inbox_service.DeliveryError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"forward to platform failed: {e}") from e
    if event is None:
        raise HTTPException(status_code=404, detail="event not found for this account")
    _touch_daemon(db, principal)
    return schemas.ResponseAck(event_id=payload.event_id, forwarded=True)


@router.post("/card", response_model=schemas.CardAck)
def post_card(request: Request, payload: schemas.CardPost, principal: Principal = Depends(require_daemon), db: Session = Depends(get_db)):
    settings = request.app.state.settings
    event = _account_event(db, principal, payload.event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found for this account")
    # Deliberately no responded-guard here: a respawn continuation run rides
    # its parent's event, so cards must keep flowing after the parent's
    # terminal close (2026-07-21 — the mega run whose status card vanished).
    # Card sends/edits are already idempotent via message_id.
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


@router.get("/events/{event_id}/attachments/{index}")
def event_attachment(event_id: str, index: int, request: Request, principal: Principal = Depends(require_daemon), db: Session = Depends(get_db)):
    """#525 — read-through proxy for a queued event's image attachment.

    The server holds only *pointers* (models.Event.attachments_json); this
    endpoint resolves the Telegram ``file_id`` fresh via ``getFile`` on every
    request and streams the bytes through memory — nothing lands at rest
    server-side (#543 bounded mirror, #542 pointer-not-copy). Same daemon
    credential as the inbox pull, scoped to the token's repo. Telegram file
    links can expire: failures surface as honest HTTP errors (502/413) for
    the daemon to annotate, never fabricated or silently empty bytes.
    """
    settings = request.app.state.settings
    event = _account_event(db, principal, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found for this repo")
    pointers = inbox_service.attachments_of(event)
    if not 0 <= index < len(pointers):
        # Also the shape a closed/aged-out event answers with — pointers are
        # cleared alongside the body, so "no such attachment" is honest.
        raise HTTPException(status_code=404, detail="no such attachment")
    if not settings.telegram_bot_token:
        raise HTTPException(status_code=503, detail="telegram is not configured")
    pointer = pointers[index]
    max_bytes = max(1, int(settings.telegram_media_max_mb)) * 1024 * 1024
    from ..platforms import telegram as tg
    try:
        info = tg.resolve_file(settings.telegram_bot_token, str(pointer.get("file_id") or ""))
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"telegram file unavailable: {e}") from e
    declared = info.get("file_size")
    if isinstance(declared, int) and declared > max_bytes:
        raise HTTPException(status_code=413, detail=f"attachment exceeds the {settings.telegram_media_max_mb} MB cap")
    try:
        content = tg.fetch_file_bytes(settings.telegram_bot_token, str(info.get("file_path") or ""), max_bytes=max_bytes)
    except tg.FileTooLarge as e:
        raise HTTPException(status_code=413, detail=f"attachment exceeds the {settings.telegram_media_max_mb} MB cap") from e
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"telegram file unavailable: {e}") from e
    _touch_daemon(db, principal)
    media_type = mimetypes.guess_type(str(pointer.get("filename") or ""))[0] or "application/octet-stream"
    return Response(content=content, media_type=media_type)


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
    daemon = db.execute(
        select(Daemon).where(
            Daemon.account_id == principal.account_id,
            Daemon.daemon_name == payload.daemon_name,
        )
    ).scalar_one_or_none()
    if daemon is not None:
        daemon.online = False
        daemon.last_seen_at = datetime.now(timezone.utc)
        db.commit()
    return {"ok": True}

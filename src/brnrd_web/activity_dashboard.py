"""Dashboard activity aggregation for the brnrd web control deck."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from brnrd.activity_records import dedupe_activity_records
from brnrd.auth import get_db
from brnrd.models import Account, ActivityRecord, Daemon, Event, GitHubInstalledRepo, Repo

from .routes import (
    _account_id,
    _age_label,
    _dt,
    _github_auto_sync_if_needed,
    _github_sync_configured,
    _installations,
    _installed_repos,
    _notice_text,
    _render,
    _repo_views,
    _repos,
    _time_label,
)

router = APIRouter(tags=["web"])


_RUNNING_STATUSES = {"running", "active", "in_progress", "draining", "started"}
_PENDING_STATUSES = {"pending", "queued", "waiting", "blocked", "accepted"}
_SCHEDULED_STATUSES = {"scheduled", "deferred", "sleeping"}
_FAILED_STATUSES = {"failed", "error", "errored", "cancelled", "canceled"}
_COMPLETED_STATUSES = {"complete", "completed", "done", "responded", "success", "succeeded"}
_PARKED_STATUSES = {"parked", "respawn", "respawned"}


def _duration_label(start: datetime | None, end: datetime | None = None) -> str:
    start = _dt(start)
    if start is None:
        return ""
    end = _dt(end) or datetime.now(timezone.utc)
    seconds = max(0, int((end - start).total_seconds()))
    if seconds < 90:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes}m {seconds % 60:02d}s"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h {minutes % 60:02d}m"
    days = hours // 24
    return f"{days}d {hours % 24:02d}h"


def _short_time_label(value: datetime | None) -> str:
    value = _dt(value)
    if value is None:
        return ""
    return value.strftime("%H:%M:%S")


def _json_obj(raw: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _compact(value: str | None, *, limit: int = 160) -> str:
    value = " ".join((value or "").split())
    if not value:
        return "untitled activity"
    return value[:limit] + ("…" if len(value) > limit else "")


def _activity_bucket(row: ActivityRecord) -> str:
    status = (row.status or "").strip().casefold()
    phase = (row.phase or "").strip().casefold()
    kind = (row.kind or "").strip().casefold()

    if kind == "scheduled" or status in _SCHEDULED_STATUSES or row.scheduled_for is not None:
        return "scheduled"
    if status in _RUNNING_STATUSES or phase in _RUNNING_STATUSES:
        return "running"
    if status in _PENDING_STATUSES or phase in _PENDING_STATUSES:
        return "pending"
    if status in _FAILED_STATUSES:
        return "failed"
    if status in _COMPLETED_STATUSES:
        return "completed"
    if kind == "respawn" or status in _PARKED_STATUSES or row.defer_until is not None:
        return "parked"
    return status or kind or "activity"


def _status_class(bucket: str) -> str:
    return {
        "running": "ok",
        "pending": "warn",
        "scheduled": "info",
        "parked": "warn",
        "failed": "danger",
        "completed": "muted",
    }.get(bucket, "info")


def _runner_parts(runner: dict[str, Any]) -> tuple[str, str, str]:
    shell = str(runner.get("shell") or runner.get("name") or "").strip()
    core = str(runner.get("core") or runner.get("model") or "").strip()
    summary = " / ".join(part for part in (shell, core) if part)
    if not summary:
        summary = str(runner.get("summary") or "").strip()
    return shell or "unknown", core, summary


def _activity_views(
    db: Session,
    repos: list[Repo],
    *,
    repo_id: str | None = None,
    kind: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    repo_by_id = {repo.id: repo for repo in repos}
    repo_ids = set(repo_by_id)
    if repo_id:
        repo_ids = {repo_id} if repo_id in repo_ids else set()
    if not repo_ids:
        return []

    daemon_by_id = {
        daemon.id: daemon
        for daemon in db.execute(select(Daemon).where(Daemon.repo_id.in_(repo_ids))).scalars()
    }

    stmt = select(ActivityRecord).where(ActivityRecord.repo_id.in_(repo_ids))
    if kind:
        stmt = stmt.where(ActivityRecord.kind == kind)
    if status:
        stmt = stmt.where(ActivityRecord.status == status)
    rows = db.execute(
        stmt.order_by(
            ActivityRecord.updated_at.desc().nullslast(),
            ActivityRecord.reported_at.desc(),
        )
    ).scalars()
    rows = dedupe_activity_records(rows)

    out: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    for record in rows:
        runner = _json_obj(record.runner_json)
        links = _json_obj(record.links_json)
        shell, core, runner_summary = _runner_parts(runner)
        repo = repo_by_id.get(record.repo_id)
        daemon = daemon_by_id.get(record.daemon_id or "")
        bucket = _activity_bucket(record)
        when = record.scheduled_for or record.defer_until or record.updated_at or record.started_at or record.reported_at
        elapsed_end = None if bucket in {"running", "pending"} else (record.updated_at or record.reported_at or now)
        out.append(
            {
                "record": record,
                "repo": repo,
                "repo_name": repo.repo_full_name if repo else record.repo_id,
                "runner": runner,
                "shell": shell,
                "core": core,
                "runner_summary": runner_summary,
                "bucket": bucket,
                "status_class": _status_class(bucket),
                "source_label": record.source or "daemon",
                "daemon_name": daemon.daemon_name if daemon else "",
                "summary_compact": _compact(record.summary or record.record_id),
                "when_label": _time_label(when),
                "short_when_label": _short_time_label(when),
                "started_label": _time_label(record.started_at),
                "elapsed_label": _duration_label(record.started_at, elapsed_end),
                "updated_label": _age_label(record.updated_at or record.reported_at),
                "links": links,
            }
        )
    return out


def _runner_stats(activity_views: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for view in activity_views:
        shell = view["shell"] or "unknown"
        row = rows.setdefault(
            shell,
            {
                "shell": shell,
                "running": 0,
                "pending": 0,
                "scheduled": 0,
                "completed": 0,
                "failed": 0,
                "parked": 0,
                "total": 0,
            },
        )
        bucket = view["bucket"]
        if bucket in {"running", "pending", "scheduled", "completed", "failed", "parked"}:
            row[bucket] += 1
        row["total"] += 1
    return sorted(rows.values(), key=lambda row: (row["running"], row["pending"], row["scheduled"], row["total"], row["shell"]), reverse=True)


def _outbound_event_views(db: Session, repos: list[Repo]) -> list[dict[str, Any]]:
    repo_by_id = {repo.id: repo for repo in repos}
    repo_ids = set(repo_by_id)
    if not repo_ids:
        return []
    rows = db.execute(
        select(Event)
        .where(Event.repo_id.in_(repo_ids))
        .order_by(Event.created_at.desc())
    ).scalars()
    out: list[dict[str, Any]] = []
    for event in rows:
        repo = repo_by_id.get(event.repo_id)
        status_label = event.response_status or event.status or "queued"
        status_class = "danger" if status_label in {"failed", "error"} else ("ok" if status_label in {"responded", "sent", "success"} else "info")
        out.append(
            {
                "repo_name": repo.repo_full_name if repo else event.repo_id,
                "source": event.source or "event",
                "status": status_label,
                "status_class": status_class,
                "time_label": _short_time_label(event.responded_at or event.created_at),
                "summary": _compact(event.body or event.reply_to or event.event_id, limit=120),
            }
        )
    return out[:8]


def _activity_stats(
    repo_views: list[dict[str, Any]],
    activity_views: list[dict[str, Any]],
    outbound_events: list[dict[str, Any]],
    installed: list[GitHubInstalledRepo],
) -> dict[str, int]:
    buckets: dict[str, int] = {}
    for view in activity_views:
        buckets[view["bucket"]] = buckets.get(view["bucket"], 0) + 1
    return {
        "synced_repos": len(installed),
        "daemons_online": sum(1 for row in repo_views if row["daemon_status"] == "online"),
        "daemons_stale": sum(1 for row in repo_views if row["daemon_status"] == "offline"),
        "daemons_waiting": sum(1 for row in repo_views if row["daemon_status"] == "missing"),
        "active_runs": buckets.get("running", 0),
        "pending_runs": buckets.get("pending", 0),
        "scheduled_runs": buckets.get("scheduled", 0),
        "completed_runs": buckets.get("completed", 0),
        "failed_runs": buckets.get("failed", 0),
        "parked_runs": buckets.get("parked", 0),
        "outbound_events": len(outbound_events),
        "outbound_failures": sum(1 for row in outbound_events if row["status_class"] == "danger"),
    }


_QUOTA_STALE_SECONDS = 300  # daemon publishes on its ~25-30s poll loop (#237)


def _quota_views(db: Session, repos: list[Repo], runner_stats: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Real per-shell quota windows from the daemons' own reports (#237).

    Reads the last ``PUT /v1/daemons/quota`` snapshot per connected daemon
    (`src/brr/gates/cloud.py::_quota_snapshot` is the writer). A daemon that
    hasn't reported inside ``_QUOTA_STALE_SECONDS`` still shows its last
    numbers, but flagged ``stale`` rather than silently going quiet — the
    honest fallback the ticket asked for. A shell with active runs
    (``runner_stats``) but no quota report at all (older daemon build, or
    cold cache) still renders as an explicit ``unknown`` placeholder card,
    not omitted — "quota provider pending" beats a missing panel.
    """
    repo_ids = {repo.id for repo in repos}
    real: dict[str, dict[str, Any]] = {}
    if repo_ids:
        now = datetime.now(timezone.utc)
        daemons = db.execute(select(Daemon).where(Daemon.repo_id.in_(repo_ids))).scalars()
        for daemon in daemons:
            reported_at = _dt(daemon.quota_updated_at)
            if reported_at is None:
                continue
            try:
                shells = json.loads(daemon.quota_json or "[]")
            except ValueError:
                shells = []
            if not isinstance(shells, list):
                continue
            stale = (now - reported_at).total_seconds() > _QUOTA_STALE_SECONDS
            for shell in shells:
                if not isinstance(shell, dict):
                    continue
                name = str(shell.get("shell") or "").strip()
                if not name:
                    continue
                existing = real.get(name)
                if existing is not None and existing["_reported_at"] >= reported_at:
                    continue
                real[name] = {
                    "shell": name,
                    "status": "stale" if stale else str(shell.get("status") or "unknown"),
                    "windows": shell.get("windows") or [],
                    "_reported_at": reported_at,
                }
    out = list(real.values())
    for row in out:
        row.pop("_reported_at", None)
    for row in runner_stats:
        shell = row["shell"]
        if shell == "unknown" or shell in real:
            continue
        out.append(
            {
                "shell": shell,
                "status": "unknown",
                "windows": [
                    {"label": "5h window", "used": None, "limit": None, "percent": None},
                    {"label": "weekly", "used": None, "limit": None, "percent": None},
                ],
            }
        )
    return sorted(out, key=lambda row: row["shell"])[:6]


_LIVE_RUNS_STALE_SECONDS = 300  # matches presence.py's own DEFAULT_STALE_AFTER_S


def _live_runs_views(db: Session, repos: list[Repo]) -> dict[str, Any]:
    """Account-scoped live/coexisting-runs view (#258).

    Reads the last ``PUT /v1/daemons/live-runs`` snapshot per connected
    daemon (`src/brr/gates/cloud.py::_live_runs_snapshot` is the writer,
    sourced from the local presence registry). Several ``Daemon`` rows can
    belong to the same physical daemon process (one row per repo it's
    registered under, `Daemon.repo_id`) and would each report the same
    underlying presence entries — deduped by ``id`` here, freshest report
    wins, the same "one daemon, several repo registrations" shape
    `_quota_views` above already merges by shell name.
    """
    repo_ids = {repo.id for repo in repos}
    if not repo_ids:
        return {"runs": [], "stale": False, "generated_at": None}
    now = datetime.now(timezone.utc)
    runs: dict[str, dict[str, Any]] = {}
    newest_reported_at: datetime | None = None
    daemons = db.execute(select(Daemon).where(Daemon.repo_id.in_(repo_ids))).scalars()
    for daemon in daemons:
        reported_at = _dt(daemon.live_runs_updated_at)
        if reported_at is None:
            continue
        if newest_reported_at is None or reported_at > newest_reported_at:
            newest_reported_at = reported_at
        try:
            entries = json.loads(daemon.live_runs_json or "[]")
        except ValueError:
            entries = []
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            run_key = str(entry.get("id") or entry.get("run_id") or "")
            if not run_key:
                continue
            existing = runs.get(run_key)
            if existing is not None and existing["_reported_at"] >= reported_at:
                continue
            row = dict(entry)
            row["_reported_at"] = reported_at
            runs[run_key] = row
    out = list(runs.values())
    for row in out:
        row.pop("_reported_at", None)
    out.sort(key=lambda row: row.get("started_at") or "")
    stale = bool(newest_reported_at) and (now - newest_reported_at).total_seconds() > _LIVE_RUNS_STALE_SECONDS
    return {
        "runs": out,
        "stale": stale,
        "generated_at": newest_reported_at.isoformat() if newest_reported_at else None,
    }


def _activity_dashboard_context(request: Request, db: Session, account: Account, *, notice: str | None = None, installation_id: str | None = None) -> dict[str, Any]:
    settings = request.app.state.settings
    repos = _repos(db, account.id)
    repo_views = _repo_views(db, repos)
    installations = _installations(db, account.id)
    installed = _installed_repos(db, account.id)
    connected = {r.repo_full_name.casefold() for r in repos}
    activity_views = _activity_views(db, repos)
    active_views = [row for row in activity_views if row["bucket"] in {"running", "pending", "parked", "failed"}][:6]
    scheduled_views = sorted(
        [row for row in activity_views if row["bucket"] == "scheduled"],
        key=lambda row: row["record"].scheduled_for or row["record"].defer_until or row["record"].updated_at or row["record"].reported_at,
    )[:5]
    recent_activity_views = activity_views[:8]
    runner_stats = _runner_stats(activity_views)
    outbound_events = _outbound_event_views(db, repos)
    return {
        "body_class": "dashboard-page",
        "title": "brnrd dashboard",
        "logged_in": True,
        "account": account,
        "repos": repos,
        "repo_views": repo_views,
        "installations": installations,
        "installed_repos": installed,
        "connected_repo_names": connected,
        "connected_count": len(repos),
        "install_url": settings.github_install_url,
        "github_app_slug": settings.github_app_slug,
        "github_bot_login": settings.github_bot_login.strip().lstrip("@"),
        "github_bot_user_login": settings.github_bot_user_login.strip().lstrip("@"),
        "github_sync_configured": _github_sync_configured(request),
        "notice": _notice_text(notice),
        "setup_installation_id": installation_id or "",
        "activity_views": activity_views,
        "active_activity_views": active_views,
        "scheduled_activity_views": scheduled_views,
        "recent_activity_views": recent_activity_views,
        "runner_stats": runner_stats,
        "runner_quotas": _quota_views(db, repos, runner_stats),
        "outbound_event_views": outbound_events,
        "dashboard_stats": _activity_stats(repo_views, activity_views, outbound_events, installed),
    }


@router.get("/v1/dashboard/quota")
def dashboard_quota_api(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """JSON twin of ``runner_quotas`` for the SvelteKit frontend (slice 2:
    window-track view, `src/frontend`). Same session cookie as the Jinja
    dashboard — no separate auth layer, per `src/frontend/README.md`'s
    "fetch the same JSON endpoints client-side" plan. Returns 401 rather
    than a login redirect: this is fetched by JS, not navigated to.
    """
    account_id = _account_id(request, db)
    if account_id is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    account = db.get(Account, account_id)
    if account is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    repos = _repos(db, account.id)
    activity_views = _activity_views(db, repos)
    runner_stats = _runner_stats(activity_views)
    return JSONResponse(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "runner_quotas": _quota_views(db, repos, runner_stats),
        }
    )


@router.get("/v1/dashboard/live-runs")
def dashboard_live_runs_api(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """Account-scoped live/coexisting-runs view (#258) for the SvelteKit
    frontend. Same session-cookie auth as ``dashboard_quota_api`` — 401,
    not a login redirect, since this is fetched by JS.
    """
    account_id = _account_id(request, db)
    if account_id is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    account = db.get(Account, account_id)
    if account is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    repos = _repos(db, account.id)
    view = _live_runs_views(db, repos)
    return JSONResponse(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "runs": view["runs"],
            "stale": view["stale"],
            "reported_at": view["generated_at"],
        }
    )


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, installation_id: str | None = None, notice: str | None = None, db: Session = Depends(get_db)):
    account_id = _account_id(request, db)
    if account_id is None:
        return _render(
            request,
            "dashboard.html",
            {
                "body_class": "dashboard-page",
                "title": "brnrd dashboard",
                "logged_in": False,
                "signin_url": "/login?next=/",
                "install_url": request.app.state.settings.github_install_url,
                "github_app_slug": request.app.state.settings.github_app_slug,
                "github_bot_login": request.app.state.settings.github_bot_login.strip().lstrip("@"),
            },
        )
    account = db.get(Account, account_id)
    if account is None:
        return RedirectResponse(url="/login?next=/", status_code=303)
    notice = notice or _github_auto_sync_if_needed(request, db, account.id)
    return _render(request, "dashboard.html", _activity_dashboard_context(request, db, account, notice=notice, installation_id=installation_id))


@router.get("/activity", response_class=HTMLResponse)
def activity_page(request: Request, repo_id: str | None = None, kind: str | None = None, status: str | None = None, db: Session = Depends(get_db)):
    account_id = _account_id(request, db)
    if account_id is None:
        return RedirectResponse(url="/login?next=/activity", status_code=303)
    account = db.get(Account, account_id)
    if account is None:
        return RedirectResponse(url="/login?next=/activity", status_code=303)
    repos = _repos(db, account.id)
    base_views = _activity_views(db, repos, repo_id=repo_id or None)
    views = _activity_views(
        db,
        repos,
        repo_id=repo_id or None,
        kind=kind or None,
        status=status or None,
    )
    kinds = sorted({view["record"].kind for view in base_views} | {"run", "scheduled", "respawn"})
    statuses = sorted({view["record"].status for view in base_views if view["record"].status} | {"running", "pending", "scheduled"})
    return _render(
        request,
        "activity.html",
        {
            "body_class": "dashboard-page",
            "title": "brnrd activity",
            "logged_in": True,
            "account": account,
            "repos": repos,
            "activity_views": views,
            "selected_repo_id": repo_id or "",
            "selected_kind": kind or "",
            "selected_status": status or "",
            "kinds": kinds,
            "statuses": statuses,
        },
    )

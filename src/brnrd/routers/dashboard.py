"""Dashboard activity aggregation for the brnrd web control deck.

Migrated from ``src/brnrd_web/activity_dashboard.py`` when ``brnrd_web``
was folded into ``src/brnrd/routers/``. Route paths and JSON response
shapes are byte-compatible with the previous module.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from brnrd import run_stop_requests, wake_requests
from brnrd.activity_records import dedupe_activity_records, fresh_activity_records
from brnrd.auth import get_db
from brnrd.models import Account, ActivityRecord, ConfigChangeRequest, Daemon, Event, GitHubInstalledRepo, Repo

from ._session import (
    _account_id,
    _age_label,
    _dt,
    _github_auto_sync_if_needed,
    _github_oauth_ready,
    _github_sync_configured,
    _installations,
    _installed_repos,
    _notice_text,
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
_DISPATCH_ENVIRONMENTS = {"worktree", "docker", "solitary"}


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
    rows = dedupe_activity_records(fresh_activity_records(rows))

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


def _parse_scrape_updated_at(value: Any) -> datetime | None:
    """Parse a collector's own ``updated_at`` timestamp, or ``None``."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _stale_quota_windows(windows: Any) -> list[dict[str, Any]]:
    """Keep stale quota rows visible without rendering old percentages as truth."""
    if not isinstance(windows, list):
        return []
    out: list[dict[str, Any]] = []
    for window in windows:
        if not isinstance(window, dict):
            continue
        out.append(
            {
                **window,
                "used": None,
                "limit": None,
                "percent": None,
                "reset": None,
                "resets_at": None,
            }
        )
    return out


def _quota_views(db: Session, repos: list[Repo], runner_stats: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Real per-shell quota windows from the daemons' own reports (#237)."""
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
            for shell in shells:
                if not isinstance(shell, dict):
                    continue
                name = str(shell.get("shell") or "").strip()
                if not name:
                    continue
                existing = real.get(name)
                if existing is not None and existing["_reported_at"] >= reported_at:
                    continue
                scrape_at = _parse_scrape_updated_at(shell.get("updated_at")) or reported_at
                stale = (now - scrape_at).total_seconds() > _QUOTA_STALE_SECONDS
                real[name] = {
                    "shell": name,
                    "status": "stale" if stale else str(shell.get("status") or "unknown"),
                    "windows": (
                        _stale_quota_windows(shell.get("windows"))
                        if stale else shell.get("windows") or []
                    ),
                    "credits": shell.get("credits"),
                    "reset_credits": shell.get("reset_credits"),
                    "spend": shell.get("spend"),
                    "burn": None if stale else shell.get("burn"),
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


_RUNNERS_STALE_SECONDS = 600


def _runners_views(db: Session, repos: list[Repo]) -> dict[str, Any]:
    """Account-scoped runner catalog: the spool rack (#328)."""
    repo_ids = {repo.id for repo in repos}
    if not repo_ids:
        return {"profiles": [], "default": None, "stale": False, "reported_at": None}
    now = datetime.now(timezone.utc)
    profiles: dict[str, dict[str, Any]] = {}
    default: str | None = None
    newest: datetime | None = None
    daemons = db.execute(select(Daemon).where(Daemon.repo_id.in_(repo_ids))).scalars()
    for daemon in daemons:
        reported_at = _dt(daemon.runners_updated_at)
        if reported_at is None:
            continue
        try:
            rows = json.loads(daemon.runners_json or "[]")
        except ValueError:
            rows = []
        if not isinstance(rows, list):
            continue
        if newest is None or reported_at > newest:
            newest = reported_at
            default = daemon.runners_default
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            existing = profiles.get(name)
            if existing is not None and existing["_reported_at"] >= reported_at:
                continue
            entry = dict(row)
            entry["_reported_at"] = reported_at
            profiles[name] = entry
    out = list(profiles.values())
    for row in out:
        row.pop("_reported_at", None)
    out.sort(
        key=lambda row: (
            row.get("cost_rank") is None,
            row.get("cost_rank") if row.get("cost_rank") is not None else 0,
            str(row.get("name") or ""),
        )
    )
    stale = newest is not None and (now - newest).total_seconds() > _RUNNERS_STALE_SECONDS
    return {
        "profiles": out,
        "default": default,
        "stale": stale,
        "reported_at": newest.isoformat() if newest else None,
    }


_LIVE_RUNS_STALE_SECONDS = 300


def _live_runs_views(db: Session, repos: list[Repo]) -> dict[str, Any]:
    """Account-scoped live/coexisting-runs view (#258)."""
    repo_ids = {repo.id for repo in repos}
    if not repo_ids:
        return {"runs": [], "stale": False, "generated_at": None, "spawn_max_concurrent": None}
    now = datetime.now(timezone.utc)
    runs: dict[str, dict[str, Any]] = {}
    newest_reported_at: datetime | None = None
    spawn_max_concurrent: int | None = None
    daemons = db.execute(select(Daemon).where(Daemon.repo_id.in_(repo_ids))).scalars()
    for daemon in daemons:
        reported_at = _dt(daemon.live_runs_updated_at)
        if reported_at is None:
            continue
        if newest_reported_at is None or reported_at > newest_reported_at:
            newest_reported_at = reported_at
            spawn_max_concurrent = daemon.spawn_max_concurrent
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
        "spawn_max_concurrent": spawn_max_concurrent,
    }


_PR_REVIEW_QUEUE_STALE_SECONDS = 300


def _pr_review_queue_views(db: Session, repos: list[Repo]) -> dict[str, Any]:
    """Account-scoped open-PR review queue (#259)."""
    repo_ids = {repo.id for repo in repos}
    if not repo_ids:
        return {"prs": [], "stale": False, "generated_at": None}
    now = datetime.now(timezone.utc)
    prs: dict[str, dict[str, Any]] = {}
    newest_reported_at: datetime | None = None
    daemons = db.execute(select(Daemon).where(Daemon.repo_id.in_(repo_ids))).scalars()
    for daemon in daemons:
        reported_at = _dt(daemon.pr_review_queue_updated_at)
        if reported_at is None:
            continue
        if newest_reported_at is None or reported_at > newest_reported_at:
            newest_reported_at = reported_at
        try:
            entries = json.loads(daemon.pr_review_queue_json or "[]")
        except ValueError:
            entries = []
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            repo_label = str(entry.get("repo_label") or "")
            number = entry.get("number")
            if not (repo_label and number):
                continue
            pr_key = f"{repo_label.casefold()}#{number}"
            existing = prs.get(pr_key)
            if existing is not None and existing["_reported_at"] >= reported_at:
                continue
            row = dict(entry)
            row["_reported_at"] = reported_at
            prs[pr_key] = row
    out = list(prs.values())
    for row in out:
        row.pop("_reported_at", None)
    out.sort(key=lambda row: row.get("created_at") or "")
    stale = bool(newest_reported_at) and (now - newest_reported_at).total_seconds() > _PR_REVIEW_QUEUE_STALE_SECONDS
    return {
        "prs": out,
        "stale": stale,
        "generated_at": newest_reported_at.isoformat() if newest_reported_at else None,
    }


_RUN_LEDGER_STALE_SECONDS = 300
# Match the daemon's published envelope; the loom asks for all of it before
# selecting a 6h→7d shelf window.
_RUN_LEDGER_API_LIMIT = 256


def _run_ledger_views(
    db: Session,
    repos: list[Repo],
    limit: int,
    *,
    span_seconds: int | None = None,
) -> dict[str, Any]:
    """Account-scoped closed-run receipt feed (#271)."""
    repo_ids = {repo.id for repo in repos}
    if not repo_ids:
        return {"rows": [], "stale": False, "generated_at": None}
    now = datetime.now(timezone.utc)
    rows: dict[str, dict[str, Any]] = {}
    newest_reported_at: datetime | None = None
    daemons = db.execute(select(Daemon).where(Daemon.repo_id.in_(repo_ids))).scalars()
    for daemon in daemons:
        reported_at = _dt(daemon.run_ledger_updated_at)
        if reported_at is None:
            continue
        if newest_reported_at is None or reported_at > newest_reported_at:
            newest_reported_at = reported_at
        try:
            entries = json.loads(daemon.run_ledger_json or "[]")
        except ValueError:
            entries = []
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            ended_value = entry.get("ended_at")
            if isinstance(ended_value, str):
                try:
                    ended_value = datetime.fromisoformat(ended_value.replace("Z", "+00:00"))
                except ValueError:
                    ended_value = None
            ended_at = _dt(ended_value) if isinstance(ended_value, datetime) else None
            if (
                span_seconds is not None
                and (ended_at is None or (now - ended_at).total_seconds() > span_seconds)
            ):
                continue
            run_key = str(entry.get("run_id") or "")
            if not run_key:
                continue
            existing = rows.get(run_key)
            if existing is not None and existing["_reported_at"] >= reported_at:
                continue
            row = dict(entry)
            row["_reported_at"] = reported_at
            rows[run_key] = row
    out = list(rows.values())
    for row in out:
        row.pop("_reported_at", None)
    out.sort(key=lambda row: row.get("ended_at") or "", reverse=True)
    stale = bool(newest_reported_at) and (now - newest_reported_at).total_seconds() > _RUN_LEDGER_STALE_SECONDS
    return {
        "rows": out[:limit],
        "stale": stale,
        "generated_at": newest_reported_at.isoformat() if newest_reported_at else None,
    }


def _config_change_requests_view(db: Session, repos: list[Repo], settings: Any) -> dict[str, Any]:
    """Account-scoped pending config-change requests (loom-envelope Phase 2)."""
    repo_ids = {repo.id for repo in repos}
    if not repo_ids:
        return {"requests": [], "generated_at": None}
    repo_labels = {repo.id: repo.repo_full_name for repo in repos}
    rows = db.execute(
        select(ConfigChangeRequest)
        .where(ConfigChangeRequest.repo_id.in_(repo_ids))
        .where(ConfigChangeRequest.status == ConfigChangeRequest.STATUS_PENDING)
        .order_by(ConfigChangeRequest.created_at)
    ).scalars()
    base_url = str(getattr(settings, "public_base_url", "") or "").rstrip("/")
    out = []
    for row in rows:
        out.append(
            {
                "id": row.id,
                "repo_label": repo_labels.get(row.repo_id, ""),
                "config_key": row.config_key,
                "current_value": row.current_value,
                "requested_value": row.requested_value,
                "reason": row.reason,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "expires_at": row.expires_at.isoformat() if row.expires_at else None,
                "approve_url": f"{base_url}/config-approve/{row.id}" if base_url else f"/config-approve/{row.id}",
            }
        )
    return {
        "requests": out,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _iso(value: datetime | None) -> str | None:
    value = _dt(value)
    return value.isoformat() if value else None


def _repo_view_out(row: dict[str, Any]) -> dict[str, Any]:
    repo: Repo = row["repo"]
    return {
        "id": repo.id,
        "dispatch_default": bool(row.get("dispatch_default")),
        "repo_full_name": repo.repo_full_name,
        "forge": repo.forge,
        "forge_repo_id": repo.forge_repo_id,
        "repo_owner": repo.repo_owner,
        "repo_name": repo.repo_name,
        "default_branch": repo.default_branch,
        "created_at": _iso(repo.created_at),
        "updated_at": _iso(repo.updated_at),
        "created_label": _age_label(repo.created_at),
        "updated_label": _age_label(repo.updated_at),
        "daemon_count": row["daemon_count"],
        "daemon_status": row["daemon_status"],
        "daemon_label": row["daemon_label"],
        "daemon_last_seen": row["daemon_last_seen"],
        "daemon_last_seen_at": _iso(row.get("daemon_last_seen_at")),
        "latest_daemon_name": row["latest_daemon_name"],
        "gates": row["gates"],
        "environment_default": row.get("environment_default"),
        "environments": row.get("environments", []),
        "setup_command": row["setup_command"],
        "telegram_pair_enabled": True,
    }


def _installation_out(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "installation_id": row.installation_id,
        "target_login": row.target_login,
        "target_type": row.target_type,
        "created_at": _iso(row.created_at),
        "last_synced_at": _iso(row.last_synced_at),
        "last_synced_label": _age_label(row.last_synced_at),
    }


def _installed_repo_out(row: GitHubInstalledRepo, *, connected_names: set[str]) -> dict[str, Any]:
    return {
        "id": row.id,
        "github_installation_id": row.github_installation_id,
        "repo_full_name": row.repo_full_name,
        "forge_repo_id": row.forge_repo_id,
        "is_private": row.is_private,
        "default_branch": row.default_branch,
        "github_pushed_at": _iso(row.github_pushed_at),
        "github_updated_at": _iso(row.github_updated_at),
        "last_seen_at": _iso(row.last_seen_at),
        "pushed_label": _age_label(row.github_pushed_at),
        "updated_label": _age_label(row.github_updated_at),
        "last_seen_label": _age_label(row.last_seen_at),
        "connected": row.repo_full_name.casefold() in connected_names,
    }


@router.get("/v1/dashboard/repos")
def dashboard_repos_api(
    request: Request,
    installation_id: str | None = None,
    notice: str | None = None,
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Account-scoped repo-management JSON twin for the SvelteKit `/repos` route (#327)."""
    account_id = _account_id(request, db)
    if account_id is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    account = db.get(Account, account_id)
    if account is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    notice = notice or _github_auto_sync_if_needed(request, db, account.id)
    settings = request.app.state.settings
    repos = _repos(db, account.id)
    repo_views = _repo_views(db, repos)
    installed = _installed_repos(db, account.id)
    installations = _installations(db, account.id)
    connected_names = {repo.repo_full_name.casefold() for repo in repos}
    return JSONResponse(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "account": {"id": account.id, "github_login": account.github_login},
            "connected_repos": [_repo_view_out(row) for row in repo_views],
            "connected_count": len(repos),
            "installations": [_installation_out(row) for row in installations],
            "installed_repos": [
                _installed_repo_out(row, connected_names=connected_names)
                for row in installed
            ],
            "github_sync_configured": _github_sync_configured(request),
            "oauth_ready": _github_oauth_ready(request),
            "install_url": settings.github_install_url,
            "github_app_slug": settings.github_app_slug,
            "github_bot_login": settings.github_bot_login.strip().lstrip("@"),
            "notice": _notice_text(notice),
            "setup_installation_id": installation_id or "",
        }
    )


@router.get("/v1/dashboard/quota")
def dashboard_quota_api(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """JSON twin of ``runner_quotas`` for the SvelteKit frontend."""
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


@router.get("/v1/dashboard/runners")
def dashboard_runners_api(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """JSON twin for the spool-rack panel (#328)."""
    account_id = _account_id(request, db)
    if account_id is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    account = db.get(Account, account_id)
    if account is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    repos = _repos(db, account.id)
    views = _runners_views(db, repos)
    pending = wake_requests.pending_for_account(db, account.id)
    return JSONResponse(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            **views,
            "wake_request": wake_requests.view(pending) if pending else None,
        }
    )


@router.post("/v1/dashboard/runners/wake-request")
async def dashboard_runners_wake_request(
    request: Request, db: Session = Depends(get_db)
) -> JSONResponse:
    """Park a one-shot wake request for the next thought (#328)."""
    account_id = _account_id(request, db)
    if account_id is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    try:
        payload = await request.json()
    except ValueError:
        payload = None
    profile = str((payload or {}).get("profile") or "").strip()
    if not profile or len(profile) > 64:
        return JSONResponse({"detail": "profile name required"}, status_code=422)
    repo_label = str((payload or {}).get("repo_label") or "").strip() or None
    environment = str((payload or {}).get("environment") or "").strip() or None
    if repo_label is not None:
        repo = db.execute(
            select(Repo).where(
                Repo.account_id == account_id,
                Repo.repo_full_name == repo_label,
            )
        ).scalar_one_or_none()
        if repo is None:
            return JSONResponse({"detail": "repo_label is not connected to this account"}, status_code=422)
    if environment is not None and environment not in _DISPATCH_ENVIRONMENTS:
        return JSONResponse({"detail": "unknown dispatch environment"}, status_code=422)
    row = wake_requests.create(
        db,
        account_id,
        profile,
        repo_label=repo_label,
        environment=environment,
    )
    return JSONResponse({"wake_request": wake_requests.view(row)})


@router.delete("/v1/dashboard/runners/wake-request/{request_id}")
def dashboard_runners_wake_request_cancel(
    request_id: str, request: Request, db: Session = Depends(get_db)
) -> JSONResponse:
    """Cancel a pending wake request."""
    account_id = _account_id(request, db)
    if account_id is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    row = wake_requests.cancel(db, account_id, request_id)
    if row is None:
        return JSONResponse({"detail": "unknown wake request"}, status_code=404)
    return JSONResponse({"wake_request": wake_requests.view(row)})


@router.get("/v1/dashboard/live-runs")
def dashboard_live_runs_api(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """Account-scoped live/coexisting-runs view (#258)."""
    account_id = _account_id(request, db)
    if account_id is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    account = db.get(Account, account_id)
    if account is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    repos = _repos(db, account.id)
    view = _live_runs_views(db, repos)
    # #476 wyrd §3: a stop is asynchronous — the daemon consumes it on its
    # next sync. Marking the row here (rather than letting the client hold
    # the fact in memory) is what lets the cell keep saying "stopping"
    # across a reload, and keeps it from claiming a terminal state the
    # system has not reached yet.
    stopping = run_stop_requests.pending_run_ids(db, account_id)
    runs = [
        {
            **row,
            "stop_requested": bool(
                stopping & {str(row.get("run_id") or ""), str(row.get("id") or "")}
            ),
        }
        for row in view["runs"]
    ]
    return JSONResponse(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "runs": runs,
            "stale": view["stale"],
            "reported_at": view["generated_at"],
            "spawn_max_concurrent": view["spawn_max_concurrent"],
        }
    )


@router.post("/v1/dashboard/runs/{run_id}/stop")
def dashboard_run_stop(run_id: str, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """Park a stop for a burning run (#476 wyrd §3, the user-side affordance).

    Authority note. The ``stop:`` outbox verb is deliberately restricted to a
    run's own dispatchees, so a run cannot reach sideways and kill a sibling
    it knows nothing about. That restriction is about *runs* as principals —
    it bounds an agent's blast radius to work it started. A human account
    owner is a different principal: every run on their daemons burns their
    quota, on their machine, under their authority, and the whole point of
    this affordance is the case the dispatch-edge rule cannot serve — a
    top-level resident thought nobody dispatched. So the check here is
    account scope and nothing narrower: any run the account can *see* on its
    live-runs view, it may stop. The daemon still enforces its own half (it
    only kills runs in its own control registry), so an account cannot reach
    into someone else's daemon by guessing a run id.
    """
    account_id = _account_id(request, db)
    if account_id is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    run_id = str(run_id or "").strip()
    if not run_id or len(run_id) > 64:
        return JSONResponse({"detail": "run id required"}, status_code=422)
    repos = _repos(db, account_id)
    live = _live_runs_views(db, repos)["runs"]
    known = {str(row.get("run_id") or "") for row in live} | {
        str(row.get("id") or "") for row in live
    }
    if run_id not in known:
        # Not 404-as-authorization: the account genuinely has nothing burning
        # under that handle, and parking a stop for it would sit pending
        # until its TTL and then expire silently.
        return JSONResponse({"detail": "no live run with that id"}, status_code=404)
    row = run_stop_requests.create(db, account_id, run_id)
    return JSONResponse({"stop_request": run_stop_requests.view(row)})


@router.get("/v1/dashboard/pr-review-queue")
def dashboard_pr_review_queue_api(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """Account-scoped open-PR review queue (#259)."""
    account_id = _account_id(request, db)
    if account_id is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    account = db.get(Account, account_id)
    if account is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    repos = _repos(db, account.id)
    view = _pr_review_queue_views(db, repos)
    return JSONResponse(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "prs": view["prs"],
            "stale": view["stale"],
            "reported_at": view["generated_at"],
        }
    )


@router.get("/v1/dashboard/run-ledger")
def dashboard_run_ledger_api(
    request: Request,
    limit: int = 10,
    span_seconds: int | None = None,
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Account-scoped closed-run receipt feed (#271)."""
    account_id = _account_id(request, db)
    if account_id is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    account = db.get(Account, account_id)
    if account is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    repos = _repos(db, account.id)
    capped = max(1, min(limit, _RUN_LEDGER_API_LIMIT))
    span = None if span_seconds is None else max(1, min(span_seconds, 7 * 24 * 3600))
    view = _run_ledger_views(db, repos, capped, span_seconds=span)
    return JSONResponse(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "rows": view["rows"],
            "stale": view["stale"],
            "reported_at": view["generated_at"],
        }
    )


@router.get("/v1/dashboard/config-requests")
def dashboard_config_requests_api(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """Account-scoped pending config-change requests (loom-envelope Phase 2)."""
    account_id = _account_id(request, db)
    if account_id is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    account = db.get(Account, account_id)
    if account is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    repos = _repos(db, account.id)
    view = _config_change_requests_view(db, repos, request.app.state.settings)
    return JSONResponse(
        {
            "generated_at": view["generated_at"],
            "requests": view["requests"],
        }
    )


@router.get("/v1/dashboard/surface")
def dashboard_surface_api(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """Account-scoped discovered work surface for the SvelteKit frontend."""
    account_id = _account_id(request, db)
    if account_id is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    account = db.get(Account, account_id)
    if account is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    try:
        files = json.loads(account.surface_json or "[]")
    except ValueError:
        files = []
    if not isinstance(files, list):
        files = []
    return JSONResponse(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "files": files,
            "reported_at": account.surface_updated_at.isoformat() if account.surface_updated_at else None,
        }
    )


def _activity_row_out(view: dict[str, Any]) -> dict[str, Any]:
    record: ActivityRecord = view["record"]

    def utc_iso(value: datetime | None) -> str | None:
        if value is None:
            return None
        # Activity timestamps are UTC on ingestion, but the database columns
        # predate timezone-aware storage and return naive datetimes. A bare
        # ISO string is parsed as browser-local time, making every scheduled
        # wake appear offset by the viewer's timezone (and often "overdue").
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()

    return {
        "id": record.record_id,
        "kind": record.kind,
        "source": view["source_label"],
        "status": record.status,
        "phase": record.phase,
        "bucket": view["bucket"],
        "summary": view["summary_compact"],
        "repo_label": view["repo_name"],
        "daemon_name": view["daemon_name"],
        "conversation_key": record.conversation_key,
        "runner": {"shell": view["shell"], "core": view["core"], "summary": view["runner_summary"]},
        "branch": record.branch,
        "pr_number": record.pr_number,
        "started_at": utc_iso(record.started_at),
        "updated_at": utc_iso(record.updated_at),
        "scheduled_for": utc_iso(record.scheduled_for),
        "defer_until": utc_iso(record.defer_until),
        "reported_at": utc_iso(record.reported_at),
        "links": view["links"],
    }


@router.get("/v1/dashboard/activity")
def dashboard_activity_api(
    request: Request,
    repo_id: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Account-scoped activity feed (#327 Jinja-removal, /activity half)."""
    account_id = _account_id(request, db)
    if account_id is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    account = db.get(Account, account_id)
    if account is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    repos = _repos(db, account.id)
    base_views = _activity_views(db, repos, repo_id=repo_id or None)
    views = _activity_views(db, repos, repo_id=repo_id or None, kind=kind or None, status=status or None)
    capped = max(1, min(limit, 300))
    kinds = sorted({view["record"].kind for view in base_views} | {"run", "scheduled", "respawn"})
    statuses = sorted({view["record"].status for view in base_views if view["record"].status} | {"running", "pending", "scheduled"})
    return JSONResponse(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "rows": [_activity_row_out(view) for view in views[:capped]],
            "total": len(views),
            "kinds": kinds,
            "statuses": statuses,
            "repos": [{"id": repo.id, "label": repo.repo_full_name} for repo in repos],
        }
    )


@router.get("/repos")
def repos_redirect() -> RedirectResponse:
    """308: repo-management page now lives in the SvelteKit `/repos` route (#327)."""
    return RedirectResponse(url="/", status_code=308)


@router.get("/activity")
def activity_redirect() -> RedirectResponse:
    """308: unbounded legacy activity feed superseded by the SvelteKit ``/activity`` route (#327)."""
    return RedirectResponse(url="/", status_code=308)

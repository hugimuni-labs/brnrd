"""Dashboard activity aggregation for the brnrd web control deck."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from brnrd import wake_requests
from brnrd.activity_records import dedupe_activity_records, fresh_activity_records
from brnrd.auth import get_db
from brnrd.models import Account, ActivityRecord, ConfigChangeRequest, Daemon, Event, GitHubInstalledRepo, Repo

from .routes import (
    _account_id,
    _age_label,
    _dt,
    _github_auto_sync_if_needed,
    _github_oauth_ready,
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
    """Parse a collector's own ``updated_at`` (``claude_usage``/``codex_status``
    shape: ``%Y-%m-%dT%H:%M:%SZ``), or ``None`` for anything else — never
    raises, since this is best-effort staleness math, not a validated field.
    """
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
    """Real per-shell quota windows from the daemons' own reports (#237).

    Reads the last ``PUT /v1/daemons/quota`` snapshot per connected daemon
    (`src/brr/gates/cloud.py::_quota_snapshot` is the writer). A daemon that
    hasn't reported inside ``_QUOTA_STALE_SECONDS`` still shows its last
    numbers, but flagged ``stale`` rather than silently going quiet — the
    honest fallback the ticket asked for. A shell with active runs
    (``runner_stats``) but no quota report at all (older daemon build, or
    cold cache) still renders as an explicit ``unknown`` placeholder card,
    not omitted — "quota provider pending" beats a missing panel.

    Staleness is measured against the *scrape's own* ``updated_at`` when the
    shell payload carries one, not the daemon's publish timestamp. Claude's
    quota shell is a cached interactive ``/usage`` PTY scrape that only
    refreshes while a Claude run is actively heartbeating — with no run
    active, the daemon still PUTs this endpoint every poll tick with numbers
    that haven't actually changed in hours, so gating staleness on
    ``quota_updated_at`` alone never fires (that timestamp is always fresh)
    and the dashboard shows old numbers as if they were live — the reported
    "lying Claude usage panel" bug (2026-07-07). Falls back to the daemon's
    publish time only for shells that carry no per-scrape timestamp at all
    (older daemon builds). Codex was assumed exempt here ("no comparable
    idle-gap") until a live 2026-07-09 screenshot showed the identical
    lying-panel symptom on its 5h window — codex_status.py's collector
    re-reads the same rollout file on every poll tick whether or not a run
    is active, and used to stamp ``updated_at`` with wall-clock now on every
    read; fixed at the source (``codex_status.py::parse_token_count`` now
    uses the rollout event's own timestamp) rather than special-cased here.
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


# The catalog itself changes rarely (a shell install/upgrade, a config
# repin) but it rides the same ~3s publish tick as quota, so a report this
# old means the daemon is gone, not the rack unchanged.
_RUNNERS_STALE_SECONDS = 600


def _runners_views(db: Session, repos: list[Repo]) -> dict[str, Any]:
    """Account-scoped runner catalog: the spool rack (#328).

    Reads the last ``PUT /v1/daemons/runners`` snapshot per connected daemon
    (`src/brr/gates/cloud.py::_runners_snapshot` is the writer). Freshest
    report wins per profile name — same merge rule `_quota_views` applies
    per shell — and the freshest daemon's own ``default`` pin is the one
    shown, since the pin is daemon-local config.
    """
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
            # Same "freshest report wins" rule the entries dict below
            # applies per-run-id — one daemon process may register under
            # several repos, each a separate row; the most recently
            # reported row's own config value is the one that's live.
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


_PR_REVIEW_QUEUE_STALE_SECONDS = 300  # daemon publishes on its ~25-30s poll loop (#259)


def _pr_review_queue_views(db: Session, repos: list[Repo]) -> dict[str, Any]:
    """Account-scoped open-PR review queue (#259).

    Reads the last ``PUT /v1/daemons/pr-review-queue`` snapshot per connected
    daemon (`src/brr/gates/cloud.py::_pr_review_snapshot` is the writer).
    Several ``Daemon`` rows can report the same underlying account queue; dedupe
    by ``repo_label`` + PR number and keep the freshest daemon report.
    """
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


_RUN_LEDGER_STALE_SECONDS = 300  # daemon publishes on the same fast dashboard loop (#271)


def _run_ledger_views(db: Session, repos: list[Repo], limit: int) -> dict[str, Any]:
    """Account-scoped closed-run receipt feed (#271).

    Reads the last ``PUT /v1/daemons/run-ledger`` snapshot per connected
    daemon (`src/brr/gates/cloud.py::_run_ledger_snapshot` is the writer).
    Several ``Daemon`` rows can report the same physical ledger; dedupe by
    ``run_id`` and keep the freshest daemon report. This is a receipt feed,
    so newest ``ended_at`` sorts first.
    """
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
    """Account-scoped pending config-change requests (loom-envelope Phase 2,
    kb/design-multi-workstream-concurrency.md "Named forks - round 2").

    Unlike the daemon-published snapshots above (live-runs, PR queue, run
    ledger), ``ConfigChangeRequest`` rows are written directly by the
    daemon's own ``POST /v1/daemons/config-requests`` call
    (``src/brnrd/routers/config_approval.py``) — there is no publish/mirror
    step and no staleness concept, this queries the table directly. Phase 2
    shipped the device-flow (mint, approve page, outcome-over-inbox) with
    no dashboard surface for a pending request at all; this is that surface
    — read-only, linking to the existing session-gated ``/config-approve/{id}``
    page for the actual decision rather than re-implementing the decide
    action in the SPA.
    """
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


def _iso(value: datetime | None) -> str | None:
    value = _dt(value)
    return value.isoformat() if value else None


def _repo_view_out(row: dict[str, Any], *, github_bot_user_login: str) -> dict[str, Any]:
    repo: Repo = row["repo"]
    return {
        "id": repo.id,
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
        "setup_command": row["setup_command"],
        "telegram_pair_enabled": True,
        "bot_invite_enabled": repo.forge == "github" and bool(github_bot_user_login),
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
    """Account-scoped repo-management JSON twin for the SvelteKit `/repos`
    route (#327 Jinja-removal). Same session-cookie auth as the other
    dashboard JSON endpoints; unauthenticated fetches get a JSON 401, not a
    login redirect.
    """
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
    github_bot_user_login = settings.github_bot_user_login.strip().lstrip("@")
    return JSONResponse(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "account": {"id": account.id, "github_login": account.github_login},
            "connected_repos": [
                _repo_view_out(row, github_bot_user_login=github_bot_user_login)
                for row in repo_views
            ],
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
            "github_bot_user_login": github_bot_user_login,
            "notice": _notice_text(notice),
            "setup_installation_id": installation_id or "",
        }
    )


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


@router.get("/v1/dashboard/runners")
def dashboard_runners_api(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """JSON twin for the spool-rack panel (#328): every Shell+Core profile
    the account's daemons can actually wake — locally discovered, cheapest
    first — plus the current default pin. Read-only by design: the change
    path is conversational (config-change request → approve page), not a
    selector on this payload.
    """
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
    """Tap a spool-rack row (#328): park a one-shot "next wake on this
    profile" request. No approve step — the tapper *is* the account owner,
    and a confirm modal on your own tap is ceremony. The request stays
    cancelable until a wake actually consumes it (chip → DELETE below);
    the daemon learns of it within one catalog-publish tick via the
    ``PUT /v1/daemons/runners`` response piggyback.

    Deliberately not a durable default change: that keeps its conversational
    config-change path. This is "hand the next thought this body", nothing
    more — the rack stays a being-view, not a control panel.
    """
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
    row = wake_requests.create(db, account_id, profile)
    return JSONResponse({"wake_request": wake_requests.view(row)})


@router.delete("/v1/dashboard/runners/wake-request/{request_id}")
def dashboard_runners_wake_request_cancel(
    request_id: str, request: Request, db: Session = Depends(get_db)
) -> JSONResponse:
    """Cancel a pending wake request (chip tap). If a wake already consumed
    it, the row comes back ``consumed`` rather than a 409 — the ~seconds
    race between cancel and dispatch is inherent, and "it already fired"
    is an answer, not an error."""
    account_id = _account_id(request, db)
    if account_id is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    row = wake_requests.cancel(db, account_id, request_id)
    if row is None:
        return JSONResponse({"detail": "unknown wake request"}, status_code=404)
    return JSONResponse({"wake_request": wake_requests.view(row)})


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
            "spawn_max_concurrent": view["spawn_max_concurrent"],
        }
    )


@router.get("/v1/dashboard/pr-review-queue")
def dashboard_pr_review_queue_api(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """Account-scoped open-PR review queue (#259) for the SvelteKit frontend.
    Same session-cookie auth as the other dashboard JSON endpoints.
    """
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
def dashboard_run_ledger_api(request: Request, limit: int = 10, db: Session = Depends(get_db)) -> JSONResponse:
    """Account-scoped closed-run receipt feed (#271) for the SvelteKit frontend."""
    account_id = _account_id(request, db)
    if account_id is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    account = db.get(Account, account_id)
    if account is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    repos = _repos(db, account.id)
    capped = max(1, min(limit, 50))
    view = _run_ledger_views(db, repos, capped)
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
    """Account-scoped pending config-change requests (loom-envelope Phase 2)
    for the SvelteKit frontend. Same session-cookie auth as the other
    dashboard JSON endpoints. Read-only: the actual approve/reject action
    stays on the existing ``/config-approve/{id}`` page (``approve_url``
    below), not duplicated here.
    """
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


@router.get("/v1/dashboard/plans")
def dashboard_plans_api(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """Account-scoped decisions space (#324 Phase 0) for the SvelteKit frontend.

    The CPS files (CS5 ``plans/<repo>/active.md``, cross-repo plan, CS7
    ``ledger/decisions.md``) were already mirrored via ``PUT
    /v1/daemons/plans`` and rendered raw on the Jinja ``/plans`` page — but
    that page lost its discoverability when the SvelteKit build took over
    "/", leaving the resident's entire scheduling mechanism (the ranked-move
    list) invisible from the surface the user actually watches. Read-only,
    same session-cookie auth as the other dashboard JSON endpoints;
    structure (section parsing, staleness) is the frontend's job — no
    storage-schema decision here, per the Phase 0 boundary.
    """
    account_id = _account_id(request, db)
    if account_id is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    account = db.get(Account, account_id)
    if account is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    repos = _repos(db, account.id)
    plans = [
        {
            "repo_label": repo.repo_full_name,
            "plan_md": repo.plan_md or "",
            "updated_at": repo.plan_updated_at.isoformat() if repo.plan_updated_at else None,
        }
        for repo in repos
        if (repo.plan_md or "").strip()
    ]
    return JSONResponse(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "plans": plans,
            "cross_repo_plan_md": (account.cross_repo_plan_md or "").strip(),
            "decisions_md": (account.decision_ledger_md or "").strip(),
            "reported_at": account.plans_updated_at.isoformat() if account.plans_updated_at else None,
        }
    )


def _activity_row_out(view: dict[str, Any]) -> dict[str, Any]:
    """JSON projection of one `_activity_views` row for the SvelteKit
    frontend: raw ISO timestamps plus the server-derived `bucket` (the
    status-set folding at the top of this module is backend knowledge the
    client shouldn't re-own); presentation labels are the client's job,
    same division as the other dashboard JSON twins.
    """
    record: ActivityRecord = view["record"]
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
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        "scheduled_for": record.scheduled_for.isoformat() if record.scheduled_for else None,
        "defer_until": record.defer_until.isoformat() if record.defer_until else None,
        "reported_at": record.reported_at.isoformat() if record.reported_at else None,
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
    """Account-scoped activity feed (#327 Jinja-removal, /activity half) for
    the SvelteKit frontend. Same session-cookie auth + filter params as the
    Jinja page this supersedes, with one deliberate difference: the response
    is bounded (``limit``, capped) — the legacy page rendered every record
    unbounded, which is exactly the 282-row pileup that made it unreadable.
    ``total`` carries the pre-limit count so the client can say "N of M".
    ``kinds``/``statuses``/``repos`` feed the filter UI from the repo-scoped
    (but kind/status-unfiltered) view, mirroring the Jinja page's behavior.
    """
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


@router.get("/plans")
def plans_redirect() -> RedirectResponse:
    """First real Jinja template cut (kb plan-jinja-removal.md Phase 2,
    plans half): the raw-``<pre>`` CPS page is fully superseded by the
    dashboard's decisions-space panel (#324 Phase 0), which renders the
    same ``PUT /v1/daemons/plans`` mirror structured. The URL stays alive
    — old-page nav links and bookmarks land on the panel, not a 404.
    """
    return RedirectResponse(url="/", status_code=308)


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


@router.get("/repos")
def repos_redirect() -> RedirectResponse:
    """#327 Jinja cut: the repo-management page now lives in the SvelteKit
    `/repos` route and reads `GET /v1/dashboard/repos`. In production the
    static SPA owns the path; this backend shim only catches bare-uvicorn
    deployments and old passthru configs.
    """
    return RedirectResponse(url="/", status_code=308)


@router.get("/activity")
def activity_redirect() -> RedirectResponse:
    """Second Jinja template cut (#327, same shape as ``plans_redirect``
    above): the unbounded legacy activity feed is superseded by the
    SvelteKit ``/activity`` route (``src/frontend/src/routes/activity``),
    which reads ``dashboard_activity_api`` bounded. In production the
    static build serves ``/activity`` directly (Upsun passthru no longer
    lists it); this backend route is only reachable from a bare-uvicorn
    deployment, where old nav links and bookmarks land on the dashboard
    instead of a 404.
    """
    return RedirectResponse(url="/", status_code=308)

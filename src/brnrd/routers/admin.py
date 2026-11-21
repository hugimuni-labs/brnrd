"""Operator read-outs, for the few accounts named in ``BRNRD_ADMIN_GITHUB_LOGINS``.

The server had no admin gate before this. The gate is the signed-in
session's account: its GitHub login must be on the list in settings
(comma-separated, case-insensitive). Unset or empty means nobody.

Everyone else (anonymous, signed in but unlisted, any visitor while the list
is empty) gets the same 404 an unknown route gets, so the surface is not
discoverable by probing it. The page that reads this (``/account/views``)
is linked from nowhere.

The 30-day page-view read-out, the funnel at /metrics (14 days and 8 ISO
weeks, plus the same numbers as JSON), and the first paying subscribers
(who holds an install-session seat and why the rest do not) are read-only.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from html import escape
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import billing, funnel
from ..auth import account_id_from_session_cookie, get_db
from ..models import Account
from ..page_views import PageViewCount

router = APIRouter(prefix="/v1/admin", tags=["admin"], include_in_schema=False)

pages = APIRouter(include_in_schema=False)

PAGE_VIEW_DAYS = 30
_PRIVATE = {"Cache-Control": "private, no-store"}


def _admin_or_404(request: Request, db: Session) -> Account:
    allowed = request.app.state.settings.admin_github_logins
    account_id = account_id_from_session_cookie(request, db) if allowed else None
    account = db.get(Account, account_id) if account_id else None
    if account is None or account.deleted_at is not None or (account.github_login or "").lower() not in allowed:
        raise HTTPException(status_code=404, detail="Not Found", headers=_PRIVATE)
    return account


@router.get("/page-views")
def page_views(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Daily views per (day, path, referrer host) for the last 30 UTC days.

    ``views`` excludes the bot user-agent family; ``bot_views`` is that family
    alone, so a crawler burst never reads as a launch. ``referer_host`` is
    ``""`` for a direct visit (no referer sent).
    """
    _admin_or_404(request, db)
    today = dt.datetime.now(dt.timezone.utc).date()
    since = today - dt.timedelta(days=PAGE_VIEW_DAYS - 1)
    return {"since": since.isoformat(), "until": today.isoformat(), "rows": _view_rows(db, since, today)}


def _view_rows(db: Session, since: dt.date, today: dt.date) -> list[dict[str, Any]]:
    P = PageViewCount
    stmt = select(P.day, P.path, P.referer_host, P.ua_family, P.views).where(P.day.between(since, today))
    totals: dict[tuple[dt.date, str, str], list[int]] = defaultdict(lambda: [0, 0])
    for day, path, host, ua, n in db.execute(stmt):
        totals[(day, path, host or "")][1 if ua == "bot" else 0] += n
    # Newest day first; within a day, path, then the busiest referrer first.
    order = sorted(totals.items(), key=lambda kv: (-kv[0][0].toordinal(), kv[0][1], -kv[1][0], kv[0][2]))
    return [
        {"day": day.isoformat(), "path": path, "referer_host": host, "views": v, "bot_views": b}
        for (day, path, host), (v, b) in order
    ]


def _funnel_row(row: dict[str, Any], campaigns: list[str]) -> str:
    camps = "".join(f"<td>{row['campaigns'][path]}</td>" for path in campaigns)
    return (
        f"<tr><th scope='row'>{escape(row['period'])}</th><td>{row['landing']}</td>{camps}"
        f"<td>{row['signups']}</td><td>{row['installs_started']}</td><td>{row['installs_finished']}</td>"
        f"<td>{row['active_day3']}</td><td>{row['paid']}</td></tr>"
    )


def _metrics_payload(request: Request, db: Session) -> dict[str, Any]:
    _admin_or_404(request, db)
    return funnel.build(db, request.app.state.settings)


@router.get("/metrics")
def metrics_json(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """The /metrics tables as JSON, behind the same gate. Not a public stat."""
    return JSONResponse(_metrics_payload(request, db), headers=_PRIVATE)


@pages.get("/metrics")
def metrics(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Fourteen UTC days and eight ISO weeks of the growth steps."""
    payload = _metrics_payload(request, db)
    today = dt.date.fromisoformat(payload["until"])
    since = dt.date.fromisoformat(payload["daily_since"])
    campaigns = payload["campaigns"]
    camp_heads = "".join(f"<th scope=\"col\">{escape(path)} visits</th>" for path in campaigns)
    head = (
        "<tr><th scope=\"col\">Period</th><th scope=\"col\">Landing</th>"
        f"{camp_heads}<th scope=\"col\">New signups</th><th scope=\"col\">Script downloads</th>"
        "<th scope=\"col\">Computers connected</th><th scope=\"col\">Active on day 3</th>"
        "<th scope=\"col\">New paid subscriptions</th></tr>"
    )
    views = _view_rows(db, since, today)
    view_rows = [
        f"<tr><td>{r['day']}</td><td>{escape(r['path'])}</td>"
        f"<td>{escape(r['referer_host'] or '(direct)')}</td>"
        f"<td>{r['views']}</td><td>{r['bot_views']}</td></tr>"
        for r in views
    ]
    if not view_rows:
        view_rows.append("<tr><td colspan='5'>No views in this window.</td></tr>")
    names = payload["excluded_logins"]

    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Metrics · brnrd</title>
  <link rel="stylesheet" href="/static/app.css">
</head>
<body>
<main class="metrics">
  <h1>Metrics</h1>
  <p>{payload['weekly_since']} to {payload['until']} (UTC). Landing, campaign visits and script downloads
    count origin requests and leave out recognized bots. A campaign path is counted and then sends the
    visitor home, so that visit is also in the landing count. The same numbers are at /v1/admin/metrics.</p>
  <p>Signups, computers connected, active on day 3 and paid subscriptions count distinct accounts.
    They leave out GitHub logins named in BRNRD_FUNNEL_EXCLUDE_GITHUB_LOGINS, the admin list, the member
    list and the first-subscriber exclude list ({names} names). Visit counts have no login to leave out.</p>
  <p>Computers connected is the day an account's first computer connected. Active on day 3 counts how many
    of those later showed a daemon sighting or a letter, 72 hours or more after that connection. The number
    is dated on the connection and rises when they return; a recent day stays 0 until then. A wake that
    never reaches this server is not in it. Paid subscriptions are the day the account's first paid invoice
    was recorded. Script downloads also count people reading the script.</p>
  <h2>By ISO week</h2>
  <p>The current week runs through today.</p>
  <div class="metrics-table">
    <table aria-label="Weekly visits and conversions">
      <thead>{head}</thead>
      <tbody>{''.join(_funnel_row(row, campaigns) for row in payload['weeks'])}</tbody>
    </table>
  </div>
  <h2>By day</h2>
  <p>{since} to {today}.</p>
  <div class="metrics-table">
    <table aria-label="Daily visits and conversions">
      <thead>{head}</thead>
      <tbody>{''.join(_funnel_row(row, campaigns) for row in payload['days'])}</tbody>
    </table>
  </div>
  <h2>Views by path and referrer</h2>
  <p>The last {funnel.DAILY_DAYS} days. Recognized bots are in their own column.</p>
  <div class="metrics-table">
    <table aria-label="Views by path and referrer">
      <thead><tr><th scope="col">Day</th><th scope="col">Path</th><th scope="col">Referrer host</th>
        <th scope="col">Views</th><th scope="col">Bots</th></tr></thead>
      <tbody>{''.join(view_rows)}</tbody>
    </table>
  </div>
</main>
</body>
</html>""",
        headers=_PRIVATE,
    )


@router.get("/first-subscribers")
def first_subscribers(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Every paying account in the order its first subscription started, with
    ``seat`` true for the ones shown the install-session link (while they stay
    subscribed) and ``skipped`` saying why the others are not."""
    _admin_or_404(request, db)
    settings = request.app.state.settings
    rows = billing.first_subscribers(db, settings)
    return {
        "seats": settings.first_subscribers_seats,
        "link": settings.first_subscribers_link,
        "rows": [
            {
                "github_login": r.github_login,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "seat": not r.skipped,
                "skipped": r.skipped,
            }
            for r in rows
        ],
    }

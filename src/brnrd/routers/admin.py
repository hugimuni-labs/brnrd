"""Operator read-outs, for the few accounts named in ``BRNRD_ADMIN_GITHUB_LOGINS``.

The server had no admin gate before this. The gate is the signed-in
session's account: its GitHub login must be on the list in settings
(comma-separated, case-insensitive). Unset or empty means nobody.

Everyone else (anonymous, signed in but unlisted, any visitor while the list
is empty) gets the same 404 an unknown route gets, so the surface is not
discoverable by probing it. The page that reads this (``/account/views``)
is linked from nowhere.

The 30-day page-view read-out, the 14-day funnel at /metrics, and the first
paying subscribers (who holds an install-session seat and why the rest do
not) are read-only.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from html import escape
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import billing
from ..auth import account_id_from_session_cookie, get_db
from ..models import Account, BillingLedgerEntry
from ..page_views import INSTALL_PATH, PROMO_PATH, PageViewCount

router = APIRouter(prefix="/v1/admin", tags=["admin"], include_in_schema=False)

pages = APIRouter(include_in_schema=False)

PAGE_VIEW_DAYS = 30
METRIC_DAYS = 14
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


@pages.get("/metrics")
def metrics(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Fourteen UTC days of origin views and local account/billing activity."""
    _admin_or_404(request, db)
    today = dt.datetime.now(dt.timezone.utc).date()
    since = today - dt.timedelta(days=METRIC_DAYS - 1)
    start = dt.datetime.combine(since, dt.time.min)
    end = dt.datetime.combine(today + dt.timedelta(days=1), dt.time.min)

    signup_day = func.date(Account.created_at)
    signups = {
        str(day): count
        for day, count in db.execute(
            select(signup_day, func.count())
            .where(Account.created_at >= start, Account.created_at < end)
            .group_by(signup_day)
        )
    }
    # The invoice-paid handler writes this grant with only the subscription ID
    # in metadata. Group across all history so a renewal cannot become a first
    # paid invoice simply because the original is outside the display window.
    # home_trial.start_once stamps this once, in the transaction of the
    # account's first successful machine connect (pairing hands the loom its
    # token, or the loom's first register/publish), and never moves it. So it
    # is the day the account's first computer connected. A second computer on
    # the same account is not counted. Accounts that connected before the
    # column existed (2026-11-17) were stamped at their next connect after.
    connect_day = func.date(Account.home_trial_started_at)
    connected = {
        str(day): count
        for day, count in db.execute(
            select(connect_day, func.count())
            .where(Account.home_trial_started_at >= start, Account.home_trial_started_at < end)
            .group_by(connect_day)
        )
    }

    ledger = BillingLedgerEntry
    first_paid = (
        select(func.min(ledger.created_at).label("received"))
        .where(ledger.op == "grant_subscriber_monthly")
        .group_by(ledger.metadata_json)
        .subquery()
    )
    paid_day = func.date(first_paid.c.received)
    paid = {
        str(day): count
        for day, count in db.execute(
            select(paid_day, func.count())
            .where(first_paid.c.received >= start, first_paid.c.received < end)
            .group_by(paid_day)
        )
    }

    views = _view_rows(db, since, today)
    promo_visits: dict[str, int] = defaultdict(int)
    downloads: dict[str, int] = defaultdict(int)
    for row in views:
        if row["path"] == PROMO_PATH:
            promo_visits[row["day"]] += row["views"]
        elif row["path"] == INSTALL_PATH:
            downloads[row["day"]] += row["views"]

    daily_rows = []
    for offset in range(METRIC_DAYS):
        day = (today - dt.timedelta(days=offset)).isoformat()
        daily_rows.append(
            f"<tr><th scope='row'>{day}</th><td>{promo_visits[day]}</td>"
            f"<td>{signups.get(day, 0)}</td><td>{downloads[day]}</td>"
            f"<td>{connected.get(day, 0)}</td><td>{paid.get(day, 0)}</td></tr>"
        )
    view_rows = [
        f"<tr><td>{r['day']}</td><td>{escape(r['path'])}</td>"
        f"<td>{escape(r['referer_host'] or '(direct)')}</td>"
        f"<td>{r['views']}</td><td>{r['bot_views']}</td></tr>"
        for r in views
    ]
    if not view_rows:
        view_rows.append("<tr><td colspan='5'>No views in this window.</td></tr>")

    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Daily metrics · brnrd</title>
  <link rel="stylesheet" href="/static/app.css">
</head>
<body>
<main class="metrics">
  <h1>Daily metrics</h1>
  <p>{since} to {today} (UTC). Paid subscriptions are counted on the day their first paid invoice was recorded.
    Script downloads are GETs of /install (curl and browsers; recognized bots excluded), which also counts
    people reading the script; computers connected counts accounts on the day their first computer connected,
    and is the install number.</p>
  <div class="metrics-table">
    <table aria-label="Daily visits and conversions">
      <thead><tr><th scope="col">Day</th><th scope="col">/hi visits</th><th scope="col">New signups</th>
        <th scope="col">Script downloads</th><th scope="col">Computers connected</th>
        <th scope="col">New paid subscriptions</th></tr></thead>
      <tbody>{''.join(daily_rows)}</tbody>
    </table>
  </div>
  <h2>Views by path and referrer</h2>
  <p>Views and /hi visits count origin requests and exclude recognized bots. Bot views appear separately.</p>
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

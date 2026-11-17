"""Operator read-outs, for the few accounts named in ``BRNRD_ADMIN_GITHUB_LOGINS``.

The server had no admin gate before this. The gate is the signed-in
session's account: its GitHub login must be on the list in settings
(comma-separated, case-insensitive). Unset or empty means nobody.

Everyone else (anonymous, signed in but unlisted, any visitor while the list
is empty) gets the same 404 an unknown route gets, so the surface is not
discoverable by probing it. The page that reads this (``/account/views``)
is linked from nowhere.

Only one read-out today: the page-view counters ``brnrd.page_views`` writes,
the last 30 days by day, path and referrer host. Read-only.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import account_id_from_session_cookie, get_db
from ..models import Account
from ..page_views import PageViewCount

router = APIRouter(prefix="/v1/admin", tags=["admin"], include_in_schema=False)

PAGE_VIEW_DAYS = 30


def _admin_or_404(request: Request, db: Session) -> Account:
    allowed = request.app.state.settings.admin_github_logins
    account_id = account_id_from_session_cookie(request, db) if allowed else None
    account = db.get(Account, account_id) if account_id else None
    if account is None or account.deleted_at is not None or (account.github_login or "").lower() not in allowed:
        raise HTTPException(status_code=404, detail="Not Found")
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
    P = PageViewCount
    stmt = select(P.day, P.path, P.referer_host, P.ua_family, P.views).where(P.day >= since)
    totals: dict[tuple[dt.date, str, str], list[int]] = defaultdict(lambda: [0, 0])
    for day, path, host, ua, n in db.execute(stmt):
        totals[(day, path, host or "")][1 if ua == "bot" else 0] += n
    # Newest day first; within a day, path, then the busiest referrer first.
    order = sorted(totals.items(), key=lambda kv: (-kv[0][0].toordinal(), kv[0][1], -kv[1][0], kv[0][2]))
    rows = [
        {"day": day.isoformat(), "path": path, "referer_host": host, "views": v, "bot_views": b}
        for (day, path, host), (v, b) in order
    ]
    return {"since": since.isoformat(), "until": today.isoformat(), "rows": rows}

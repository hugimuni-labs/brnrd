"""Weekly growth counts from records the server already keeps.

Visits and script downloads are the page-view counters (no row per visitor).
Signups, install finished, day-3 return and paid subscriptions are distinct
accounts. Team and test GitHub logins are left out of the account steps.

A campaign path is a visit tag only. Carrying it onto the account would need
a cookie or a stored address to survive the redirect and the login, and the
counter sets neither. ``tag_on_account`` is false for that reason.

Day 3 is 72 hours after the account's first computer connected
(``home_trial_started_at``, which is also the install-finished stamp). A
daemon sighting or a letter at or after that moment counts. The cell is
dated on the connection, so it rises when the person comes back. A wake
that never reaches this server is not in the number. Letters live on event
rows, which are deleted after 90 days; a daemon row's latest sighting stays.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import Settings
from .models import Account, BillingLedgerEntry, Daemon, Event, Repo
from .page_views import INSTALL_PATH, PageViewCount

DAILY_DAYS = 14
WEEKLY_WEEKS = 8
DAY3 = dt.timedelta(days=3)
_PAID_OP = "grant_subscriber_monthly"

NOTES = {
    "landing": (
        "Origin GETs of /, recognized bots left out. Requests, not distinct people. "
        "A campaign path redirects home, so those visitors are in this count as well."
    ),
    "campaigns": (
        "Origin GETs of each campaign path, recognized bots left out. The path is "
        "counted and the visitor is sent home. This is the only place the tag appears."
    ),
    "signups": "Distinct accounts created in the period, team and test logins left out.",
    "installs_started": "GETs of /install, recognized bots left out. Also counts people reading the script.",
    "installs_finished": (
        "Distinct accounts whose first computer connected in the period, team and test logins left out."
    ),
    "active_day3": (
        "Of those connections, accounts with a daemon sighting or a letter at least 72 hours later. "
        "Dated on the connection. A recent day stays 0 until then."
    ),
    "paid": (
        "Distinct accounts whose first paid invoice was recorded in the period, team and test logins left out."
    ),
    "excluded": (
        "GitHub logins on BRNRD_FUNNEL_EXCLUDE_GITHUB_LOGINS, the admin list, the member list "
        "and the first-subscriber exclude list. Visit counts have no login to compare."
    ),
}


def excluded_logins(settings: Settings) -> frozenset[str]:
    names = (
        settings.funnel_exclude_github_logins
        + settings.admin_github_logins
        + settings.home_gate_member_github_logins
        + settings.first_subscribers_exclude
    )
    return frozenset(part.strip().lower() for part in names if part and part.strip())


def week_label(day: dt.date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def week_mondays(today: dt.date, weeks: int = WEEKLY_WEEKS) -> list[dt.date]:
    """The Monday of each ISO week in the window, newest first."""

    this = today - dt.timedelta(days=today.weekday())
    return [this - dt.timedelta(weeks=i) for i in range(weeks)]


def _naive(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = dt.datetime.fromisoformat(value)
    if not isinstance(value, dt.datetime):
        return None
    if value.tzinfo is not None:
        return value.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return value


def _day(value: Any) -> dt.date | None:
    if isinstance(value, dt.datetime):
        naive = _naive(value)
        return None if naive is None else naive.date()
    if isinstance(value, dt.date):
        return value
    return None


def _returned(anchor: dt.datetime, *marks: Any) -> bool:
    start = _naive(anchor)
    if start is None:
        return False
    threshold = start + DAY3
    return any((seen := _naive(mark)) is not None and seen >= threshold for mark in marks)


def _blank(campaigns: tuple[str, ...]) -> dict[str, Any]:
    return {
        "landing": 0,
        "campaigns": {path: 0 for path in campaigns},
        "signups": 0,
        "installs_started": 0,
        "installs_finished": 0,
        "active_day3": 0,
        "paid": 0,
    }


def _row(period: str, bucket: dict[str, Any]) -> dict[str, Any]:
    return {"period": period, **bucket, "campaigns": dict(bucket["campaigns"])}


def build(db: Session, settings: Settings, *, today: dt.date | None = None) -> dict[str, Any]:
    today = today or dt.datetime.now(dt.timezone.utc).date()
    mondays = week_mondays(today)
    weekly_since = mondays[-1]
    daily_since = today - dt.timedelta(days=DAILY_DAYS - 1)
    campaigns = tuple(settings.campaign_paths)
    excluded = excluded_logins(settings)
    start = dt.datetime.combine(weekly_since, dt.time.min)
    end = dt.datetime.combine(today + dt.timedelta(days=1), dt.time.min)

    counts: dict[dt.date, dict[str, Any]] = {}
    cursor = weekly_since
    while cursor <= today:
        counts[cursor] = _blank(campaigns)
        cursor += dt.timedelta(days=1)

    view = PageViewCount
    for day, path, ua, n in db.execute(
        select(view.day, view.path, view.ua_family, view.views).where(view.day.between(weekly_since, today))
    ):
        if ua == "bot":
            continue
        slot = _day(day)
        bucket = counts.get(slot) if slot is not None else None
        if bucket is None:
            continue
        if path == "/":
            bucket["landing"] += n
        elif path == INSTALL_PATH:
            bucket["installs_started"] += n
        elif path in bucket["campaigns"]:
            bucket["campaigns"][path] += n

    def keep(login: str | None) -> bool:
        return (login or "").lower() not in excluded

    for created, login in db.execute(
        select(Account.created_at, Account.github_login).where(Account.created_at >= start, Account.created_at < end)
    ):
        slot = _day(created)
        if slot in counts and keep(login):
            counts[slot]["signups"] += 1

    ledger = BillingLedgerEntry
    for login, received in db.execute(
        select(Account.github_login, func.min(ledger.created_at))
        .join(Account, Account.id == ledger.account_id)
        .where(ledger.op == _PAID_OP)
        .group_by(Account.id, Account.github_login)
    ):
        slot = _day(received)
        if slot in counts and keep(login):
            counts[slot]["paid"] += 1

    connected = [
        (account_id, login, started)
        for account_id, login, started in db.execute(
            select(Account.id, Account.github_login, Account.home_trial_started_at).where(
                Account.home_trial_started_at.is_not(None),
                Account.home_trial_started_at >= start,
                Account.home_trial_started_at < end,
            )
        )
        if keep(login) and started is not None
    ]
    ids = [account_id for account_id, _, _ in connected]
    seen: dict[str, Any] = {}
    letters: dict[str, Any] = {}
    if ids:
        seen = {
            account_id: seen_at
            for account_id, seen_at in db.execute(
                select(Daemon.account_id, func.max(Daemon.last_seen_at))
                .where(Daemon.account_id.in_(ids))
                .group_by(Daemon.account_id)
            )
        }
        letters = {
            account_id: sent_at
            for account_id, sent_at in db.execute(
                select(Repo.account_id, func.max(Event.created_at))
                .join(Event, Event.repo_id == Repo.id)
                .where(Repo.account_id.in_(ids))
                .group_by(Repo.account_id)
            )
        }
    for account_id, _, started in connected:
        slot = _day(started)
        if slot not in counts:
            continue
        counts[slot]["installs_finished"] += 1
        if _returned(started, seen.get(account_id), letters.get(account_id)):
            counts[slot]["active_day3"] += 1

    days = [_row((today - dt.timedelta(days=offset)).isoformat(), counts[today - dt.timedelta(days=offset)])
            for offset in range(DAILY_DAYS)]
    weeks = []
    for monday in mondays:
        summed = _blank(campaigns)
        for offset in range(7):
            slot = monday + dt.timedelta(days=offset)
            bucket = counts.get(slot)
            if bucket is None:
                continue
            summed["landing"] += bucket["landing"]
            summed["signups"] += bucket["signups"]
            summed["installs_started"] += bucket["installs_started"]
            summed["installs_finished"] += bucket["installs_finished"]
            summed["active_day3"] += bucket["active_day3"]
            summed["paid"] += bucket["paid"]
            for path in campaigns:
                summed["campaigns"][path] += bucket["campaigns"][path]
        weeks.append(_row(week_label(monday), summed))

    return {
        "until": today.isoformat(),
        "daily_since": daily_since.isoformat(),
        "weekly_since": weekly_since.isoformat(),
        "campaigns": list(campaigns),
        "excluded_logins": len(excluded),
        "tag_on_account": False,
        "days": days,
        "weeks": weeks,
        "notes": NOTES,
    }

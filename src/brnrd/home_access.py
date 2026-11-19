"""The home's one entitlement decision, and the refusals that call it.

Every protected operation (hosted board reads and sends, a device's uploads, relay
delivery, new pairing credentials, the managed GitHub token) asks :func:`decide`.
Nothing else in the server reads subscription state to refuse home access.

Off by default. ``BRNRD_HOME_GATE_FROM`` (an ISO instant, UTC when no offset) turns it
on from that instant; unset, unparseable or still in the future, :func:`decide` admits
everyone without reading the database, so no response changes for anyone.

On, an account is admitted while it is:

- named: its GitHub login is in ``BRNRD_HOME_GATE_ADMIT_GITHUB_LOGINS`` (the household,
  admitted by hand with no Stripe subscription; empty by default);
- subscribed: ``Account.tier`` is subscribed, which only Stripe webhooks flip and which
  holds for any non-canceled subscription row on any price (old supporter prices, past
  due, and the founder's own included);
- not yet started: the clock starts on the first successful connect (``home_trial``),
  so an account that never connected has nothing to refuse;
- inside its window: 14 days from ``max(trial started, gate from)``. A clock that
  started before the switch-on gets a fresh window from the switch-on, never a cut-off
  on day one;
- unreadable: a billing read error admits (paying users never brick on an outage).

Account controls, deletion, billing, terms, sign-in and connecting a machine never call
this: they stay open to everyone.

A refusal names what to do. Browser routes answer 402. Device routes answer 429 with
``Retry-After``, the one refusal an installed client already holds a path on, after
holding the request open (``BRNRD_HOME_GATE_HOLD_S``) so a client too old to honour
``Retry-After`` cannot turn its retry loop into a flood.
"""
from __future__ import annotations

import asyncio
import functools
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from . import billing
from .auth import Principal, account_id_from_session_cookie, get_db, require_daemon
from .config import Settings
from . import home_trial
from .home_trial import TRIAL_LENGTH
from .models import Account
from .schemas import HomeTrialOut

logger = logging.getLogger(__name__)

REASON = "home_subscription_required"

#: Refusals being held open right now, in this process. Past ``home_gate_max_holds`` a
#: refusal answers at once: the hold paces old clients, it must never starve the server.
_holds_open = 0


@dataclass(frozen=True)
class HomeAccess:
    allowed: bool
    #: gate_off | named | subscribed | not_started | trial | degraded | no_account | home_subscription_required
    why: str
    ends_at: datetime | None = None


@functools.lru_cache(maxsize=8)
def _parse(raw: str) -> datetime | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("BRNRD_HOME_GATE_FROM=%r is not an ISO instant; the home gate stays off", raw)
        return None
    return at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at.astimezone(timezone.utc)


def gate_from(settings: Settings) -> datetime | None:
    """The switch-on instant, or ``None`` while the gate is off."""
    return _parse(settings.home_gate_from)


def enabled(settings: Settings, *, now: datetime | None = None) -> bool:
    at = gate_from(settings)
    return at is not None and (now or datetime.now(timezone.utc)) >= at


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def window_end(settings: Settings, account: Account) -> datetime | None:
    """When this account's home window ends; a pre-switch-on clock restarts at the switch-on."""
    started = account.home_trial_started_at
    if started is None:
        return None
    base = _utc(started)
    at = gate_from(settings)
    if at is not None and at > base:
        base = at
    return base + TRIAL_LENGTH


def trial_status(settings: Settings, account: Account, *, now: datetime | None = None) -> HomeTrialOut:
    """``GET /v1/accounts/home-trial``: the clock as recorded while off; the window the gate uses while on."""
    now = now or datetime.now(timezone.utc)
    if not enabled(settings, now=now):
        return home_trial.status(account, now=now)
    ends = window_end(settings, account)
    if ends is None:
        return HomeTrialOut(status="not_started", enforcement_enabled=True)
    return HomeTrialOut(status="active" if now < ends else "expired", started_at=_utc(account.home_trial_started_at),
                        ends_at=ends, enforcement_enabled=True)


def decide(db: Session, settings: Settings, account: Account | str | None, *, now: datetime | None = None) -> HomeAccess:
    """May this account use the home's hosted services right now?"""
    now = now or datetime.now(timezone.utc)
    if not enabled(settings, now=now):
        return HomeAccess(True, "gate_off")
    try:
        row = db.get(Account, account) if isinstance(account, str) else account
    except Exception:
        return HomeAccess(True, "degraded")
    if row is None:
        return HomeAccess(True, "no_account")  # authentication is not this function's job
    if (row.github_login or "").lower() in settings.home_gate_admit_github_logins:
        return HomeAccess(True, "named")
    ent = billing.entitlements(db, row)
    if ent.degraded:
        return HomeAccess(True, "degraded")
    if ent.headroom_lifted:
        return HomeAccess(True, "subscribed")
    ends = window_end(settings, row)
    if ends is None:
        return HomeAccess(True, "not_started")
    if now < ends:
        return HomeAccess(True, "trial", ends)
    return HomeAccess(False, REASON, ends)


def _detail(settings: Settings, access: HomeAccess) -> dict:
    account_url = settings.public_base_url.rstrip("/") + "/account"
    return {
        "reason": REASON,
        "message": (f"The home trial ended{' on ' + access.ends_at.date().isoformat() if access.ends_at else ''} "
                    f"and this account has no subscription. Subscribe at {account_url} to use it again; "
                    "your assistant keeps working on your computer."),
        "account_url": account_url,
    }


def refuse_session(db: Session, settings: Settings, account_id: str | None) -> None:
    """Browser routes: 402 naming /account. No account: the route answers its own 401."""
    if account_id is None:
        return
    access = decide(db, settings, account_id)
    if not access.allowed:
        raise HTTPException(status_code=402, detail=_detail(settings, access))


def session_gate(request: Request, db: Session = Depends(get_db)) -> None:
    """Route dependency for the hosted board's session routes."""
    if not enabled(request.app.state.settings):
        return
    refuse_session(db, request.app.state.settings, account_id_from_session_cookie(request, db))


def _decide_alone(request: Request, account_id: str) -> HomeAccess:
    # Its own short session: a request-scoped one would hold a pooled connection
    # through the inbox long-poll and the refusal's hold.
    with request.app.state.SessionLocal() as db:
        return decide(db, request.app.state.settings, account_id)


async def daemon_gate(request: Request, principal: Principal = Depends(require_daemon)) -> Principal:
    """``require_daemon`` behind the home decision, for a device's uploads and relay routes."""
    settings = request.app.state.settings
    if not enabled(settings):
        return principal
    access = await run_in_threadpool(_decide_alone, request, principal.account_id)
    if access.allowed:
        return principal
    hold = settings.home_gate_hold_s
    if request.url.path.endswith("/inbox"):
        # The long-poll's own timeout: a refused poller asks no faster than a served one.
        hold = max(hold, settings.inbox_long_poll_max_s)
    global _holds_open
    if hold > 0 and _holds_open < settings.home_gate_max_holds:
        _holds_open += 1  # one event loop: no lock needed between the check and the increment
        try:
            await asyncio.sleep(hold)
        finally:
            _holds_open -= 1
    raise HTTPException(status_code=429, detail=_detail(settings, access),
                        headers={"Retry-After": str(settings.home_gate_retry_after_s)})

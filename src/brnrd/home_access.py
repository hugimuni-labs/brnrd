"""The home's one entitlement decision, and the refusals that call it.

Every protected operation (hosted board reads and sends, a device's uploads, relay
delivery, new pairing credentials, the managed GitHub token) asks :func:`decide`.
Nothing else in the server reads subscription state to refuse home access.

Always on. An account is admitted while it is:

- member: its GitHub login is in ``BRNRD_MEMBER_GITHUB_LOGINS`` (members,
  admitted by hand with no Stripe subscription; empty by default). The previous
  name ``BRNRD_HOME_GATE_ADMIT_GITHUB_LOGINS`` is read when the new one is unset;
- subscribed: ``Account.tier`` is subscribed, which only Stripe webhooks flip and which
  holds for any non-canceled subscription row on any price (old supporter prices, past
  due, and the founder's own included);
- not yet started: the clock starts on the first successful connect (``home_trial``),
  so an account that never connected has nothing to refuse;
- inside its window: 14 days from its first successful connect;
- unreadable: a billing read error admits (paying users never brick on an outage).

Account controls, deletion, billing, terms and sign-in stay open to everyone.

A refusal names what to do. Browser routes answer 402. Device routes answer 429 with
``Retry-After``, the one refusal an installed client already holds a path on, after
holding the request open (``BRNRD_HOME_GATE_HOLD_S``) so a client too old to honour
``Retry-After`` cannot turn its retry loop into a flood.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from . import billing
from .auth import Principal, account_id_from_session_cookie, get_db, require_daemon
from .config import Settings
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
    #: member | subscribed | not_started | trial | degraded | no_account | home_subscription_required
    why: str
    ends_at: datetime | None = None


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def window_end(account: Account) -> datetime | None:
    """When this account's home window ends: 14 days after its first successful connect."""
    started = account.home_trial_started_at
    return None if started is None else _utc(started) + TRIAL_LENGTH


def is_member(settings: Settings, account: Account) -> bool:
    return (account.github_login or "").lower() in settings.home_gate_member_github_logins


def trial_status(settings: Settings, account: Account, *, now: datetime | None = None) -> HomeTrialOut:
    """``GET /v1/accounts/home-trial``: the window the gate uses."""
    now = now or datetime.now(timezone.utc)
    labels = {
        "member": is_member(settings, account),
        "subscribed": account.tier == Account.TIER_SUBSCRIBED,
    }
    ends = window_end(account)
    if ends is None:
        return HomeTrialOut(status="not_started", enforcement_enabled=True, **labels)
    return HomeTrialOut(status="active" if now < ends else "expired", started_at=_utc(account.home_trial_started_at),
                        ends_at=ends, enforcement_enabled=True, **labels)


def decide(db: Session, settings: Settings, account: Account | str | None, *, now: datetime | None = None) -> HomeAccess:
    """May this account use the home's hosted services right now?"""
    now = now or datetime.now(timezone.utc)
    try:
        row = db.get(Account, account) if isinstance(account, str) else account
    except Exception:
        return HomeAccess(True, "degraded")
    if row is None:
        return HomeAccess(True, "no_account")  # authentication is not this function's job
    if is_member(settings, row):
        return HomeAccess(True, "member")
    ent = billing.entitlements(db, row)
    if ent.degraded:
        return HomeAccess(True, "degraded")
    if ent.headroom_lifted:
        return HomeAccess(True, "subscribed")
    ends = window_end(row)
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
                    f"and this account has no subscription. Subscribe at {account_url} to reconnect, "
                    "use the hosted board and relay, and publish with the managed GitHub App. "
                    "Your assistant keeps working on your computer."),
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
    refuse_session(db, request.app.state.settings, account_id_from_session_cookie(request, db))


def _decide_alone(request: Request, account_id: str) -> HomeAccess:
    # Its own short session: a request-scoped one would hold a pooled connection
    # through the inbox long-poll and the refusal's hold.
    with request.app.state.SessionLocal() as db:
        return decide(db, request.app.state.settings, account_id)


async def daemon_gate(request: Request, principal: Principal = Depends(require_daemon)) -> Principal:
    """``require_daemon`` behind the home decision, for a device's uploads and relay routes."""
    settings = request.app.state.settings
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

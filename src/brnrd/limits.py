"""Free-tier headroom limits + abuse ceilings (#501 repo cap half; account
decision ledger 2026-07-21).

Two distinct kinds of bound, deliberately kept apart:

- **Headroom limits** bind only the free tier and lift entirely for any
  member account or any account with lifted billing headroom. Membership
  uses the same allowlist as home access.
- **Abuse ceilings** bind every tier, sit far above real use, and exist as
  protection, not product.

Failure posture when subscription state is unreadable (billing outage):
headroom fails *open* (a billing outage must not brick paying users'
ingress), abuse ceilings fail *closed* (they never depended on billing
state to begin with — the counts come straight from this database).

All numeric bounds live in :class:`brnrd.config.Settings` (the
``limit_*`` block); nothing here carries a scattered numeral. Counters
read real tables only: connected repos = ``repos`` rows, event rates =
``events`` rows by ``created_at`` (kept accurate well past these windows
by the #502 GC's 90-day row TTL).

The routes an install calls (sends and reports) have their own all-tier
ceiling, :class:`Ceiling`, counted in this process rather than in the
database: see its docstring for why.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import billing, home_access, inbox
from .config import Settings
from .models import Account, Event, Repo

# One line, no marketing (ledger 2026-07-21): name the limit, name the path.
_UPGRADE_HINT = "subscribing lifts free-tier limits (dashboard billing page)."


@dataclass(frozen=True)
class LimitDecision:
    """Outcome of one limit check.

    ``reason`` is the machine-readable handle (stable, snake_case);
    ``message`` is the one-line human text naming the limit (and, for
    free-tier headroom rejections, the upgrade path). ``status`` is the
    HTTP status an HTTP ingress should map the rejection to — platform
    webhooks ignore it and do a polite logged drop instead.
    """

    allowed: bool
    reason: str = ""
    message: str = ""
    status: int = 429
    #: Seconds until the refused window ends, sent as ``Retry-After``; ``None`` when there is no window.
    retry_after: int | None = None


_ALLOW = LimitDecision(True)


def _naive_utcnow() -> datetime:
    # Model DateTime columns hold naive UTC (models._utcnow via SQLite).
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _entitlements(db: Session, account: Account | str | None) -> billing.Entitlements:
    """Read the enablement seam; unreadable ⇒ degraded (headroom open)."""
    try:
        return billing.entitlements(db, account)
    except Exception:
        return billing.DEGRADED_ENTITLEMENTS


def _account_event_count(db: Session, account_id: str, *, since: datetime) -> int:
    return int(
        db.execute(
            select(func.count())
            .select_from(Event)
            .join(Repo, Event.repo_id == Repo.id)
            .where(Repo.account_id == account_id, Event.created_at >= since)
        ).scalar_one()
    )


def _free_window_full(
    db: Session,
    account_id: str,
    *,
    since: datetime,
    limit: int,
    telegram_album: tuple[str, str] | None,
) -> bool:
    # Responded rows retain their routing metadata, so a relay drain does
    # not spend a fresh free slot for each remaining item of an album.
    rows = db.execute(
        select(Event.source, Event.reply_to)
        .join(Repo, Event.repo_id == Repo.id)
        .where(Repo.account_id == account_id, Event.created_at >= since)
    )
    albums: set[tuple[str, str]] = set()
    singles = 0
    for source, reply_to in rows:
        meta = inbox.decode_reply_to(reply_to) if source == "telegram" else {}
        if meta.get("media_group_id") and meta.get("chat_id") is not None:
            albums.add((str(meta["chat_id"]), str(meta["media_group_id"])))
        else:
            singles += 1
    return telegram_album not in albums and singles + len(albums) >= limit


def check_event_admission(
    db: Session,
    settings: Settings,
    account: Account | None,
    *,
    body: str = "",
    attachment_count: int = 0,
    telegram_album: tuple[str, str] | None = None,
) -> LimitDecision:
    """May this account enqueue one more inbox event right now?

    Order matters: abuse ceilings (payload shape, then rate) are checked
    before any billing read, so they bind identically for every tier and
    never depend on subscription state being readable.
    """
    if account is None:
        # FK integrity makes this unreachable from live ingress; if a
        # dangling row ever gets here, admitting one event beats a 500.
        return _ALLOW
    if len((body or "").encode("utf-8")) > settings.limit_max_event_body_bytes:
        kb = settings.limit_max_event_body_bytes // 1000
        return LimitDecision(
            False,
            "event_body_too_large",
            f"Message exceeds the {kb} kB per-event limit; send something shorter.",
            status=413,
        )
    if attachment_count > settings.limit_max_event_attachments:
        return LimitDecision(
            False,
            "too_many_attachments",
            f"Too many attachments (limit {settings.limit_max_event_attachments} per message).",
            status=413,
        )

    now = _naive_utcnow()
    minute_start = now - timedelta(minutes=1)
    minute_count = _account_event_count(db, account.id, since=minute_start)
    if minute_count >= settings.limit_abuse_events_per_minute:
        return LimitDecision(
            False,
            "abuse_event_rate",
            f"Account event-rate ceiling reached ({settings.limit_abuse_events_per_minute}/min); wait a moment and retry.",
        )
    day_start = now - timedelta(days=1)
    day_count = _account_event_count(db, account.id, since=day_start)
    if day_count >= settings.limit_abuse_events_per_day:
        return LimitDecision(
            False,
            "abuse_daily_events",
            f"Account daily event ceiling reached ({settings.limit_abuse_events_per_day}/day); retry tomorrow.",
        )

    if home_access.is_member(settings, account) or _entitlements(db, account).headroom_lifted:
        return _ALLOW
    burst_limit = settings.limit_free_events_per_minute
    if minute_count >= burst_limit and _free_window_full(
        db, account.id, since=minute_start, limit=burst_limit, telegram_album=telegram_album,
    ):
        return LimitDecision(
            False,
            "free_event_burst",
            f"Free-tier burst limit reached ({burst_limit} events/min); {_UPGRADE_HINT}",
        )
    daily_limit = settings.limit_free_events_per_day
    if day_count >= daily_limit and _free_window_full(
        db, account.id, since=day_start, limit=daily_limit, telegram_album=telegram_album,
    ):
        return LimitDecision(
            False,
            "free_daily_events",
            f"Free-tier daily event limit reached ({daily_limit}/day); {_UPGRADE_HINT}",
        )
    return _ALLOW


def check_repo_connect(db: Session, settings: Settings, account: Account) -> LimitDecision:
    """May this account connect one more (new) repo?

    Callers invoke this only when about to create a *new* ``repos`` row —
    reconnecting an already-connected repo stays idempotent and uncapped.
    """
    count = int(
        db.execute(
            select(func.count()).select_from(Repo).where(Repo.account_id == account.id)
        ).scalar_one()
    )
    if count >= settings.limit_abuse_repos:
        return LimitDecision(
            False,
            "abuse_repo_ceiling",
            f"Connected-repo ceiling reached ({settings.limit_abuse_repos} repos).",
            status=403,
        )
    if (
        count >= settings.limit_free_repos
        and not home_access.is_member(settings, account)
        and not _entitlements(db, account).headroom_lifted
    ):
        return LimitDecision(
            False,
            "free_repo_limit",
            f"Free-tier repo limit reached ({settings.limit_free_repos} connected repos); {_UPGRADE_HINT}",
            status=403,
        )
    return _ALLOW


def raise_if_denied(decision: LimitDecision) -> None:
    """HTTP-ingress mapping: 4xx with a structured, machine-readable detail."""
    if decision.allowed:
        return
    raise HTTPException(
        status_code=decision.status,
        detail={"reason": decision.reason, "message": decision.message},
        headers={"Retry-After": str(decision.retry_after)} if decision.retry_after else None,
    )


# --- the ceiling on the routes an install calls --------------------------------

#: Lane -> the ``(settings attribute, window seconds)`` pairs that bound it.
#: ``sends`` reach a person's chat (or hold memory, ``/pack``); ``reports`` are
#: the publish loops' PUTs and the relay's presence and typing posts.
CEILING_LANES: dict[str, tuple[tuple[str, int], ...]] = {
    "sends": (
        ("limit_abuse_sends_per_minute", 60),
        ("limit_abuse_sends_per_hour", 3600),
        ("limit_abuse_sends_per_day", 86400),
    ),
    "reports": (("limit_abuse_reports_per_minute", 60),),
}

_SPAN_WORD = {60: "min", 3600: "hour", 86400: "day"}


class Ceiling:
    """Fixed-window call counts per account and lane, held in this process.

    Why not the database: the ceiling exists to protect the server, so it must
    cost almost nothing on the path it guards. ``reports`` run near 200 calls a
    minute per install at worst (the 3 s publish loop's eight lanes, the
    board's parts push, the loom snapshot); a COUNT over a table per call, or a
    counter row written per call, would add that load to the database it is
    meant to spare, and a refused call would cost as much as an admitted one.
    In-process is exact while brnrd runs as one uvicorn process (the
    Dockerfile's CMD, and ``min-scale 1`` in deploy/scaleway.md), which the
    product already depends on: the inbox long-poll and the pack relay are in
    this process's memory too. Under N processes each counts alone, so the
    ceiling becomes N times the number: still a ceiling, never a false refusal.
    A restart forgets the counts, which only ever admits more.

    A refused call is not counted, so a caller that backs off is admitted again
    as soon as the window turns.
    """

    def __init__(self, clock=time.monotonic) -> None:
        self.clock = clock
        self._lock = threading.Lock()
        self._windows: dict[tuple[str, str, int], tuple[int, int]] = {}
        self._last_refused: dict[tuple[str, ...], float] = {}

    def admit(self, settings: Settings, account_id: str, lane: str) -> LimitDecision:
        bounds = [(getattr(settings, attr), span) for attr, span in CEILING_LANES[lane]]
        with self._lock:
            now = self.clock()
            if len(self._windows) > 50_000:
                self._prune(now)
            for limit, span in bounds:
                index = int(now // span)
                at, count = self._windows.get((account_id, lane, span), (index, 0))
                if at == index and count >= limit:
                    word = _SPAN_WORD.get(span, f"{span}s")
                    return LimitDecision(
                        False,
                        f"abuse_{lane}_rate",
                        f"Account {lane} ceiling reached ({limit}/{word}); "
                        "this install is calling far faster than real use. Wait and retry.",
                        retry_after=max(1, math.ceil((index + 1) * span - now)),
                    )
            for limit, span in bounds:
                index = int(now // span)
                key = (account_id, lane, span)
                at, count = self._windows.get(key, (index, 0))
                self._windows[key] = (index, count + 1 if at == index else 1)
        return _ALLOW

    def first_in_episode(self, settings: Settings, *key: str) -> bool:
        """True for the first refusal of an episode: none under ``key`` in the last quiet span.

        Every refusal extends the episode, so a flood that never stops is told about once.
        """
        quiet = settings.limit_abuse_episode_quiet_seconds
        with self._lock:
            now = self.clock()
            last = self._last_refused.get(key)
            self._last_refused[key] = now
            if len(self._last_refused) > 50_000:
                self._last_refused = {k: t for k, t in self._last_refused.items() if now - t < quiet}
        return last is None or now - last >= quiet

    def _prune(self, now: float) -> None:
        self._windows = {k: v for k, v in self._windows.items() if v[0] == int(now // k[2])}


#: The one instance: per process, like the inbox long-poll and the typing limiter.
CEILING = Ceiling()


def is_rate_refusal(decision: LimitDecision) -> bool:
    """A refusal that repeats under a flood (a rate window), not one about this message's shape."""
    return not decision.allowed and decision.status == 429

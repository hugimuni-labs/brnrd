"""Server-side page-view counters — the funnel without a tracker.

Decision (maintainer, 2026-09-20): brnrd.dev keeps its no-tracking posture, so
the funnel (visits by path → pricing → install page → first task) is counted
by the one party that already sees every request: this process.

What is stored, and what is not — the whole privacy argument is this table:

    day · path · referer_host · ua_family · status  →  views

It is a *counter*, not a log. There is no row per visit, so nothing at rest can
be replayed into a visitor's trail. No IP address (never read), no query string
(dropped before the path is normalised), no cookie (never set or read), no
user id, no full user-agent (reduced to a coarse family), no full referer (only
its host). Nothing leaves this database, so no sub-processor is added: the
counter rides the managed PostgreSQL the app already uses. A container
filesystem is not state on this deployment (``deploy/runtime-contract.md``), so
a daily file would be lost at the next rollout; a counter row survives it.

Only a closed allowlist of public routes is counted (``/``, ``/pricing``,
``/learn*``, ``/log*``), only ``GET``, and only a 200/304 — a scanner walking
``/learn/<junk>`` cannot mint rows. Signed-in surfaces are never touched.

Limit, stated: a CDN in front of the origin can answer a cacheable page
without this process ever seeing the request, so counts are a floor on
origin-served views, not a census.
"""

from __future__ import annotations

import datetime as dt
import re
from urllib.parse import urlsplit

import anyio
from sqlalchemy import Column, Date, Integer, String, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .db import Base

_EXACT = frozenset({"/", "/pricing", "/learn", "/log"})
_PREFIXES = ("/learn/", "/log/")
_MAX_PATH = 64
_MAX_SEGMENTS = 3
_MAX_HOST = 100
_COUNTED_STATUS = frozenset({200, 304})

# Order matters: bots first, then the browsers whose UA strings embed each other
# (Edge and Opera say "Chrome"; Chrome says "Safari").
_UA_FAMILIES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("bot", re.compile(r"bot|crawl|spider|slurp|preview|facebookexternalhit|headless|python-requests|httpx", re.I)),
    ("curl", re.compile(r"^(curl|wget)/", re.I)),
    ("edge", re.compile(r"edg(e|a|ios)?/", re.I)),
    ("firefox", re.compile(r"firefox|fxios", re.I)),
    ("chrome", re.compile(r"chrome|chromium|crios", re.I)),
    ("safari", re.compile(r"safari", re.I)),
)


class PageViewCount(Base):
    """One counter per (day, path, referer host, UA family, status)."""

    __tablename__ = "page_view_counts"

    day = Column(Date, primary_key=True)
    path = Column(String(_MAX_PATH), primary_key=True)
    referer_host = Column(String(_MAX_HOST), primary_key=True, default="")
    ua_family = Column(String(16), primary_key=True)
    status = Column(Integer, primary_key=True)
    views = Column(Integer, nullable=False, default=0)


def counted_path(raw_path: str) -> str | None:
    """The normalised path if it is on the allowlist, else ``None``."""

    path = raw_path.split("?", 1)[0].split("#", 1)[0]
    if len(path) > 1:
        path = path.rstrip("/")
    if len(path) > _MAX_PATH or not path.startswith("/"):
        return None
    if path in _EXACT:
        return path
    if path.startswith(_PREFIXES) and path.count("/") <= _MAX_SEGMENTS:
        if re.fullmatch(r"/[A-Za-z0-9._~/-]+", path):
            return path
    return None


def referer_host(value: str | None) -> str:
    """Host of the referer only — never its path or query. ``""`` = direct."""

    if not value:
        return ""
    try:
        host = urlsplit(value).hostname or ""
    except ValueError:
        return ""
    return host.lower()[:_MAX_HOST]


def ua_family(value: str | None) -> str:
    if not value:
        return "none"
    for name, pattern in _UA_FAMILIES:
        if pattern.search(value):
            return name
    return "other"


def record_view(
    factory: sessionmaker, *, day: dt.date, path: str, referer: str, ua: str, status: int,
) -> None:
    """Add one to the counter, creating the row on first sight of its key."""

    key = dict(day=day, path=path, referer_host=referer, ua_family=ua, status=status)
    for _ in range(2):
        with factory() as db:
            hit = db.execute(
                update(PageViewCount).filter_by(**key).values(views=PageViewCount.views + 1)
            ).rowcount
            if hit:
                db.commit()
                return
            try:
                db.add(PageViewCount(views=1, **key))
                db.commit()
                return
            except IntegrityError:  # a concurrent first view won the insert; retry as an update
                db.rollback()


class PageViewMiddleware:
    """Count public-route GETs. Raw ASGI, like HSTS: the body is never touched.

    The write happens after the response has been sent and swallows every
    error — analytics must never be able to fail a page.
    """

    def __init__(self, app: ASGIApp, *, factory: sessionmaker) -> None:
        self.app = app
        self.factory = factory

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "GET":
            await self.app(scope, receive, send)
            return
        path = counted_path(scope.get("path") or "")
        if path is None:
            await self.app(scope, receive, send)
            return

        status = 0

        async def capture(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        await self.app(scope, receive, capture)
        if status not in _COUNTED_STATUS:
            return
        headers = {k: v for k, v in scope.get("headers") or ()}
        try:
            await anyio.to_thread.run_sync(
                lambda: record_view(
                    self.factory,
                    day=dt.datetime.now(dt.timezone.utc).date(),
                    path=path,
                    referer=referer_host(headers.get(b"referer", b"").decode("latin-1")),
                    ua=ua_family(headers.get(b"user-agent", b"").decode("latin-1")),
                    status=status,
                )
            )
        except Exception:  # noqa: BLE001 — a counter failing is never the visitor's problem
            pass

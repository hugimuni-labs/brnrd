"""Database engine + session plumbing.

SQLite for the prototype, swappable to Postgres via
``BRNRD_DATABASE_URL`` without code changes — the ORM layer is
the same. No Alembic yet; ``create_all`` runs at app startup
(migrations land with the Postgres cutover).
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    """Declarative base for all brnrd ORM models."""


def make_engine(url: str) -> Engine:
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        # FastAPI runs sync endpoints in a threadpool, so the SQLite
        # connection may be touched from a different thread than the
        # one that opened it.
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url:
            # A single shared in-memory connection, so every session
            # sees the same database (otherwise each connection gets a
            # fresh empty one).
            #
            # The cost, and it only ever bites tests: *one* connection means
            # one SQLite transaction for every session alive at once. Two
            # concurrent sessions are then not isolated from each other at
            # all — one session's `close()` ROLLBACKs another's uncommitted
            # INSERT, and the writer sees its own just-committed row vanish
            # (`Could not refresh instance '<Event ...>'`, 2026-08-01 CI, the
            # inbox long-poll wake test). No pooled backend behaves this way:
            # Postgres and file-backed SQLite both give each session its own
            # connection. So `:memory:` is only honest for a test that issues
            # one request at a time; anything modelling concurrent callers
            # must use a file (see `tests/test_brnrd_inbox.py::env`).
            kwargs["poolclass"] = StaticPool
    return create_engine(url, **kwargs)


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False, future=True
    )

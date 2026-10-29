"""Startup migrations against a live Postgres, while requests hold `tokens`.

CI has no Postgres, so this runs only with BRNRD_TEST_POSTGRES_URL set (a
throwaway database: the test drops its public schema), e.g. from
`pip install pgserver`. SQLite cannot show it: it has no table locks to wait on.
"""

from __future__ import annotations

import os
import threading

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("psycopg")

from sqlalchemy import create_engine, select, text  # noqa: E402

from brnrd.db import Base  # noqa: E402
from brnrd.migrations import run_startup_migrations  # noqa: E402
from brnrd.models import Token  # noqa: E402

URL = os.environ.get("BRNRD_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not URL, reason="BRNRD_TEST_POSTGRES_URL not set")


def test_a_restart_migrates_while_a_long_poll_holds_tokens():
    # brnrd#2523: an `ALTER TABLE tokens` at startup queued behind every
    # long-poll's auth session, holding the locks those long-polls next
    # needed, and every token lookup hung 30 s.
    engine = create_engine(URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    Base.metadata.create_all(engine)
    run_startup_migrations(engine)
    with engine.connect() as auth:
        auth.execute(select(Token.id).limit(1))  # a request's auth session, mid long-poll
        done = threading.Event()

        def restart():
            Base.metadata.create_all(engine)
            run_startup_migrations(engine)
            done.set()

        threading.Thread(target=restart, daemon=True).start()
        assert done.wait(10), "startup migrations wait on a session holding tokens"
        with engine.connect() as poll:
            poll.execute(text("SET statement_timeout = '2s'"))
            poll.execute(text("SELECT count(*) FROM events"))
    engine.dispose()

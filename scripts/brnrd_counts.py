#!/usr/bin/env python3
"""How many users brnrd has — a read-only, aggregates-only census.

Filed for the maintainer's Web Summit Showcase application (deadline 18 Sep
2026): the only reader of this number is him, so a script beats a new
endpoint — no auth, no public surface, no privacy line to own for a number
nobody else consumes.

Every number below is a ``COUNT(*)``. Nothing identifying (no email, login,
github id, account id, repo name) is ever printed or returned by this
module — ``tests/test_brnrd_counts.py`` asserts that directly, not just this
docstring.

## What each row means

- **accounts** — live ``accounts`` rows (``deleted_at IS NULL``). An Art-17
  erased account is a tombstone, not a user, and is excluded. This barely
  changes anything in practice: ``account_deletion.delete_account`` already
  deletes that account's own repos/daemons/subscription rows, so an erased
  account would not have contributed to any of the other rows anyway.
- **accounts_with_repo** — the subset of the above with >=1 row in ``repos``.
- **accounts_daemon_7d** / **accounts_daemon_30d** — the subset with >=1
  ``daemons`` row whose ``last_seen_at`` (the only per-daemon liveness
  timestamp the model carries, refreshed on every quota/live-runs publish
  tick — see ``models.Daemon.last_seen_at``) falls inside the window. An
  account running several daemons counts once.
- **paying_accounts** — the subset with a ``subscriptions`` row whose
  ``status`` is ``Subscription.STATUS_ACTIVE`` — the billing model's own
  definition of "currently paying" (``past_due`` and ``canceled`` do not
  count; see ``kb/design-billing.md``).
- **repos** — every ``repos`` row. Not filtered by account liveness on top
  of the above, because it does not need to be: a repo's account is deleted
  alongside its repos (same function), so a live count and a total count
  are already the same number.

## Run it

Local dev (the ``sqlite:///./brnrd.db`` default, or whatever
``BRNRD_DATABASE_URL`` names):

    python scripts/brnrd_counts.py

Against the hosted database — Scaleway managed Postgres, per
``plan-upsun-to-scaleway-cutover.md``; brnrd left Upsun on 2026-07-30, so an
Upsun app-shell command is no longer the right answer here:

    PGPASSFILE=~/.pgpass python scripts/brnrd_counts.py \\
        --url "postgresql+psycopg://<user>@<host>:<port>/rdb"

The ``postgres`` extra (``pip install -e .[postgres]``) provides the
``psycopg`` driver; ``~/.pgpass`` holds the read credential — the same
recipe ``scripts/db_row_counts.py``'s own header documents. ``--json``
prints the same numbers as one JSON object instead of a table.

Sample output (from the sqlite test fixture)::

    accounts             6   live accounts (deleted_at IS NULL) ...
    accounts_with_repo   1   live accounts with >=1 connected repo
    accounts_daemon_7d   1   live accounts with a daemon last_seen_at in the last 7 days
    accounts_daemon_30d  2   live accounts with a daemon last_seen_at in the last 30 days
    paying_accounts      1   live accounts with an active subscription (status == 'active')
    repos                1   total repos rows (already live-only, see above)
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class Row:
    key: str
    value: int
    definition: str


def compute_counts(session, *, now: datetime | None = None) -> list[Row]:
    """The whole census, as a list of ``Row``. One ``SELECT`` per row.

    Read-only by construction: every query below is a ``func.count()``
    aggregate, nothing here ever builds an ``insert``/``update``/``delete``
    statement, and the caller is expected to never call ``session.commit()``
    (see ``open_session`` — the connection itself is opened read-only against
    Postgres, belt-and-suspenders over the SQL shape alone).
    """
    from sqlalchemy import func, select

    from brnrd.models import Account, Daemon, Repo, Subscription

    now = now or datetime.now(timezone.utc)
    live_account = Account.deleted_at.is_(None)

    accounts_total = session.execute(
        select(func.count()).select_from(Account).where(live_account)
    ).scalar_one()

    accounts_with_repo = session.execute(
        select(func.count(func.distinct(Repo.account_id)))
        .select_from(Repo)
        .join(Account, Account.id == Repo.account_id)
        .where(live_account)
    ).scalar_one()

    def _daemon_seen_since(delta: timedelta) -> int:
        cutoff = now - delta
        return session.execute(
            select(func.count(func.distinct(Daemon.account_id)))
            .select_from(Daemon)
            .join(Account, Account.id == Daemon.account_id)
            .where(live_account, Daemon.last_seen_at >= cutoff)
        ).scalar_one()

    accounts_daemon_7d = _daemon_seen_since(timedelta(days=7))
    accounts_daemon_30d = _daemon_seen_since(timedelta(days=30))

    paying_accounts = session.execute(
        select(func.count(func.distinct(Subscription.account_id)))
        .select_from(Subscription)
        .join(Account, Account.id == Subscription.account_id)
        .where(live_account, Subscription.status == Subscription.STATUS_ACTIVE)
    ).scalar_one()

    repos_total = session.execute(
        select(func.count()).select_from(Repo)
    ).scalar_one()

    return [
        Row("accounts", accounts_total,
            "live accounts (deleted_at IS NULL) — an Art-17 tombstone is not a user"),
        Row("accounts_with_repo", accounts_with_repo,
            "live accounts with >=1 connected repo"),
        Row("accounts_daemon_7d", accounts_daemon_7d,
            "live accounts with a daemon last_seen_at in the last 7 days"),
        Row("accounts_daemon_30d", accounts_daemon_30d,
            "live accounts with a daemon last_seen_at in the last 30 days"),
        Row("paying_accounts", paying_accounts,
            "live accounts with an active subscription (Subscription.status == 'active')"),
        Row("repos", repos_total,
            "total repos rows (an account's repos are deleted alongside it, so this is already live-only)"),
    ]


def render_table(rows: list[Row]) -> str:
    width = max(len(r.key) for r in rows)
    return "\n".join(f"{r.key.ljust(width)}  {r.value:>8}   {r.definition}" for r in rows)


def render_json(rows: list[Row]) -> str:
    return json.dumps({r.key: r.value for r in rows}, indent=2)


def open_session(url: str):
    """A session bound to a read-only connection where the driver allows it.

    SQLite has no server-side read-only transaction mode reachable through
    this driver, so sqlite URLs get a plain session — the caller (``main``)
    still never commits. Postgres does support it
    (``postgresql_readonly`` execution option, psycopg2/psycopg dialects),
    so a Postgres connection is marked read-only before a single query runs:
    a mistake in this script cannot write, the database refuses it.
    """
    from brnrd.db import make_engine, make_session_factory

    engine = make_engine(url)
    connection = engine.connect()
    if connection.dialect.name == "postgresql":
        connection = connection.execution_options(postgresql_readonly=True)
    SessionLocal = make_session_factory(engine)
    return SessionLocal(bind=connection)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--url", default=None,
        help="SQLAlchemy database URL (default: BRNRD_DATABASE_URL, else local sqlite)",
    )
    parser.add_argument("--json", action="store_true", help="print as JSON instead of a table")
    args = parser.parse_args(argv)

    from brnrd.config import Settings

    url = args.url or Settings().database_url
    session = open_session(url)
    try:
        rows = compute_counts(session)
    finally:
        session.close()  # no commit ever happens on this session

    print(render_json(rows) if args.json else render_table(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

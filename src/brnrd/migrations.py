"""Small idempotent database migrations for the brnrd prototype.

The service still relies on SQLAlchemy ``create_all`` instead of a full Alembic
migration stack. ``create_all`` creates missing tables, but it does not evolve
existing tables. These startup migrations cover the narrow production schema
skew created while moving from the old password/account table to the current
GitHub-OAuth account model.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine


_ACCOUNT_REQUIRED_COLUMNS = {"id", "github_id", "github_login", "created_at"}


def run_startup_migrations(engine: Engine) -> None:
    """Apply small, safe, idempotent schema fixes.

    This is intentionally conservative. It only handles PostgreSQL because the
    production deployment uses Postgres and SQLite test/dev databases are fresh
    ``create_all`` databases. It also does not delete or rewrite legacy account
    rows; if old rows without GitHub identity exist, the GitHub columns remain
    nullable at the database level until a proper migration can decide how to
    backfill or retire those rows.
    """
    if engine.dialect.name != "postgresql":
        return

    with engine.begin() as conn:
        if not _table_exists(conn, "accounts"):
            return
        _migrate_accounts(conn)


def _table_exists(conn: Connection, table_name: str) -> bool:
    return bool(
        conn.execute(
            text(
                """
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = :table_name
                """
            ),
            {"table_name": table_name},
        ).scalar_one_or_none()
    )


def _column_exists(conn: Connection, table_name: str, column_name: str) -> bool:
    return bool(
        conn.execute(
            text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = :table_name
                  AND column_name = :column_name
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        ).scalar_one_or_none()
    )


def _migrate_accounts(conn: Connection) -> None:
    # Columns expected by the current GitHub-OAuth-only Account model.
    conn.execute(text("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS github_id VARCHAR(32)"))
    conn.execute(
        text("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS github_login VARCHAR(255)")
    )
    conn.execute(text("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS email VARCHAR(320)"))
    conn.execute(
        text(
            "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        )
    )

    # Email is optional in the GitHub identity model. ``password_hash`` is a
    # legacy password-login column that the current ORM no longer writes.
    conn.execute(text("ALTER TABLE accounts ALTER COLUMN email DROP NOT NULL"))
    if _column_exists(conn, "accounts", "password_hash"):
        conn.execute(text("ALTER TABLE accounts ALTER COLUMN password_hash DROP NOT NULL"))

    conn.execute(
        text("CREATE UNIQUE INDEX IF NOT EXISTS ix_accounts_github_id ON accounts (github_id)")
    )
    conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_accounts_github_login ON accounts (github_login)")
    )
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_accounts_email ON accounts (email)"))

    _tighten_required_account_columns(conn)


def _tighten_required_account_columns(conn: Connection) -> None:
    """Set NOT NULL where doing so is safe for existing production rows."""
    nullable_required_columns = conn.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'accounts'
              AND is_nullable = 'YES'
            """
        )
    ).scalars()

    for column in nullable_required_columns:
        if column not in _ACCOUNT_REQUIRED_COLUMNS:
            continue
        null_count = conn.execute(
            text(f'SELECT count(*) FROM accounts WHERE "{column}" IS NULL')
        ).scalar_one()
        if null_count == 0:
            conn.execute(text(f'ALTER TABLE accounts ALTER COLUMN "{column}" SET NOT NULL'))

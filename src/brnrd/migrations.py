"""Small idempotent database migrations for the brnrd prototype.

The service still relies on SQLAlchemy ``create_all`` instead of a full Alembic
migration stack. ``create_all`` creates missing tables, but it does not evolve
existing tables. These startup migrations cover the narrow production schema
skew created while moving fast before launch.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine


_ACCOUNT_REQUIRED_COLUMNS = {"id", "github_id", "github_login", "created_at"}


def run_startup_migrations(engine: Engine) -> None:
    """Apply small, safe, idempotent schema fixes."""
    if engine.dialect.name != "postgresql":
        return

    with engine.begin() as conn:
        if _table_exists(conn, "accounts"):
            _migrate_accounts(conn)
        if _table_exists(conn, "github_installed_repos"):
            _migrate_github_installed_repos(conn)
        if _table_exists(conn, "daemons"):
            _migrate_daemons(conn)
        if _table_exists(conn, "runner_wake_requests"):
            _migrate_runner_wake_requests(conn)
        if _table_exists(conn, "channel_routes"):
            _migrate_channel_routes(conn)
        if _table_exists(conn, "events"):
            _migrate_events(conn)


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
    conn.execute(text("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS github_id VARCHAR(32)"))
    conn.execute(text("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS github_login VARCHAR(255)"))
    conn.execute(text("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS email VARCHAR(320)"))
    conn.execute(text("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS hosted_terms_accepted_at TIMESTAMP"))
    conn.execute(text("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS hosted_terms_version VARCHAR(32) DEFAULT ''"))
    conn.execute(
        text(
            "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        )
    )

    conn.execute(text("ALTER TABLE accounts ALTER COLUMN email DROP NOT NULL"))
    if _column_exists(conn, "accounts", "password_hash"):
        conn.execute(text("ALTER TABLE accounts ALTER COLUMN password_hash DROP NOT NULL"))

    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_accounts_github_id ON accounts (github_id)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_accounts_github_login ON accounts (github_login)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_accounts_email ON accounts (email)"))

    # Discovered corpus mirror (authored surface + home knowledge + replies).
    # The layered files carry ``layer``/``truncated`` inside this JSON blob, so
    # the corpus join needed no DDL — pre-corpus rows self-heal on the next
    # full-replace publish (the daemon republishes once on boot).
    conn.execute(text("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS surface_json TEXT DEFAULT '[]'"))
    conn.execute(text("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS surface_updated_at TIMESTAMP"))

    # Billing (#53) — tier + Stripe customer link; new billing tables come
    # from create_all.
    conn.execute(text("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS tier VARCHAR(32) DEFAULT 'free'"))
    conn.execute(text("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS stripe_customer_id VARCHAR(64)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_accounts_stripe_customer_id ON accounts (stripe_customer_id)"))

    _tighten_required_account_columns(conn)


def _migrate_github_installed_repos(conn: Connection) -> None:
    conn.execute(text("ALTER TABLE github_installed_repos ADD COLUMN IF NOT EXISTS github_pushed_at TIMESTAMP"))
    conn.execute(text("ALTER TABLE github_installed_repos ADD COLUMN IF NOT EXISTS github_updated_at TIMESTAMP"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_github_installed_repos_pushed_at ON github_installed_repos (github_pushed_at)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_github_installed_repos_updated_at ON github_installed_repos (github_updated_at)"))


def _migrate_daemons(conn: Connection) -> None:
    # Account-scoped daemon identity. Existing rows inherit the owning account
    # from their compatibility/default repo; repo_id stops being identifying.
    conn.execute(text("ALTER TABLE daemons ADD COLUMN IF NOT EXISTS account_id VARCHAR(64)"))
    conn.execute(text(
        "UPDATE daemons SET account_id = repos.account_id FROM repos "
        "WHERE daemons.repo_id = repos.id AND daemons.account_id IS NULL"
    ))
    conn.execute(text("ALTER TABLE daemons ALTER COLUMN repo_id DROP NOT NULL"))
    # Dedup before the unique index: daemon_name defaults to the constant
    # "daemon", so an account with two repos connected from one host holds two
    # rows per (account_id, daemon_name) — the index would refuse to build and
    # brick the deploy. Keep the most recently seen row per key; detach and
    # drop the rest (activity keeps its rows, daemon_id is nullable).
    conn.execute(text(
        "UPDATE activity_records SET daemon_id = NULL WHERE daemon_id IN ("
        " SELECT id FROM ("
        "  SELECT id, ROW_NUMBER() OVER ("
        "   PARTITION BY account_id, daemon_name"
        "   ORDER BY last_seen_at DESC NULLS LAST, id DESC"
        "  ) AS rn FROM daemons"
        " ) ranked WHERE ranked.rn > 1)"
    ))
    conn.execute(text(
        "DELETE FROM daemons WHERE id IN ("
        " SELECT id FROM ("
        "  SELECT id, ROW_NUMBER() OVER ("
        "   PARTITION BY account_id, daemon_name"
        "   ORDER BY last_seen_at DESC NULLS LAST, id DESC"
        "  ) AS rn FROM daemons"
        " ) ranked WHERE ranked.rn > 1)"
    ))
    conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_daemon_account_name "
        "ON daemons (account_id, daemon_name)"
    ))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_daemons_account_id ON daemons (account_id)"))
    # Runner-quota snapshot mirror (#237) — see models.Daemon.quota_json.
    conn.execute(text("ALTER TABLE daemons ADD COLUMN IF NOT EXISTS quota_json TEXT DEFAULT '[]'"))
    conn.execute(text("ALTER TABLE daemons ADD COLUMN IF NOT EXISTS quota_updated_at TIMESTAMP"))
    # Per-gate ingestion health (#360), published in the quota payload.
    conn.execute(text("ALTER TABLE daemons ADD COLUMN IF NOT EXISTS gate_health_json TEXT DEFAULT '[]'"))
    # Live/coexisting-runs snapshot mirror (#258) — see models.Daemon.live_runs_json.
    conn.execute(text("ALTER TABLE daemons ADD COLUMN IF NOT EXISTS live_runs_json TEXT DEFAULT '[]'"))
    conn.execute(text("ALTER TABLE daemons ADD COLUMN IF NOT EXISTS live_runs_updated_at TIMESTAMP"))
    # PR-review queue snapshot mirror (#259) — see models.Daemon.pr_review_queue_json.
    conn.execute(text("ALTER TABLE daemons ADD COLUMN IF NOT EXISTS pr_review_queue_json TEXT DEFAULT '[]'"))
    conn.execute(text("ALTER TABLE daemons ADD COLUMN IF NOT EXISTS pr_review_queue_updated_at TIMESTAMP"))
    # Closed-run cost ledger snapshot mirror (#271) — see models.Daemon.run_ledger_json.
    conn.execute(text("ALTER TABLE daemons ADD COLUMN IF NOT EXISTS run_ledger_json TEXT DEFAULT '[]'"))
    conn.execute(text("ALTER TABLE daemons ADD COLUMN IF NOT EXISTS run_ledger_updated_at TIMESTAMP"))
    # Configured spawn: pool width, piggybacked on live-runs publish (loom
    # envelope Phase 1) — see models.Daemon.spawn_max_concurrent.
    conn.execute(text("ALTER TABLE daemons ADD COLUMN IF NOT EXISTS spawn_max_concurrent INTEGER"))
    # Runner-catalog snapshot mirror (#328 spool rack) — see models.Daemon.runners_json.
    conn.execute(text("ALTER TABLE daemons ADD COLUMN IF NOT EXISTS runners_json TEXT DEFAULT '[]'"))
    conn.execute(text("ALTER TABLE daemons ADD COLUMN IF NOT EXISTS runners_default VARCHAR(64)"))
    conn.execute(text("ALTER TABLE daemons ADD COLUMN IF NOT EXISTS runners_updated_at TIMESTAMP"))
    conn.execute(text("ALTER TABLE daemons ADD COLUMN IF NOT EXISTS environment_default VARCHAR(32)"))
    conn.execute(text("ALTER TABLE daemons ADD COLUMN IF NOT EXISTS environments_json TEXT DEFAULT '[]'"))


def _migrate_runner_wake_requests(conn: Connection) -> None:
    conn.execute(text("ALTER TABLE runner_wake_requests ADD COLUMN IF NOT EXISTS repo_label VARCHAR(255)"))
    conn.execute(text("ALTER TABLE runner_wake_requests ADD COLUMN IF NOT EXISTS environment VARCHAR(32)"))


def _migrate_channel_routes(conn: Connection) -> None:
    # #409 — authorization principal for the default-closed Telegram gate;
    # existing rows land NULL (no principal), which is deliberately
    # unauthorized until the chat is re-paired via /start.
    conn.execute(text("ALTER TABLE channel_routes ADD COLUMN IF NOT EXISTS paired_user_id INTEGER"))


def _migrate_events(conn: Connection) -> None:
    # Retry-dedupe handle for responded events that keep forwarding
    # continuation messages (the respawn-continuation mute, 2026-07-21).
    conn.execute(text("ALTER TABLE events ADD COLUMN IF NOT EXISTS response_sha VARCHAR(64)"))
    # #525 — telegram image-attachment pointers (never bytes; see
    # models.Event.attachments_json).
    conn.execute(text("ALTER TABLE events ADD COLUMN IF NOT EXISTS attachments_json TEXT DEFAULT '[]'"))


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

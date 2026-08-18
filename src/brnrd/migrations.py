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
        if _table_exists(conn, "tg_pair_codes"):
            _migrate_tg_pair_codes(conn)
        if _table_exists(conn, "events"):
            _migrate_events(conn)
        if _table_exists(conn, "repos"):
            _migrate_repos(conn)
        if _table_exists(conn, "terms_acceptances") and _table_exists(conn, "accounts"):
            _migrate_terms_acceptances(conn)
        if _table_exists(conn, "pair_requests"):
            _migrate_pair_requests(conn)


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
    # ``hosted_terms_accepted_at`` / ``hosted_terms_version`` are deliberately
    # NOT created here any more (#735): acceptance moved to the
    # ``terms_acceptances`` table. A database that already has them keeps them
    # — ``_migrate_terms_acceptances`` copies their contents across and then
    # nothing reads them again. Dropping them is a follow-up, once the copy is
    # confirmed in production; a DROP COLUMN in the same release as the copy
    # would leave no way back if the copy were wrong.
    conn.execute(
        text(
            "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        )
    )

    conn.execute(text("ALTER TABLE accounts ALTER COLUMN email DROP NOT NULL"))
    if _column_exists(conn, "accounts", "password_hash"):
        conn.execute(text("ALTER TABLE accounts ALTER COLUMN password_hash DROP NOT NULL"))

    # w-57 (2026-08-16): brnrd stopped collecting a login email — nothing
    # writes this column any more (see routers/accounts.py::
    # account_for_github_identity). One-time backfill nulls out whatever a
    # prior deploy already collected; idempotent (a WHERE-filtered UPDATE
    # against all-NULL rows touches nothing on every run after the first).
    # The column itself stays — nullable, unindexed reads elsewhere still
    # reference it (account_deletion.py's tombstone write) — dropping it is
    # a follow-up once nothing in the codebase reads it at all.
    conn.execute(text("UPDATE accounts SET email = NULL WHERE email IS NOT NULL"))

    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_accounts_github_id ON accounts (github_id)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_accounts_github_login ON accounts (github_login)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_accounts_email ON accounts (email)"))

    # Discovered corpus mirror. The layers are `authored`, `knowledge` and
    # `runs` — the same three the consent vocabulary names
    # (`brr.gates.cloud._PUBLISH_CORPUS_SLICES`); this comment used to say
    # "replies", a fourth layer nothing produces and no consent can permit.
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

    # Art 17 erasure tombstone (account_deletion.py) — see models.Account.deleted_at.
    conn.execute(text("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP"))

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
    # `repo-initialised` capability source (#1268), also piggybacked on the
    # quota payload — see models.Daemon.repo_agents_md_missing/.repo_kb_missing.
    conn.execute(text("ALTER TABLE daemons ADD COLUMN IF NOT EXISTS repo_agents_md_missing BOOLEAN"))
    conn.execute(text("ALTER TABLE daemons ADD COLUMN IF NOT EXISTS repo_kb_missing BOOLEAN"))
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
    # #566 slice 0: daemon-level telemetry face — see models.Daemon.daemon_mood_json.
    conn.execute(text("ALTER TABLE daemons ADD COLUMN IF NOT EXISTS daemon_mood_json TEXT"))
    # Runner-catalog snapshot mirror (#328 spool rack) — see models.Daemon.runners_json.
    conn.execute(text("ALTER TABLE daemons ADD COLUMN IF NOT EXISTS runners_json TEXT DEFAULT '[]'"))
    conn.execute(text("ALTER TABLE daemons ADD COLUMN IF NOT EXISTS runners_default VARCHAR(64)"))
    conn.execute(text("ALTER TABLE daemons ADD COLUMN IF NOT EXISTS runners_updated_at TIMESTAMP"))
    conn.execute(text("ALTER TABLE daemons ADD COLUMN IF NOT EXISTS environment_default VARCHAR(32)"))
    conn.execute(text("ALTER TABLE daemons ADD COLUMN IF NOT EXISTS environments_json TEXT DEFAULT '[]'"))
    # #932 conversation-sticky mirror + release ask — see models.Daemon.
    conn.execute(text("ALTER TABLE daemons ADD COLUMN IF NOT EXISTS runner_sticky_json TEXT"))
    conn.execute(text("ALTER TABLE daemons ADD COLUMN IF NOT EXISTS runner_sticky_release_at TIMESTAMP"))


def _migrate_runner_wake_requests(conn: Connection) -> None:
    conn.execute(text("ALTER TABLE runner_wake_requests ADD COLUMN IF NOT EXISTS repo_label VARCHAR(255)"))
    conn.execute(text("ALTER TABLE runner_wake_requests ADD COLUMN IF NOT EXISTS environment VARCHAR(32)"))
    # #1492: starvation bound (counter) + visibility (last blocked reason).
    conn.execute(
        text(
            "ALTER TABLE runner_wake_requests "
            "ADD COLUMN IF NOT EXISTS parked_after_refusals INTEGER NOT NULL DEFAULT 0"
        )
    )
    conn.execute(text("ALTER TABLE runner_wake_requests ADD COLUMN IF NOT EXISTS blocked_reason VARCHAR(255)"))


def _migrate_channel_routes(conn: Connection) -> None:
    # #409 — authorization principal for the default-closed Telegram gate;
    # existing rows land NULL (no principal), which is deliberately
    # unauthorized until the chat is re-paired via /start.
    # #1392 — models.py now declares BigInteger (Telegram user ids crossed
    # 2**31-1 in 2021); a fresh table already gets it right via create_all,
    # but an existing INTEGER column needs widening the same way #1377
    # widened events.response_ms.
    conn.execute(text("ALTER TABLE channel_routes ADD COLUMN IF NOT EXISTS paired_user_id BIGINT"))
    _widen_channel_routes_paired_user_id(conn)
    # #1457 — account-level routes: NULL repo_id = "resolved at message
    # time", so the column may no longer be NOT NULL. Existing rows keep
    # their value (they become per-chat pins, semantics unchanged).
    conn.execute(text("ALTER TABLE channel_routes ALTER COLUMN repo_id DROP NOT NULL"))
    # #1464 — the paired-chats surface's display columns; see models.py for
    # what each means. Existing rows land NULL (no display/title captured
    # before this shipped) — harmless, since both are rendering-only.
    conn.execute(text("ALTER TABLE channel_routes ADD COLUMN IF NOT EXISTS paired_user_display VARCHAR(255)"))
    conn.execute(text("ALTER TABLE channel_routes ADD COLUMN IF NOT EXISTS chat_title VARCHAR(255)"))


def _migrate_tg_pair_codes(conn: Connection) -> None:
    # #1457 — account-level pair codes carry no repo; see the matching
    # channel_routes DROP NOT NULL above for what a NULL means downstream.
    conn.execute(text("ALTER TABLE tg_pair_codes ALTER COLUMN repo_id DROP NOT NULL"))
    # #1464 — the minting session's outcome readback; see models.py.
    conn.execute(text("ALTER TABLE tg_pair_codes ADD COLUMN IF NOT EXISTS redeemed_display VARCHAR(255)"))


def _widen_channel_routes_paired_user_id(conn: Connection) -> None:
    data_type = conn.execute(
        text(
            """
            SELECT data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'channel_routes'
              AND column_name = 'paired_user_id'
            """
        )
    ).scalar_one_or_none()
    if data_type is None:
        # create_all already made the column the right width on a fresh DB.
        return
    if data_type != "integer":
        # Already bigint (or wider) — nothing to do. A guard that fires on
        # every type, including bigint, would silently pass over nothing
        # forever if this ever got renamed away from the intended check.
        assert data_type == "bigint", (
            f"unexpected channel_routes.paired_user_id data_type: {data_type!r}"
        )
        return
    conn.execute(text("ALTER TABLE channel_routes ALTER COLUMN paired_user_id TYPE BIGINT"))


def _migrate_events(conn: Connection) -> None:
    # Retry-dedupe handle for responded events that keep forwarding
    # continuation messages (the respawn-continuation mute, 2026-07-21).
    conn.execute(text("ALTER TABLE events ADD COLUMN IF NOT EXISTS response_sha VARCHAR(64)"))
    # #525 — telegram image-attachment pointers (never bytes; see
    # models.Event.attachments_json).
    conn.execute(text("ALTER TABLE events ADD COLUMN IF NOT EXISTS attachments_json TEXT DEFAULT '[]'"))
    # #61 — conversation identity reported by the daemon on response POSTs
    # (set-when-null; see models.Event.conversation_id). The index mirrors the
    # model's ``index=True`` for installs that migrate instead of create.
    conn.execute(text("ALTER TABLE events ADD COLUMN IF NOT EXISTS conversation_id VARCHAR(255)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_events_conversation_id ON events (conversation_id)"))
    # #1377 — response_ms is a millisecond delta that overflowed 32-bit
    # INTEGER once an event sat open past 2**31-1 ms (~24.855 days): the
    # commit 500'd, and in the done path that landed *after* the reply had
    # already forwarded, so every retry re-delivered it. models.py now
    # declares BigInteger; widen any existing INTEGER column to match.
    _widen_events_response_ms(conn)


def _widen_events_response_ms(conn: Connection) -> None:
    data_type = conn.execute(
        text(
            """
            SELECT data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'events'
              AND column_name = 'response_ms'
            """
        )
    ).scalar_one_or_none()
    if data_type is None:
        # create_all already made the column the right width on a fresh DB.
        return
    if data_type != "integer":
        # Already bigint (or wider) — nothing to do. A guard that fires on
        # every type, including bigint, would silently pass over nothing
        # forever if this ever got renamed away from the intended check.
        assert data_type == "bigint", (
            f"unexpected events.response_ms data_type: {data_type!r}"
        )
        return
    conn.execute(text("ALTER TABLE events ALTER COLUMN response_ms TYPE BIGINT"))


def _migrate_repos(conn: Connection) -> None:
    # Explicit publish-scope consent, captured at connect (legal pack item
    # 2, #417 follow-on). NULL on existing rows is deliberate: a repo
    # connected before this column existed carries no consent to enforce,
    # so it keeps its current (daemon-config-only) behaviour untouched.
    conn.execute(text("ALTER TABLE repos ADD COLUMN IF NOT EXISTS publish_layers VARCHAR(255)"))
    # brnrd-bot marker-collaborator state (#874, rescoped) — see
    # models.Repo.github_bot_collaborator. NULL on existing rows means
    # "never checked", not "not a collaborator".
    conn.execute(text("ALTER TABLE repos ADD COLUMN IF NOT EXISTS github_bot_collaborator BOOLEAN"))
    conn.execute(text("ALTER TABLE repos ADD COLUMN IF NOT EXISTS github_bot_notice TEXT"))
    conn.execute(text("ALTER TABLE repos ADD COLUMN IF NOT EXISTS github_bot_checked_at TIMESTAMP"))


def _migrate_pair_requests(conn: Connection) -> None:
    # See models.PairRequest.capabilities_json — carries the connecting
    # daemon's own repo detection across the pair handshake so the browser
    # approval page can bind (or create) the right repo without a dropdown.
    # NULL on existing/in-flight rows: they predate the column and fall back
    # to the old pick-from-a-list behaviour, same as always.
    conn.execute(text("ALTER TABLE pair_requests ADD COLUMN IF NOT EXISTS capabilities_json TEXT"))
    # See models.PairRequest.approve_secret_hash — the initiator proof an
    # approve must present. Backfilled as `''`, which `approve_core` reads
    # as "unapprovable": rows in flight across the deploy fail closed and
    # their humans re-run `brnrd account connect`. That is the intended
    # outcome, not collateral — the alternative is a window in which the
    # account-hijack still works, and the pair TTL is 600 seconds.
    conn.execute(
        text(
            "ALTER TABLE pair_requests ADD COLUMN IF NOT EXISTS "
            "approve_secret_hash VARCHAR(64) NOT NULL DEFAULT ''"
        )
    )


def _migrate_terms_acceptances(conn: Connection) -> None:
    """#735 — carry the legacy hosted-terms acceptances into the new table.

    Two things this deliberately does **not** do.

    It does not manufacture a general-ToS row for anybody. No account has ever
    accepted the general Terms of Service — there was nowhere to record it —
    so every existing account is asked on next login. A backfilled row would
    be a forged consent record, and forging the evidence is the exact defect
    this change exists to remove.

    It does not invent a ``sha256`` for the rows it does carry across. The
    text those users accepted was never pinned and cannot be reconstructed
    (the hosted-execution page was redrafted under an unchanged version
    label), so the hash stays empty and ``terms.text_for_sha256`` reports
    "not recoverable". An acceptance that cannot reproduce its document is
    weak evidence; an acceptance carrying a hash of a document the user never
    saw is false evidence.

    Idempotent: the ``NOT EXISTS`` guard plus ``uq_terms_acceptance`` mean a
    second startup copies nothing, and a user who has since re-accepted
    through the real endpoint keeps their newer, hash-carrying row.
    """
    if not _column_exists(conn, "accounts", "hosted_terms_accepted_at"):
        return
    conn.execute(
        text(
            """
            INSERT INTO terms_acceptances (id, account_id, document, version, sha256, accepted_at)
            SELECT
                'ta_legacy_' || substr(md5(a.id), 1, 24),
                a.id,
                'hosted-execution',
                COALESCE(a.hosted_terms_version, ''),
                '',
                a.hosted_terms_accepted_at
            FROM accounts a
            WHERE a.hosted_terms_accepted_at IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM terms_acceptances t
                  WHERE t.account_id = a.id
                    AND t.document = 'hosted-execution'
                    AND t.version = COALESCE(a.hosted_terms_version, '')
              )
            """
        )
    )


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

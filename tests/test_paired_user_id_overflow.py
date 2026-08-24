"""The paired principal is platform-neutral and lossless.

It began as Telegram's BIGINT ``paired_user_id`` (#1392). WhatsApp exposed
the deeper defect: a successful non-Telegram route had no value in that
column and disappeared from every persisted status view.
"""

from __future__ import annotations

import re
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import String  # noqa: E402
from sqlalchemy import select  # noqa: E402

from brnrd import create_app, migrations  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.models import ChannelRoute  # noqa: E402
from _helpers import brnrd_account_headers  # noqa: E402

_SECRET = "webhook-secret"
_HDR = {"X-Telegram-Bot-Api-Secret-Token": _SECRET}

# A real out-of-range Telegram user id: 32-bit INTEGER tops out at
# 2**31-1 = 2_147_483_647; Telegram ids crossed that around 2021.
_OUT_OF_RANGE_USER_ID = 7_000_000_000
# A supergroup-shaped chat id — Telegram encodes supergroups as large
# *negative* numbers (the -100… form), which blows the 32-bit range from
# the other end. ``channel_id`` is already a String column (never an
# Integer one), so this is coverage that it stays that way, not a fix.
_SUPERGROUP_CHAT_ID = -1009000000000123


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setattr("brnrd.platforms.telegram.send_message", lambda *a, **k: None)
    settings = Settings(
        database_url="sqlite:///:memory:",
        telegram_bot_token="bot:TOKEN",
        telegram_webhook_secret=_SECRET,
    )
    app = create_app(settings)
    return app, TestClient(app)


def _account(client):
    return brnrd_account_headers(
        client.app, github_id="123", login="octocat", email="a@b.com"
    )


def _repo(client, headers, name="demo"):
    return client.post(
        "/v1/accounts/repos",
        json={"repo_full_name": f"Gurio/{name}"},
        headers=headers,
    ).json()["repo_id"]


def _tg_pair_code(client, headers, repo_id):
    return client.post(
        "/v1/accounts/pair/telegram", json={"repo_id": repo_id}, headers=headers
    ).json()["pair_code"]


def _message(chat_id, text, *, user_id=42, message_id=1):
    return {
        "update_id": message_id,
        "message": {
            "chat": {"id": chat_id},
            "from": {"id": user_id, "first_name": "Ada", "username": "ada_l"},
            "message_id": message_id,
            "date": int(time.time()),
            "text": text,
        },
    }


def test_paired_principal_id_is_declared_as_platform_neutral_text():
    assert isinstance(ChannelRoute.__table__.c.paired_principal_id.type, String)


def test_start_binds_chat_for_a_post2021_telegram_user_id(env):
    """Drives the real write path: the webhook `/start` handler
    (`routers/webhooks.py::_handle_start`) constructing `ChannelRoute` with
    the verified sender — not a direct model `.add()`."""
    app, client = env
    acc = _account(client)
    rid = _repo(client, acc, name="myrepo")
    code = _tg_pair_code(client, acc, rid)

    r = client.post(
        "/v1/webhooks/telegram",
        json=_message(555, f"/start {code}", user_id=_OUT_OF_RANGE_USER_ID),
        headers=_HDR,
    )
    assert r.status_code == 200, r.text

    with app.state.SessionLocal() as db:
        binding = db.execute(
            select(ChannelRoute).where(ChannelRoute.channel_id == "555")
        ).scalar_one()
        assert binding.repo_id == rid
        # The value must round-trip exactly, not wrap or truncate.
        assert binding.paired_principal_id == str(_OUT_OF_RANGE_USER_ID)


def test_start_binds_a_supergroup_shaped_negative_chat_id(env):
    """`channel_id` stores the Telegram chat id, and supergroup chat ids are
    large negative numbers — the 32-bit range blown from the other end.
    `channel_id` is already `String`, so this pins that it stays a lossless
    string round-trip rather than ever being narrowed to a numeric column."""
    app, client = env
    acc = _account(client)
    rid = _repo(client, acc, name="myrepo")
    code = _tg_pair_code(client, acc, rid)

    r = client.post(
        "/v1/webhooks/telegram",
        json=_message(_SUPERGROUP_CHAT_ID, f"/start {code}", user_id=42),
        headers=_HDR,
    )
    assert r.status_code == 200, r.text

    with app.state.SessionLocal() as db:
        binding = db.execute(
            select(ChannelRoute).where(ChannelRoute.repo_id == rid)
        ).scalar_one()
        assert binding.channel_id == str(_SUPERGROUP_CHAT_ID)


# Every column name this double knows how to be wrong about. A statement
# naming one of these while it is absent is the failure mode being pinned;
# anything outside the vocabulary is ignored, because this is a fixture and
# not a SQL parser.
_TRACKED_COLUMNS = frozenset({"paired_user_id", "paired_principal_id"})


class MissingColumn(Exception):
    """What Postgres answers as ``UndefinedColumn``, raised where tests read it."""


class _FakeInformationSchemaConn:
    """A migration double that *applies* the DDL it is handed.

    The 2026-08-23 prod outage got past this file because the previous
    version only recorded statements. After
    ``RENAME COLUMN paired_user_id TO paired_principal_id`` it still
    answered "yes, ``paired_user_id`` exists" — so the migration's own
    two-column recovery branch fired against a column that was gone, the
    assertion below it passed on the recorded text, and the container died
    on the first real database it met.

    A double that never applies its own DDL cannot fail the way production
    fails. This one tracks the column set, and raises :class:`MissingColumn`
    the moment a statement names a tracked column it does not have — which
    is exactly what Postgres does, and exactly what the suite was missing.
    """

    def __init__(self, columns, data_type="character varying"):
        self.columns = set(columns)
        self.data_type = data_type
        self.statements: list[str] = []

    def _require(self, statement: str, name: str) -> None:
        if name not in self.columns:
            raise MissingColumn(
                f'column "{name}" does not exist — statement: {" ".join(statement.split())}'
            )

    def _apply(self, statement: str) -> None:
        """Mutate the column set the way Postgres would, then guard reads."""
        squashed = " ".join(statement.split())
        rename = re.search(
            r"RENAME COLUMN (\w+) TO (\w+)", squashed, re.IGNORECASE
        )
        if rename:
            old, new = rename.group(1), rename.group(2)
            self._require(squashed, old)
            self.columns.discard(old)
            self.columns.add(new)
            return
        added = re.search(
            r"ADD COLUMN (?:IF NOT EXISTS )?(\w+)", squashed, re.IGNORECASE
        )
        if added:
            self.columns.add(added.group(1))
            return
        dropped = re.search(r"DROP COLUMN (\w+)", squashed, re.IGNORECASE)
        if dropped:
            self._require(squashed, dropped.group(1))
            self.columns.discard(dropped.group(1))
            return
        # Everything else — UPDATE, SELECT, ALTER COLUMN … TYPE — only reads.
        for name in _TRACKED_COLUMNS:
            if re.search(rf"\b{name}\b", squashed):
                self._require(squashed, name)

    def execute(self, statement, params=None):
        text = str(statement)
        self.statements.append(text)
        if "information_schema.columns" in text:
            if "SELECT data_type" in text:
                return _ScalarResult(self.data_type)
            return _ScalarResult(1 if params["column_name"] in self.columns else None)
        self._apply(text)
        return _ScalarResult(None)


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


def test_migration_renames_the_telegram_column_and_widens_it_to_text():
    conn = _FakeInformationSchemaConn({"paired_user_id"}, data_type="bigint")
    migrations._migrate_channel_routes(conn)
    assert any("RENAME COLUMN paired_user_id TO paired_principal_id" in s for s in conn.statements)
    assert any("ALTER COLUMN paired_principal_id" in s and "VARCHAR(255)" in s for s in conn.statements)


def test_migration_keeps_the_platform_neutral_column_as_the_one_store():
    conn = _FakeInformationSchemaConn({"paired_principal_id"})
    migrations._migrate_channel_routes(conn)
    assert not any("RENAME COLUMN" in s for s in conn.statements)
    assert "paired_principal_id" in ChannelRoute.__table__.c
    assert "paired_user_id" not in ChannelRoute.__table__.c


def test_migration_recovers_an_interrupted_two_column_state_without_two_stores():
    conn = _FakeInformationSchemaConn({"paired_user_id", "paired_principal_id"})
    migrations._migrate_channel_routes(conn)
    assert any("SET paired_principal_id = paired_user_id::text" in s for s in conn.statements)
    assert any("DROP COLUMN paired_user_id" in s for s in conn.statements)


def test_the_rename_does_not_leave_a_read_of_the_column_it_renamed_away():
    """The 2026-08-23 rollout outage, pinned at the statement that caused it.

    A database that still carries the legacy column takes the RENAME branch.
    Before the fix, ``legacy_exists`` was never re-read, so the two-column
    recovery below it fired anyway and issued
    ``SET paired_principal_id = paired_user_id::text`` against a column the
    previous statement had just renamed away. Postgres answers
    ``UndefinedColumn``; the container died at startup; twelve consecutive
    Scaleway rollouts sat in ``pending`` and timed out at 300s while prod
    kept serving the last image that booted.
    """
    conn = _FakeInformationSchemaConn({"paired_user_id"}, data_type="bigint")
    migrations._migrate_channel_routes(conn)

    emitted = [" ".join(s.split()) for s in conn.statements]
    after_rename = emitted[
        next(i for i, s in enumerate(emitted) if "RENAME COLUMN" in s) + 1 :
    ]
    assert not any("paired_user_id" in s for s in after_rename), (
        "a statement after the rename still names the column it renamed away: "
        f"{[s for s in after_rename if 'paired_user_id' in s]}"
    )
    # And the migration still arrives where it was going: one principal
    # store, under the platform-neutral name, and the legacy one retired.
    assert conn.columns & _TRACKED_COLUMNS == {"paired_principal_id"}

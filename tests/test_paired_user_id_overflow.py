"""The paired principal is platform-neutral and lossless.

It began as Telegram's BIGINT ``paired_user_id`` (#1392). WhatsApp exposed
the deeper defect: a successful non-Telegram route had no value in that
column and disappeared from every persisted status view.
"""

from __future__ import annotations

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


class _FakeInformationSchemaConn:
    def __init__(self, columns, data_type="character varying"):
        self.columns = set(columns)
        self.data_type = data_type
        self.statements: list[str] = []

    def execute(self, statement, params=None):
        text = str(statement)
        self.statements.append(text)
        if "information_schema.columns" in text:
            if "SELECT data_type" in text:
                return _ScalarResult(self.data_type)
            return _ScalarResult(1 if params["column_name"] in self.columns else None)
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

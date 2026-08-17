"""#1392 — ``paired_user_id`` overflowed 32-bit INTEGER: Telegram user ids
crossed 2**31-1 around 2021, so any account created since then could not
pair, and the failure landed at write time on a brand-new user's very first
``/start``. See ``brnrd.models.ChannelRoute.paired_user_id`` and
``brnrd.migrations._migrate_channel_routes`` for the fix. Mirrors the
shape of ``test_response_ms_overflow.py`` (#1377), the sibling 32-bit
overflow this same sweep found.
"""

from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import BigInteger  # noqa: E402
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


def test_paired_user_id_is_declared_as_biginteger():
    """The behavioural test below runs on SQLite, whose ints are 64-bit —
    the 32-bit overflow itself cannot reproduce there (confirmed by
    reverting the column to ``Integer`` locally: the behavioural test
    stayed green). This is the assertion that actually pins the fix: a
    rename or an accidental revert back to ``Integer`` would leave that
    test green on SQLite while still failing the write on postgres.
    """
    assert isinstance(ChannelRoute.__table__.c.paired_user_id.type, BigInteger)


def test_start_binds_chat_for_a_post2021_telegram_user_id(env):
    """Drives the real write path: the webhook `/start` handler
    (`routers/webhooks.py::_handle_start`) constructing `ChannelRoute` with
    `paired_user_id=parsed.user_id` — not a direct model `.add()`."""
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
        assert binding.paired_user_id == _OUT_OF_RANGE_USER_ID


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
    """Records executed statements; answers the one information_schema
    lookup the guard issues with a fixed ``data_type``."""

    def __init__(self, data_type):
        self.data_type = data_type
        self.statements: list[str] = []

    def execute(self, statement, params=None):
        text = str(statement)
        self.statements.append(text)
        if "information_schema.columns" in text:
            return _ScalarResult(self.data_type)
        return _ScalarResult(None)


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


def test_migration_widens_an_integer_paired_user_id_column_to_bigint():
    conn = _FakeInformationSchemaConn("integer")
    migrations._widen_channel_routes_paired_user_id(conn)
    altered = [s for s in conn.statements if "ALTER COLUMN paired_user_id" in s]
    assert len(altered) == 1
    assert "BIGINT" in altered[0]


def test_migration_leaves_an_already_bigint_paired_user_id_column_alone():
    conn = _FakeInformationSchemaConn("bigint")
    migrations._widen_channel_routes_paired_user_id(conn)
    altered = [s for s in conn.statements if "ALTER COLUMN paired_user_id" in s]
    assert altered == []


def test_migration_guard_cannot_silently_pass_over_an_unrecognised_type():
    """A rename of the ``data_type`` string this guard checks for must fail
    loudly rather than quietly stop widening anything — this is what makes
    the "leaves bigint alone" test above trustworthy rather than a check
    that happens to pass because the guard fired on nothing.
    """
    conn = _FakeInformationSchemaConn("smallint")
    with pytest.raises(AssertionError):
        migrations._widen_channel_routes_paired_user_id(conn)


def test_migrate_channel_routes_column_still_present_in_the_model():
    assert "paired_user_id" in ChannelRoute.__table__.c

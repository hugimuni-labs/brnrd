"""#1377 — response_ms overflowed 32-bit INTEGER on postgres past ~24.855
days (2**31-1 ms), 500ing the close and, on the done path, doing so *after*
the reply had already forwarded — so every daemon retry re-delivered the
same message. See ``brnrd.models.Event.response_ms`` and
``brnrd.migrations._migrate_events`` for the fix.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import BigInteger  # noqa: E402
from sqlalchemy import select  # noqa: E402

from brnrd import create_app, migrations  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.inbox import CapturingForwarder  # noqa: E402
from brnrd.models import Event  # noqa: E402
from _helpers import brnrd_account_headers  # noqa: E402

# 40 days, comfortably past the 2**31-1 ms (~24.855 day) horizon that
# overflows a 32-bit INTEGER.
_OLD_EVENT_AGE = timedelta(days=40)
_OVERFLOW_MS = 2**31 - 1


@pytest.fixture()
def env(tmp_path):
    forwarder = CapturingForwarder()
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'brnrd-test.db'}")
    app = create_app(settings, forwarder=forwarder)
    client = TestClient(app)
    return app, client, forwarder


def _account(client, email="a@b.com"):
    login = email.split("@", 1)[0].replace(".", "-")
    return brnrd_account_headers(client.app, login=login, email=email)


def _repo(client, headers, name="demo"):
    r = client.post(
        "/v1/accounts/repos",
        json={"repo_full_name": f"Gurio/{name}"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["repo_id"]


def _connect(client, headers, repo_id):
    pair = client.post("/v1/accounts/pair").json()
    code, secret = pair["pair_code"], pair["poll_secret"]
    client.get(f"/v1/accounts/pair/{code}", params={"poll_secret": secret})
    approve = client.post(
        f"/v1/accounts/pair/{code}/approve",
        json={"repo_id": repo_id, "approve_secret": pair["approve_secret"]},
        headers=headers,
    )
    assert approve.status_code == 200, approve.text
    paired = client.get(
        f"/v1/accounts/pair/{code}", params={"poll_secret": secret}
    ).json()
    token = paired["daemon_token"]
    assert token
    return {"Authorization": f"Bearer {token}"}


def _make_old_event(app, client, acc, rid):
    event_id = client.post(
        "/v1/_dev/enqueue", json={"repo_id": rid, "body": "task"}, headers=acc
    ).json()["event_id"]
    with app.state.SessionLocal() as db:
        row = db.execute(select(Event).where(Event.event_id == event_id)).scalar_one()
        row.created_at = datetime.now(timezone.utc) - _OLD_EVENT_AGE
        db.commit()
    return event_id


def test_response_ms_is_declared_as_biginteger():
    """The caller-level tests below run on SQLite, whose ints are 64-bit —
    the overflow itself cannot reproduce there. This is the assertion that
    actually pins the fix: a rename or an accidental revert back to
    ``Integer`` would leave the two behavioural tests green on SQLite while
    still 500ing on postgres.
    """
    assert isinstance(Event.__table__.c.response_ms.type, BigInteger)


def test_record_response_done_path_survives_an_event_older_than_the_32bit_ms_horizon(env):
    app, client, forwarder = env
    acc = _account(client)
    rid = _repo(client, acc)
    dmn = _connect(client, acc, rid)
    event_id = _make_old_event(app, client, acc, rid)

    r = client.post(
        "/v1/daemons/responses",
        json={"event_id": event_id, "body_markdown": "late reply", "status": "done"},
        headers=dmn,
    )
    assert r.status_code == 200, r.text
    assert [item.body for item in forwarder.items] == ["late reply"]

    with app.state.SessionLocal() as db:
        row = db.execute(select(Event).where(Event.event_id == event_id)).scalar_one()
        assert row.status == Event.STATUS_RESPONDED
        assert row.response_ms is not None
        assert row.response_ms > _OVERFLOW_MS

    # A retry after close must still be a quiet ACK, not a second forward —
    # the exact flood #1377 reported when the commit above 500'd instead.
    r2 = client.post(
        "/v1/daemons/responses",
        json={"event_id": event_id, "body_markdown": "late reply", "status": "done"},
        headers=dmn,
    )
    assert r2.status_code == 200, r2.text
    assert len(forwarder.items) == 1


def test_close_noted_survives_an_event_older_than_the_32bit_ms_horizon(env):
    app, client, forwarder = env
    acc = _account(client)
    rid = _repo(client, acc)
    dmn = _connect(client, acc, rid)
    event_id = _make_old_event(app, client, acc, rid)

    before = len(forwarder.items)
    r = client.post(
        "/v1/daemons/responses",
        json={"event_id": event_id, "body_markdown": "", "status": "noted"},
        headers=dmn,
    )
    assert r.status_code == 200, r.text
    assert len(forwarder.items) == before, "a note must not reach the platform"

    with app.state.SessionLocal() as db:
        row = db.execute(select(Event).where(Event.event_id == event_id)).scalar_one()
        assert row.status == Event.STATUS_RESPONDED
        assert row.response_status == "noted"
        assert row.response_ms is not None
        assert row.response_ms > _OVERFLOW_MS


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


def test_migration_widens_an_integer_column_to_bigint():
    conn = _FakeInformationSchemaConn("integer")
    migrations._widen_events_response_ms(conn)
    altered = [s for s in conn.statements if "ALTER COLUMN response_ms" in s]
    assert len(altered) == 1
    assert "BIGINT" in altered[0]


def test_migration_leaves_an_already_bigint_column_alone():
    conn = _FakeInformationSchemaConn("bigint")
    migrations._widen_events_response_ms(conn)
    altered = [s for s in conn.statements if "ALTER COLUMN response_ms" in s]
    assert altered == []


def test_migration_guard_cannot_silently_pass_over_an_unrecognised_type():
    """A rename of the ``data_type`` string this guard checks for must fail
    loudly rather than quietly stop widening anything — this is what makes
    the "leaves bigint alone" test above trustworthy rather than a check
    that happens to pass because the guard fired on nothing.
    """
    conn = _FakeInformationSchemaConn("smallint")
    with pytest.raises(AssertionError):
        migrations._widen_events_response_ms(conn)


def test_migrate_events_column_still_present_in_the_model():
    assert "response_ms" in Event.__table__.c

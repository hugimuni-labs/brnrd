"""One account, one inbox consumer: the newest paired token still polling."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from brnrd import create_app  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.inbox import CapturingForwarder  # noqa: E402
from brnrd.models import Token  # noqa: E402
from _helpers import PUBLISH_EVERYTHING, brnrd_account_headers  # noqa: E402


@pytest.fixture()
def client(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'brnrd-test.db'}",
        inbox_long_poll_max_s=0.05,
        inbox_poll_interval_s=0.01,
    )
    return TestClient(create_app(settings, forwarder=CapturingForwarder()))


def _paired(client, headers, repo_id):
    pair = client.post("/v1/accounts/pair").json()
    approve = client.post(
        f"/v1/accounts/pair/{pair['pair_code']}/approve",
        json={"repo_id": repo_id, "approve_secret": pair["approve_secret"]},
        headers=headers,
    )
    assert approve.status_code == 200, approve.text
    paired = client.get(f"/v1/accounts/pair/{pair['pair_code']}", params={"poll_secret": pair["poll_secret"]}).json()
    return {"Authorization": f"Bearer {paired['daemon_token']}"}


def _poll(client, auth):
    return client.get("/v1/daemons/inbox", params={"since": 0, "wait": 0}, headers=auth)


def _setup(client):
    headers = brnrd_account_headers(client.app, login="sasha", email="sasha@example.com")
    r = client.post(
        "/v1/accounts/repos",
        json={"repo_full_name": "sasha/self", "publish_layers": PUBLISH_EVERYTHING},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    repo_id = r.json()["repo_id"]
    old = _paired(client, headers, repo_id)
    _age_tokens(client, timedelta(hours=1))
    new = _paired(client, headers, repo_id)
    return old, new


def _age_tokens(client, by, *, field="created_at"):
    with client.app.state.SessionLocal() as db:
        for token in db.execute(select(Token).where(Token.kind == Token.KIND_DAEMON)).scalars():
            value = getattr(token, field)
            if value is not None:
                setattr(token, field, value - by)
        db.commit()


def test_an_old_daemon_is_refused_once_a_newer_pairing_polls(client):
    old, new = _setup(client)
    assert _poll(client, old).status_code == 200
    assert _poll(client, new).status_code == 200

    refused = _poll(client, old)
    assert refused.status_code == 409, refused.text
    assert "brnrd daemon uninstall" in refused.json()["detail"]
    assert _poll(client, new).status_code == 200


def test_a_newer_pairing_that_never_polls_takes_nothing(client):
    old, _new = _setup(client)
    assert _poll(client, old).status_code == 200
    assert _poll(client, old).status_code == 200


def test_a_newer_pairing_gone_quiet_hands_the_inbox_back(client):
    old, new = _setup(client)
    assert _poll(client, new).status_code == 200
    with client.app.state.SessionLocal() as db:
        for token in db.execute(select(Token).where(Token.kind == Token.KIND_DAEMON)).scalars():
            if token.polled_at is not None:
                token.polled_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        db.commit()
    assert _poll(client, old).status_code == 200

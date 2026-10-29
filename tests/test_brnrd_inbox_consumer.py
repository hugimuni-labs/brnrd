"""One account, one inbox consumer: a live loom refuses an old daemon's poll."""

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


def _poll(client, auth, consumer=None):
    params = {"since": 0, "wait": 0}
    if consumer:
        params["consumer"] = consumer
    return client.get("/v1/daemons/inbox", params=params, headers=auth)


def _setup(client):
    headers = brnrd_account_headers(client.app, login="sasha", email="sasha@example.com")
    r = client.post(
        "/v1/accounts/repos",
        json={"repo_full_name": "sasha/self", "publish_layers": PUBLISH_EVERYTHING},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    repo_id = r.json()["repo_id"]
    return _paired(client, headers, repo_id), _paired(client, headers, repo_id)


def test_an_old_daemon_is_refused_while_a_loom_polls(client):
    daemon, loom = _setup(client)
    assert _poll(client, daemon).status_code == 200
    assert _poll(client, loom, "loom").status_code == 200

    refused = _poll(client, daemon)
    assert refused.status_code == 409, refused.text
    assert "brnrd daemon uninstall" in refused.json()["detail"]
    assert _poll(client, loom, "loom").status_code == 200


def test_a_daemon_alone_on_its_account_keeps_its_inbox(client):
    daemon, _unused = _setup(client)
    assert _poll(client, daemon).status_code == 200
    assert _poll(client, daemon).status_code == 200


def test_two_looms_on_one_account_are_never_refused(client):
    # Two machines (or a test install signed in as the same person): the
    # inbox lease decides which polls, and the cloud never cuts a loom off.
    older, newer = _setup(client)
    assert _poll(client, newer, "loom").status_code == 200
    assert _poll(client, older, "loom").status_code == 200
    assert _poll(client, newer, "loom").status_code == 200


def test_a_loom_gone_quiet_hands_the_inbox_back(client):
    daemon, loom = _setup(client)
    assert _poll(client, loom, "loom").status_code == 200
    with client.app.state.SessionLocal() as db:
        for token in db.execute(select(Token).where(Token.kind == Token.KIND_DAEMON)).scalars():
            if token.loom_seen_at is not None:
                token.loom_seen_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        db.commit()
    assert _poll(client, daemon).status_code == 200


def test_a_loom_without_the_consumer_tag_counts_by_its_snapshot(client):
    # A 0.6.26 loom polls without ?consumer=loom but PUTs its snapshot every minute.
    daemon, loom = _setup(client)
    assert client.put("/v1/daemons/loom", json={"v": 1, "threads": [], "log": []}, headers=loom).status_code in (200, 403)
    assert _poll(client, loom).status_code == 200
    refused = _poll(client, daemon)
    assert refused.status_code == 409, refused.text
    assert "paired" in refused.json()["detail"]

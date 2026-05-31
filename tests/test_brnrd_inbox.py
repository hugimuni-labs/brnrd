"""End-to-end tests for the brnrd inbox-as-service spine."""

from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient  # noqa: E402

from brnrd import create_app  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.inbox import CapturingForwarder  # noqa: E402
from brnrd.models import Event  # noqa: E402
from sqlalchemy import select  # noqa: E402


@pytest.fixture()
def env():
    forwarder = CapturingForwarder()
    settings = Settings(
        database_url="sqlite:///:memory:",
        inbox_long_poll_max_s=0.4,
        inbox_poll_interval_s=0.02,
    )
    app = create_app(settings, forwarder=forwarder)
    client = TestClient(app)
    return app, client, forwarder


def _account(client, email="a@b.com", password="supersecret"):
    r = client.post("/v1/accounts", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    key = r.json()["api_key"]
    return {"Authorization": f"Bearer {key}"}


def _project(client, headers, name="demo"):
    r = client.post("/v1/accounts/projects", json={"name": name}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


def _connect(client, headers, project_id):
    """Run the device-flow handshake, returning daemon auth headers."""
    pair = client.post("/v1/accounts/pair").json()
    code, secret = pair["pair_code"], pair["poll_secret"]

    # Pending before approval.
    pending = client.get(f"/v1/accounts/pair/{code}", params={"poll_secret": secret})
    assert pending.json()["status"] == "pending"

    approve = client.post(
        f"/v1/accounts/pair/{code}/approve",
        json={"project_id": project_id},
        headers=headers,
    )
    assert approve.status_code == 200, approve.text

    paired = client.get(
        f"/v1/accounts/pair/{code}", params={"poll_secret": secret}
    ).json()
    assert paired["status"] == "paired"
    assert paired["project_id"] == project_id
    token = paired["daemon_token"]
    assert token
    return {"Authorization": f"Bearer {token}"}


def test_healthz(env):
    _, client, _ = env
    assert client.get("/healthz").json()["status"] == "ok"


def test_full_round_trip(env):
    app, client, forwarder = env
    acc = _account(client)
    pid = _project(client, acc)
    dmn = _connect(client, acc, pid)

    assert client.post(
        "/v1/daemons/register", json={"daemon_name": "laptop"}, headers=dmn
    ).status_code == 200

    enq = client.post(
        "/v1/_dev/enqueue",
        json={"project_id": pid, "body": "do the thing", "reply_to": {"chat": 7}},
        headers=acc,
    )
    assert enq.status_code == 201, enq.text
    event_id = enq.json()["event_id"]

    drained = client.get(
        "/v1/daemons/inbox", params={"since": 0, "wait": 0}, headers=dmn
    ).json()
    assert [e["body"] for e in drained["events"]] == ["do the thing"]
    assert drained["events"][0]["reply_to"] == {"chat": 7}
    assert drained["cursor"] == drained["events"][0]["seq"]

    resp = client.post(
        "/v1/daemons/responses",
        json={"event_id": event_id, "body_markdown": "all done", "status": "done"},
        headers=dmn,
    )
    assert resp.status_code == 200, resp.text

    # The body was forwarded out with its reply target, not persisted.
    assert len(forwarder.items) == 1
    item = forwarder.items[0]
    assert item.event_id == event_id
    assert item.body == "all done"
    assert item.reply_to == {"chat": 7}

    assert client.post(
        "/v1/daemons/deregister", json={"daemon_name": "laptop"}, headers=dmn
    ).status_code == 200


def test_response_records_metadata_only(env):
    app, client, forwarder = env
    acc = _account(client)
    pid = _project(client, acc)
    dmn = _connect(client, acc, pid)
    event_id = client.post(
        "/v1/_dev/enqueue", json={"project_id": pid, "body": "task"}, headers=acc
    ).json()["event_id"]
    client.post(
        "/v1/daemons/responses",
        json={"event_id": event_id, "body_markdown": "twelve chars", "status": "done"},
        headers=dmn,
    )

    with app.state.SessionLocal() as db:
        row = db.execute(
            select(Event).where(Event.event_id == event_id)
        ).scalar_one()
        assert row.status == Event.STATUS_RESPONDED
        assert row.response_status == "done"
        assert row.response_len == len("twelve chars")
        assert row.response_ms is not None
        # The inbound + response bodies are both gone from storage.
        assert row.body is None

    # And a re-drain shows the dropped body.
    again = client.get(
        "/v1/daemons/inbox", params={"since": 0, "wait": 0}, headers=dmn
    ).json()
    assert again["events"][0]["body"] is None


def test_long_poll_times_out_empty(env):
    _, client, _ = env
    acc = _account(client)
    pid = _project(client, acc)
    dmn = _connect(client, acc, pid)

    started = time.monotonic()
    result = client.get(
        "/v1/daemons/inbox", params={"since": 0, "wait": 0.3}, headers=dmn
    ).json()
    elapsed = time.monotonic() - started
    assert result["events"] == []
    assert result["cursor"] == 0
    # It actually waited rather than returning instantly.
    assert elapsed >= 0.25


def test_long_poll_wakes_on_enqueue(env):
    _, client, _ = env
    acc = _account(client)
    pid = _project(client, acc)
    dmn = _connect(client, acc, pid)

    def _enqueue_soon():
        time.sleep(0.05)
        client.post(
            "/v1/_dev/enqueue", json={"project_id": pid, "body": "late"}, headers=acc
        )

    t = threading.Thread(target=_enqueue_soon)
    t.start()
    try:
        result = client.get(
            "/v1/daemons/inbox", params={"since": 0, "wait": 2.0}, headers=dmn
        ).json()
    finally:
        t.join()
    assert [e["body"] for e in result["events"]] == ["late"]


def test_cursor_is_idempotent_on_repoll(env):
    _, client, _ = env
    acc = _account(client)
    pid = _project(client, acc)
    dmn = _connect(client, acc, pid)
    client.post("/v1/_dev/enqueue", json={"project_id": pid, "body": "one"}, headers=acc)
    client.post("/v1/_dev/enqueue", json={"project_id": pid, "body": "two"}, headers=acc)

    first = client.get(
        "/v1/daemons/inbox", params={"since": 0, "wait": 0}, headers=dmn
    ).json()
    assert [e["body"] for e in first["events"]] == ["one", "two"]
    # Same cursor re-poll returns the same rows (read-only, idempotent).
    repeat = client.get(
        "/v1/daemons/inbox", params={"since": 0, "wait": 0}, headers=dmn
    ).json()
    assert [e["body"] for e in repeat["events"]] == ["one", "two"]
    # Advancing the cursor drains the rest.
    rest = client.get(
        "/v1/daemons/inbox", params={"since": first["cursor"], "wait": 0}, headers=dmn
    ).json()
    assert rest["events"] == []


def test_project_isolation(env):
    _, client, _ = env
    acc = _account(client)
    pid_a = _project(client, acc, name="a")
    pid_b = _project(client, acc, name="b")
    dmn_a = _connect(client, acc, pid_a)
    dmn_b = _connect(client, acc, pid_b)

    client.post("/v1/_dev/enqueue", json={"project_id": pid_a, "body": "for-a"}, headers=acc)

    a_sees = client.get(
        "/v1/daemons/inbox", params={"since": 0, "wait": 0}, headers=dmn_a
    ).json()
    b_sees = client.get(
        "/v1/daemons/inbox", params={"since": 0, "wait": 0}, headers=dmn_b
    ).json()
    assert [e["body"] for e in a_sees["events"]] == ["for-a"]
    assert b_sees["events"] == []


def test_auth_scoping(env):
    _, client, _ = env
    acc = _account(client)
    pid = _project(client, acc)
    dmn = _connect(client, acc, pid)

    # No credentials.
    assert client.get("/v1/daemons/inbox", params={"wait": 0}).status_code == 401
    # Account key on a daemon endpoint.
    assert client.post(
        "/v1/daemons/register", json={"daemon_name": "x"}, headers=acc
    ).status_code == 403
    # Daemon token on an account endpoint.
    assert client.post(
        "/v1/accounts/projects", json={"name": "z"}, headers=dmn
    ).status_code == 403
    # Garbage token.
    bad = {"Authorization": "Bearer nope"}
    assert client.get("/v1/daemons/inbox", params={"wait": 0}, headers=bad).status_code == 401


def test_pair_poll_rejects_wrong_secret(env):
    _, client, _ = env
    pair = client.post("/v1/accounts/pair").json()
    resp = client.get(
        f"/v1/accounts/pair/{pair['pair_code']}", params={"poll_secret": "wrong"}
    )
    assert resp.status_code == 401


def test_dev_enqueue_rejects_foreign_project(env):
    _, client, _ = env
    acc_a = _account(client, email="a@b.com")
    acc_b = _account(client, email="c@d.com")
    pid_b = _project(client, acc_b, name="b-proj")
    # Account A cannot enqueue into account B's project.
    resp = client.post(
        "/v1/_dev/enqueue", json={"project_id": pid_b, "body": "x"}, headers=acc_a
    )
    assert resp.status_code == 404


def test_project_create_is_idempotent(env):
    _, client, _ = env
    acc = _account(client)
    first = client.post(
        "/v1/accounts/projects", json={"name": "same"}, headers=acc
    ).json()
    second = client.post(
        "/v1/accounts/projects", json={"name": "same"}, headers=acc
    ).json()
    assert first["project_id"] == second["project_id"]
    listing = client.get("/v1/accounts/projects", headers=acc).json()
    assert len(listing["projects"]) == 1

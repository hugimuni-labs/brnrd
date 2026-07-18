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
from _helpers import brnrd_account_headers  # noqa: E402


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
    """Run the device-flow handshake, returning daemon auth headers."""
    pair = client.post("/v1/accounts/pair").json()
    code, secret = pair["pair_code"], pair["poll_secret"]

    # Pending before approval.
    pending = client.get(f"/v1/accounts/pair/{code}", params={"poll_secret": secret})
    assert pending.json()["status"] == "pending"

    approve = client.post(
        f"/v1/accounts/pair/{code}/approve",
        json={"repo_id": repo_id},
        headers=headers,
    )
    assert approve.status_code == 200, approve.text

    paired = client.get(
        f"/v1/accounts/pair/{code}", params={"poll_secret": secret}
    ).json()
    assert paired["status"] == "paired"
    assert paired["repo_id"] == repo_id
    token = paired["daemon_token"]
    assert token
    return {"Authorization": f"Bearer {token}"}


def test_healthz(env):
    _, client, _ = env
    assert client.get("/healthz").json()["status"] == "ok"


def test_full_round_trip(env):
    app, client, forwarder = env
    acc = _account(client)
    rid = _repo(client, acc)
    dmn = _connect(client, acc, rid)

    assert client.post(
        "/v1/daemons/register", json={"daemon_name": "laptop"}, headers=dmn
    ).status_code == 200

    enq = client.post(
        "/v1/_dev/enqueue",
        json={"repo_id": rid, "body": "do the thing", "reply_to": {"chat": 7}},
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
    rid = _repo(client, acc)
    dmn = _connect(client, acc, rid)
    event_id = client.post(
        "/v1/_dev/enqueue", json={"repo_id": rid, "body": "task"}, headers=acc
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


def test_interim_responses_forward_without_closing_the_event(env):
    """Streaming over the cloud relay: interims (``status="processing"``)
    forward to the platform but leave the event open — only the terminal
    ``done`` closes it, and a duplicate terminal after close is ACKed
    without re-forwarding (regression 2026-07-18: the first interim used
    to close the event and swallow the final reply)."""
    app, client, forwarder = env
    acc = _account(client)
    rid = _repo(client, acc)
    dmn = _connect(client, acc, rid)
    event_id = client.post(
        "/v1/_dev/enqueue", json={"repo_id": rid, "body": "task"}, headers=acc
    ).json()["event_id"]

    for body in ("first interim", "second interim"):
        r = client.post(
            "/v1/daemons/responses",
            json={"event_id": event_id, "body_markdown": body, "status": "processing"},
            headers=dmn,
        )
        assert r.status_code == 200, r.text

    with app.state.SessionLocal() as db:
        row = db.execute(select(Event).where(Event.event_id == event_id)).scalar_one()
        assert row.status != Event.STATUS_RESPONDED  # still open for the terminal

    r = client.post(
        "/v1/daemons/responses",
        json={"event_id": event_id, "body_markdown": "final reply", "status": "done"},
        headers=dmn,
    )
    assert r.status_code == 200, r.text
    assert [item.body for item in forwarder.items] == [
        "first interim", "second interim", "final reply",
    ]

    with app.state.SessionLocal() as db:
        row = db.execute(select(Event).where(Event.event_id == event_id)).scalar_one()
        assert row.status == Event.STATUS_RESPONDED

    # Terminal retry after close: ACKed, not double-posted to the platform.
    r = client.post(
        "/v1/daemons/responses",
        json={"event_id": event_id, "body_markdown": "final reply", "status": "done"},
        headers=dmn,
    )
    assert r.status_code == 200
    assert len(forwarder.items) == 3


def test_long_poll_times_out_empty(env):
    _, client, _ = env
    acc = _account(client)
    rid = _repo(client, acc)
    dmn = _connect(client, acc, rid)

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
    rid = _repo(client, acc)
    dmn = _connect(client, acc, rid)

    def _enqueue_soon():
        time.sleep(0.05)
        client.post(
            "/v1/_dev/enqueue",
            json={"repo_id": rid, "body": "late"},
            headers=acc,
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
    rid = _repo(client, acc)
    dmn = _connect(client, acc, rid)
    client.post("/v1/_dev/enqueue", json={"repo_id": rid, "body": "one"}, headers=acc)
    client.post("/v1/_dev/enqueue", json={"repo_id": rid, "body": "two"}, headers=acc)

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
    rid_a = _repo(client, acc, name="a")
    rid_b = _repo(client, acc, name="b")
    dmn_a = _connect(client, acc, rid_a)
    dmn_b = _connect(client, acc, rid_b)

    client.post("/v1/_dev/enqueue", json={"repo_id": rid_a, "body": "for-a"}, headers=acc)

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
    rid = _repo(client, acc)
    dmn = _connect(client, acc, rid)

    # No credentials.
    assert client.get("/v1/daemons/inbox", params={"wait": 0}).status_code == 401
    # Account key on a daemon endpoint.
    assert client.post(
        "/v1/daemons/register", json={"daemon_name": "x"}, headers=acc
    ).status_code == 403
    # Daemon token on an account endpoint.
    assert client.post(
        "/v1/accounts/repos", json={"repo_full_name": "Gurio/z"}, headers=dmn
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
    rid_b = _repo(client, acc_b, name="b-proj")
    # Account A cannot enqueue into account B's project.
    resp = client.post(
        "/v1/_dev/enqueue", json={"repo_id": rid_b, "body": "x"}, headers=acc_a
    )
    assert resp.status_code == 404


def test_delivery_failure_keeps_event_queued_then_recovers():
    """A forwarder failure must not 500, must not mark the event done,
    and must let the daemon retry safely (once) without double-sending."""

    class Flaky:
        def __init__(self):
            self.fail = True
            self.sent = []

        def __call__(self, item):
            if self.fail:
                raise RuntimeError("telegram unreachable")
            self.sent.append(item)

    flaky = Flaky()
    app = create_app(
        Settings(
            database_url="sqlite:///:memory:",
            inbox_long_poll_max_s=0.2,
            inbox_poll_interval_s=0.02,
        ),
        forwarder=flaky,
    )
    client = TestClient(app)
    acc = _account(client)
    rid = _repo(client, acc)
    dmn = _connect(client, acc, rid)
    event_id = client.post(
        "/v1/_dev/enqueue", json={"repo_id": rid, "body": "task"}, headers=acc
    ).json()["event_id"]

    body = {"event_id": event_id, "body_markdown": "answer", "status": "done"}

    # Forward fails -> 502 (not 500); event stays queued with its body.
    bad = client.post("/v1/daemons/responses", json=body, headers=dmn)
    assert bad.status_code == 502
    assert flaky.sent == []
    with app.state.SessionLocal() as db:
        row = db.execute(select(Event).where(Event.event_id == event_id)).scalar_one()
        assert row.status == Event.STATUS_QUEUED
        assert row.body == "task"

    # Recover: the retry delivers, marks responded, drops the body.
    flaky.fail = False
    ok = client.post("/v1/daemons/responses", json=body, headers=dmn)
    assert ok.status_code == 200
    assert len(flaky.sent) == 1
    with app.state.SessionLocal() as db:
        row = db.execute(select(Event).where(Event.event_id == event_id)).scalar_one()
        assert row.status == Event.STATUS_RESPONDED
        assert row.body is None

    # Idempotent: a duplicate POST is a no-op, never a second send.
    again = client.post("/v1/daemons/responses", json=body, headers=dmn)
    assert again.status_code == 200
    assert len(flaky.sent) == 1


def test_repo_create_is_idempotent(env):
    _, client, _ = env
    acc = _account(client)
    first = client.post(
        "/v1/accounts/repos", json={"repo_full_name": "Gurio/same"}, headers=acc
    ).json()
    second = client.post(
        "/v1/accounts/repos", json={"repo_full_name": "Gurio/same"}, headers=acc
    ).json()
    assert first["repo_id"] == second["repo_id"]
    listing = client.get("/v1/accounts/repos", headers=acc).json()
    names = [r["repo_full_name"] for r in listing["repos"]]
    assert names == ["Gurio/same"]


def test_github_account_starts_with_no_repos(env):
    _, client, _ = env
    acc = _account(client, email="seed@b.com")
    listing = client.get("/v1/accounts/repos", headers=acc).json()
    assert listing["repos"] == []


def test_password_account_endpoints_are_not_exposed(env):
    _, client, _ = env
    payload = {"email": "a@b.com", "password": "supersecret"}
    assert client.post("/v1/accounts", json=payload).status_code == 404
    assert client.post("/v1/accounts/sessions", json=payload).status_code == 404


def test_stale_cursor_from_older_epoch_redelivers_queued_backlog(env):
    """A cursor above the repo's max seq is provably from an older DB epoch
    (cursors are derived from delivered seqs). Instead of trusting it — which
    silently skips every queued event — the server resets it to just below
    the oldest still-queued event so the backlog delivers, and returns the
    healed cursor. Live failure 2026-07-09: since=4 against a fresh table
    swallowed a week of messages with no error anywhere."""
    app, client, forwarder = env
    acc = _account(client)
    rid = _repo(client, acc)
    dmn = _connect(client, acc, rid)

    first = client.post(
        "/v1/_dev/enqueue", json={"repo_id": rid, "body": "do you hear me?"}, headers=acc
    ).json()["event_id"]
    client.post(
        "/v1/_dev/enqueue", json={"repo_id": rid, "body": "hola"}, headers=acc
    )

    drained = client.get(
        "/v1/daemons/inbox", params={"since": 999, "wait": 0}, headers=dmn
    ).json()
    assert [e["body"] for e in drained["events"]] == ["do you hear me?", "hola"]
    # The healed cursor rides back so the daemon can persist it.
    assert drained["cursor"] == drained["events"][-1]["seq"]

    # Responded husks (body nulled) below the backlog are not redelivered.
    client.post(
        "/v1/daemons/responses",
        json={"event_id": first, "body_markdown": "done", "status": "done"},
        headers=dmn,
    )
    again = client.get(
        "/v1/daemons/inbox", params={"since": 999, "wait": 0}, headers=dmn
    ).json()
    assert [e["body"] for e in again["events"]] == ["hola"]


def test_stale_cursor_with_no_backlog_heals_to_max_seq(env):
    _, client, _ = env
    acc = _account(client)
    rid = _repo(client, acc)
    dmn = _connect(client, acc, rid)

    empty = client.get(
        "/v1/daemons/inbox", params={"since": 999, "wait": 0}, headers=dmn
    ).json()
    assert empty["events"] == []
    assert empty["cursor"] == 0

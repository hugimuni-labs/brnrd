"""End-to-end tests for the brnrd inbox-as-service spine."""

from __future__ import annotations

import inspect
import threading
import time

import anyio
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient  # noqa: E402

from brnrd import create_app  # noqa: E402
from brnrd import inbox as inbox_service  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.inbox import CapturingForwarder  # noqa: E402
from brnrd.models import Event  # noqa: E402
from brnrd.routers import daemons as daemon_routes  # noqa: E402
from sqlalchemy import select  # noqa: E402
from _helpers import brnrd_account_headers  # noqa: E402


@pytest.fixture()
def env(tmp_path):
    forwarder = CapturingForwarder()
    settings = Settings(
        # A *file*, not `sqlite:///:memory:` — this file is the one place in
        # the suite where two requests are in flight at once (the wake test
        # POSTs `/v1/_dev/enqueue` from a thread while a long-poll is
        # running), and an in-memory URL cannot model that. `db.make_engine`
        # pins `:memory:` to a `StaticPool`, which hands *every* session the
        # same DBAPI connection, so concurrent sessions share one SQLite
        # transaction: the poll loop's `with session_factory() as db:`
        # (`inbox._fetch_since_many_detached`) closes every 20 ms, and that
        # close ROLLBACKs the enqueue's not-yet-committed INSERT out from
        # under it. `inbox.enqueue`'s `db.refresh(event)` then finds no row
        # and raises `InvalidRequestError: Could not refresh instance
        # '<Event ...>'` (2026-08-01 CI). It is purely an artifact of the
        # shared connection — production is Postgres, and even file-backed
        # SQLite gives each session its own connection, so no session can
        # roll back another's work. A file keeps the pool honest while every
        # test here still drives the real endpoints through the real client —
        # nothing about what this file covers changes, only whether the
        # engine under it can model two callers at once. Measured on the
        # 200-run replica of the wake test: `:memory:` fails ~1%, all of them
        # this exception; a file, 0/200.
        database_url=f"sqlite:///{tmp_path / 'brnrd-test.db'}",
        # This cap must stay *above* the largest `wait=` any test here asks
        # for, because the endpoint clamps to `min(wait, cap)` silently: at
        # 0.4 the wake test below declared a 2s budget and got 0.4s, so on a
        # loaded 2-core CI runner the enqueue landed after the poll had
        # already returned empty (`assert [] == ['late']`, #726/#727 CI).
        # The cap is a failure bound, not a schedule — every test still
        # returns the moment its event arrives, so raising it costs nothing
        # on the happy path. Tests wanting a short window pass their own
        # `wait=` (see `test_long_poll_times_out_empty`, which asks 0.3).
        inbox_long_poll_max_s=3.0,
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
        json={"repo_id": repo_id, "approve_secret": pair["approve_secret"]},
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

    # And a re-drain does not hand the answered event back at all. This used
    # to assert `again["events"][0]["body"] is None` — pinning the husk's
    # *shape* on redelivery, which read as coverage of a behaviour that was
    # in fact the 2026-07-30 replay: 181 body-less husks became 181 pending
    # events on a daemon whose cursor had been reset to zero.
    again = client.get(
        "/v1/daemons/inbox", params={"since": 0, "wait": 0}, headers=dmn
    ).json()
    assert again["events"] == []


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


def test_continuation_messages_forward_after_the_event_closed(env):
    """A respawn continuation inherits its parent's cloud event id, so its
    messages arrive after the parent's terminal ``done`` closed the event.
    They must still forward — only a byte-identical retry of the last
    forwarded body is quietly ACKed (regression 2026-07-21: the hard
    responded-guard 200-ACKed and dropped an entire continuation run's
    output, interims and final reply alike)."""
    app, client, forwarder = env
    acc = _account(client)
    rid = _repo(client, acc)
    dmn = _connect(client, acc, rid)
    event_id = client.post(
        "/v1/_dev/enqueue", json={"repo_id": rid, "body": "task"}, headers=acc
    ).json()["event_id"]

    # Parent run closes the event.
    client.post(
        "/v1/daemons/responses",
        json={"event_id": event_id, "body_markdown": "parent done", "status": "done"},
        headers=dmn,
    )

    # Continuation run speaks into the closed event: interims and terminal.
    for body, status in (
        ("child interim", "processing"),
        ("child final", "done"),
    ):
        r = client.post(
            "/v1/daemons/responses",
            json={"event_id": event_id, "body_markdown": body, "status": status},
            headers=dmn,
        )
        assert r.status_code == 200, r.text
    assert [item.body for item in forwarder.items] == [
        "parent done", "child interim", "child final",
    ]

    # Exact retry of the last forwarded body: quiet ACK, no double post.
    r = client.post(
        "/v1/daemons/responses",
        json={"event_id": event_id, "body_markdown": "child final", "status": "done"},
        headers=dmn,
    )
    assert r.status_code == 200
    assert len(forwarder.items) == 3

    # The event stays closed throughout.
    with app.state.SessionLocal() as db:
        row = db.execute(select(Event).where(Event.event_id == event_id)).scalar_one()
        assert row.status == Event.STATUS_RESPONDED


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


def test_long_poll_route_waits_async_and_offloads_each_db_read(monkeypatch):
    """A connected daemon must not occupy one AnyIO worker for its wait."""

    caller_thread = threading.get_ident()
    db_threads = []

    def fake_fetch(session_factory, repo_ids, since, limit=None):
        db_threads.append(threading.get_ident())
        return []

    monkeypatch.setattr(
        inbox_service,
        "_fetch_since_many_detached",
        fake_fetch,
    )

    async def exercise():
        ticks = 0
        polling = True

        async def ticker():
            nonlocal ticks
            while polling:
                ticks += 1
                await anyio.sleep(0.002)

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(ticker)
            result = await inbox_service.long_poll_many(
                object(),
                {"repo-1"},
                0,
                max_wait_s=0.03,
                interval_s=0.005,
            )
            polling = False
        return result, ticks

    result, ticks = anyio.run(exercise)

    assert inspect.iscoroutinefunction(daemon_routes.inbox)
    assert result == []
    assert ticks >= 3, "the poll interval blocked the event loop"
    assert db_threads
    assert all(thread_id != caller_thread for thread_id in db_threads)


def test_long_poll_wakes_on_enqueue(env, monkeypatch):
    """An enqueue landing *during* a wait must wake the poll that is waiting.

    The ordering here is a barrier, never a sleep (#1081). This thread used
    to `time.sleep(0.05)` and hope the main thread had reached the poll by
    then; on a loaded box it had not, and both requests entered FastAPI's
    router at once. That is a real data race, not a slow start: FastAPI
    0.139's `_IncludedRouter.effective_candidates()` memoizes lazily and
    publishes the *empty* list (`self._effective_candidates = []`) before it
    refills it, setting the version guard only at the end — so two threads
    materializing the route table on first touch can leave one of them
    walking a half-built list. The poll then 404s on a URL that works on the
    very next call, and the test dies on `KeyError: 'events'` (reproduced
    11/30 with the sleep, pinned to one CPU against 3 burners; 19/30 green).

    So the release signal is the poll's own first DB read: by the time
    `_fetch_since_many_detached` runs, the GET has been routed, its handler
    entered, and no router state is touched again for the rest of the wait —
    the enqueue's own first touch of the dev router therefore races nothing.
    No wall-clock constant decides any of it. The `timeout=` below is a
    failure bound, not a schedule: a hung test is worse than a red one, and
    on the happy path nothing ever waits for it.

    What keeps this a *wake* and not merely a return: that first read is
    observed, and it must come back empty. The enqueue cannot begin before
    it, so the row cannot pre-date the poll — the poll can only be holding
    an event that appeared while it was already waiting.
    """
    _, client, _ = env
    acc = _account(client)
    rid = _repo(client, acc)
    dmn = _connect(client, acc, rid)
    thread_errors = []
    poll_reached_db = threading.Event()
    first_read_row_count = []

    real_fetch = inbox_service._fetch_since_many_detached

    def observed_fetch(session_factory, repo_ids, since, limit=None):
        rows = real_fetch(session_factory, repo_ids, since, limit)
        if not poll_reached_db.is_set():
            first_read_row_count.append(len(rows))
            poll_reached_db.set()
        return rows

    monkeypatch.setattr(inbox_service, "_fetch_since_many_detached", observed_fetch)

    def _enqueue_once_the_poll_is_waiting():
        try:
            if not poll_reached_db.wait(timeout=10.0):
                raise AssertionError("the long poll never reached its first DB read")
            response = client.post(
                "/v1/_dev/enqueue",
                json={"repo_id": rid, "body": "late"},
                headers=acc,
            )
            response.raise_for_status()
        except BaseException as exc:
            thread_errors.append(exc)

    t = threading.Thread(target=_enqueue_once_the_poll_is_waiting)
    t.start()
    try:
        result = client.get(
            "/v1/daemons/inbox", params={"since": 0, "wait": 2.0}, headers=dmn
        ).json()
    finally:
        t.join()
    if thread_errors:
        raise thread_errors[0]
    # The poll looked and found nothing before the enqueue was allowed to
    # fire; anything it returns below arrived mid-wait.
    assert first_read_row_count == [0]
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


def test_inbox_carries_the_server_fingerprint(env, tmp_path, monkeypatch):
    """The prod fingerprint rides the channel the daemon already polls
    (2026-07-30 task): no new endpoint, no new poll — just an extra block
    on the response the long-poll already returns."""
    from brnrd import version_info

    _, client, _ = env
    acc = _account(client)
    rid = _repo(client, acc)
    dmn = _connect(client, acc, rid)

    stamped = tmp_path / "build_info.txt"
    stamped.write_text(
        "bebd5c1d\n2026-07-30T10:19:01+00:00\ngit\n", encoding="utf-8",
    )
    monkeypatch.setattr(version_info, "_BUILD_INFO_PATH", stamped)

    resp = client.get(
        "/v1/daemons/inbox", params={"since": 0, "wait": 0}, headers=dmn
    ).json()
    server = resp["server"]
    assert server["build"] == {
        "commit": "bebd5c1d",
        "built_at": "2026-07-30T10:19:01+00:00",
        "started_at": version_info._STARTED_AT,
    }
    github = server["github"]
    assert github["bot_login"] == "brnrd-bot"
    assert github["app_slug"] == "brnrd-dev"
    assert github["trigger_label"] == "brnrd"
    assert github["trigger_aliases"] == ["brnrd", "brr"]
    # Never a credential value — booleans only.
    assert github["webhook_secret_set"] is False
    assert github["bot_token_set"] is False
    assert github["app_id_set"] is False
    assert github["app_key_set"] is False
    assert set(github) == {
        "bot_login", "app_slug", "trigger_label", "trigger_aliases",
        "webhook_secret_set", "bot_token_set", "app_id_set", "app_key_set",
    }


def test_inbox_fingerprint_reports_the_app_identity_that_publishing_needs():
    """The credential a managed runner *pushes* with was the one this
    surface did not carry (2026-07-31, the Scaleway cutover).

    Prod read ``webhook secret set · bot token set`` — both true, both
    irrelevant to publishing — while every mint of an installation token had
    been failing for six hours. The App id is not a secret and the key is;
    they are reported the same way here because they answer one question.
    """
    settings = Settings(
        database_url="sqlite:///:memory:",
        inbox_long_poll_max_s=3.0,
        inbox_poll_interval_s=0.02,
        github_app_id="1234567",
        github_app_private_key_b64="cHJpdmF0ZSBrZXk=",
    )
    client = TestClient(create_app(settings, forwarder=CapturingForwarder()))
    acc = _account(client)
    rid = _repo(client, acc)
    dmn = _connect(client, acc, rid)
    resp = client.get(
        "/v1/daemons/inbox", params={"since": 0, "wait": 0}, headers=dmn
    ).json()
    body = resp["server"]["github"]
    assert body["app_id_set"] is True
    assert body["app_key_set"] is True
    # The key is a secret; the boolean is the whole report.
    assert "cHJpdmF0ZSBrZXk=" not in str(resp)


def test_inbox_fingerprint_separates_a_carried_key_from_a_dropped_id():
    """The exact cutover shape: the migration copied the rows marked
    **Secret** and dropped the plain App id, so the deployment held half an
    App identity — and ``app_jwt`` raises on either half."""
    settings = Settings(
        database_url="sqlite:///:memory:",
        inbox_long_poll_max_s=3.0,
        inbox_poll_interval_s=0.02,
        github_app_private_key_b64="cHJpdmF0ZSBrZXk=",
    )
    client = TestClient(create_app(settings, forwarder=CapturingForwarder()))
    acc = _account(client)
    rid = _repo(client, acc)
    dmn = _connect(client, acc, rid)
    body = client.get(
        "/v1/daemons/inbox", params={"since": 0, "wait": 0}, headers=dmn
    ).json()["server"]["github"]
    assert body["app_key_set"] is True
    assert body["app_id_set"] is False


def test_inbox_server_fingerprint_reports_credentials_set_as_booleans():
    """Never a credential value — booleans only, for both secret fields."""
    settings = Settings(
        database_url="sqlite:///:memory:",
        inbox_long_poll_max_s=3.0,
        inbox_poll_interval_s=0.02,
        github_webhook_secret="shh-do-not-leak",
        github_bot_token="ghp_do-not-leak-either",
    )
    app = create_app(settings, forwarder=CapturingForwarder())
    client = TestClient(app)
    acc = _account(client)
    rid = _repo(client, acc)
    dmn = _connect(client, acc, rid)
    resp = client.get(
        "/v1/daemons/inbox", params={"since": 0, "wait": 0}, headers=dmn
    ).json()
    body = resp["server"]["github"]
    assert body["webhook_secret_set"] is True
    assert body["bot_token_set"] is True
    assert "shh-do-not-leak" not in str(resp)
    assert "ghp_do-not-leak-either" not in str(resp)


def test_account_daemons_receive_events_for_every_repo(env):
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
    assert [e["body"] for e in b_sees["events"]] == ["for-a"]
    assert a_sees["events"][0]["repo_label"] == "Gurio/a"
    assert b_sees["events"][0]["repo_label"] == "Gurio/a"


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


def test_a_reset_cursor_replays_only_what_is_still_unanswered(env):
    """The husk guard has to hold for *any* arrangement, and at ``since=0``.

    `test_stale_cursor_from_older_epoch_redelivers_queued_backlog` asserts
    husks are not redelivered — but only proves it for the arrangement where
    the answered event is the *oldest*, which is exactly where
    `clamp_since`'s ``oldest_queued - 1`` floor happens to exclude it. Flip
    the order and the floor stops covering it; and a cursor of 0 skips the
    clamp entirely (`routers/daemons.py` guards on ``since > 0``), so no
    floor is computed at all.

    Live 2026-07-30: `account connect` wrote ``since: 0`` after the Scaleway
    cutover and the daemon was handed the whole event table — 181 of the 339
    were answered husks with no body, each one an empty run.
    """
    app, client, forwarder = env
    acc = _account(client)
    rid = _repo(client, acc)
    dmn = _connect(client, acc, rid)

    # Never answered, and *first* — in production this is the never-closed
    # event that pins `clamp_since`'s floor at the beginning of history.
    client.post("/v1/_dev/enqueue", json={"repo_id": rid, "body": "still open"}, headers=acc)
    answered = client.post(
        "/v1/_dev/enqueue", json={"repo_id": rid, "body": "handled"}, headers=acc
    ).json()["event_id"]
    client.post(
        "/v1/daemons/responses",
        json={"event_id": answered, "body_markdown": "done", "status": "done"},
        headers=dmn,
    )

    # A cursor of zero: the shape a re-pair writes. The answered event stays
    # off the wire on identity, not on position.
    from_scratch = client.get(
        "/v1/daemons/inbox", params={"since": 0, "wait": 0}, headers=dmn
    ).json()
    assert [e["body"] for e in from_scratch["events"]] == ["still open"]

    # And above the clamp's floor, where the old fixture's ordering hid it.
    healed = client.get(
        "/v1/daemons/inbox", params={"since": 999, "wait": 0}, headers=dmn
    ).json()
    assert [e["body"] for e in healed["events"]] == ["still open"]


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


def test_gc_events_nulls_dead_bodies_and_prunes_old_rows(env):
    """#502: the queue is a relay, not an archive — dead bodies null at 14d,
    rows prune at 90d, live traffic is untouched."""
    from datetime import datetime, timedelta, timezone

    from brnrd import inbox as inbox_service

    app, client, forwarder = env
    headers = _account(client)
    repo_id = _repo(client, headers)
    now = datetime.now(timezone.utc)
    with app.state.SessionLocal() as db:
        db.add(
            Event(
                event_id="evt-ancient",
                repo_id=repo_id,
                source="dev",
                body=None,
                reply_to="{}",
                status=Event.STATUS_RESPONDED,
                created_at=now - timedelta(days=120),
            )
        )
        db.add(
            Event(
                event_id="evt-dead-queued",
                repo_id=repo_id,
                source="dev",
                body="never answered",
                reply_to="{}",
                status=Event.STATUS_QUEUED,
                created_at=now - timedelta(days=30),
            )
        )
        db.add(
            Event(
                event_id="evt-fresh",
                repo_id=repo_id,
                source="dev",
                body="fresh",
                reply_to="{}",
                status=Event.STATUS_QUEUED,
                created_at=now,
            )
        )
        db.commit()

    with app.state.SessionLocal() as db:
        # brnrd#1388's expiry sweep would otherwise close `evt-dead-queued`
        # (30 days old) before this test ever gets to observe the
        # body-null-but-still-queued state it's isolating — pass a horizon
        # well past its age so the two sweeps stay decoupled here; the
        # expiry sweep itself has its own tests below.
        inbox_service.gc_events(
            db, force=True, stale_event_horizon=timedelta(days=9999),
        )

    with app.state.SessionLocal() as db:
        remaining = {e.event_id: e for e in db.execute(select(Event)).scalars()}
        assert "evt-ancient" not in remaining
        assert remaining["evt-dead-queued"].body is None
        assert remaining["evt-dead-queued"].status == Event.STATUS_QUEUED
        assert remaining["evt-fresh"].body == "fresh"


def test_gc_events_hourly_throttle(env):
    """The opportunistic sweep runs at most once per interval per process."""
    from datetime import datetime, timedelta, timezone

    from brnrd import inbox as inbox_service

    app, client, forwarder = env
    headers = _account(client)
    repo_id = _repo(client, headers)
    now = datetime.now(timezone.utc)
    inbox_service.reset_gc_throttle()
    with app.state.SessionLocal() as db:
        inbox_service.gc_events(db)  # arms the throttle
        db.add(
            Event(
                event_id="evt-late-arrival",
                repo_id=repo_id,
                source="dev",
                body="old",
                reply_to="{}",
                status=Event.STATUS_QUEUED,
                created_at=now - timedelta(days=200),
            )
        )
        db.commit()
        inbox_service.gc_events(db)  # throttled: must not touch the new row
    with app.state.SessionLocal() as db:
        assert db.execute(select(Event).where(Event.event_id == "evt-late-arrival")).scalar_one().body == "old"


def test_gc_events_expires_stale_queued_rows_past_horizon(env):
    """brnrd#1388, server half: a queued row past the horizon closes
    `expired` on the server's own clock — it never depended on a daemon
    ever noting it. A row still inside the horizon is untouched."""
    from datetime import datetime, timedelta, timezone

    from brnrd import inbox as inbox_service
    from brnrd.inbox import RESPONSE_STATUS_EXPIRED

    app, client, forwarder = env
    headers = _account(client)
    repo_id = _repo(client, headers)
    now = datetime.now(timezone.utc)
    with app.state.SessionLocal() as db:
        db.add(
            Event(
                event_id="evt-stale",
                repo_id=repo_id,
                source="dev",
                body="nobody ever noted this",
                reply_to="{}",
                status=Event.STATUS_QUEUED,
                created_at=now - timedelta(hours=72),
            )
        )
        db.add(
            Event(
                event_id="evt-within-horizon",
                repo_id=repo_id,
                source="dev",
                body="still young",
                reply_to="{}",
                status=Event.STATUS_QUEUED,
                created_at=now - timedelta(hours=1),
            )
        )
        db.commit()

    with app.state.SessionLocal() as db:
        # No stale_event_horizon override ⇒ the 48h module default.
        inbox_service.gc_events(db, force=True)

    with app.state.SessionLocal() as db:
        by_id = {e.event_id: e for e in db.execute(select(Event)).scalars()}
        stale = by_id["evt-stale"]
        assert stale.status == Event.STATUS_RESPONDED
        assert stale.response_status == RESPONSE_STATUS_EXPIRED
        assert stale.body is None
        assert stale.attachments_json == "[]"
        assert stale.responded_at is not None

        fresh = by_id["evt-within-horizon"]
        assert fresh.status == Event.STATUS_QUEUED
        assert fresh.body == "still young"
        assert fresh.response_status is None


def test_gc_events_stale_horizon_disabled_by_non_positive_value(env):
    """A non-positive override turns the sweep off, mirroring the daemon
    side's `dispatch.stale_event_horizon_hours<=0` "off means off" shape —
    not a horizon of zero that expires everything instantly."""
    from datetime import datetime, timedelta, timezone

    from brnrd import inbox as inbox_service

    app, client, forwarder = env
    headers = _account(client)
    repo_id = _repo(client, headers)
    now = datetime.now(timezone.utc)
    with app.state.SessionLocal() as db:
        db.add(
            Event(
                event_id="evt-ancient-queued",
                repo_id=repo_id,
                source="dev",
                body="still here",
                reply_to="{}",
                status=Event.STATUS_QUEUED,
                created_at=now - timedelta(days=10),
            )
        )
        db.commit()

    with app.state.SessionLocal() as db:
        inbox_service.gc_events(
            db, force=True, stale_event_horizon=timedelta(0),
        )

    with app.state.SessionLocal() as db:
        row = db.execute(
            select(Event).where(Event.event_id == "evt-ancient-queued")
        ).scalar_one()
        assert row.status == Event.STATUS_QUEUED
        assert row.body == "still here"


def test_gc_events_expiry_unpins_clamp_since_floor(env):
    """brnrd#1388: `clamp_since`'s `oldest_queued - 1` floor used to depend
    on every old queued row eventually being noted by a connected daemon —
    a single row nobody ever noted pinned it at the beginning of time. The
    expiry sweep unpins it on its own schedule: once the ancient row closes
    `expired`, it stops counting as "queued" and the floor advances past it."""
    from datetime import datetime, timedelta, timezone

    from brnrd import inbox as inbox_service

    app, client, forwarder = env
    acc = _account(client)
    rid = _repo(client, acc)
    now = datetime.now(timezone.utc)

    with app.state.SessionLocal() as db:
        # The never-noted row: old enough to be well past the default
        # horizon, and — being the *oldest* queued row — exactly what pins
        # clamp_since's floor before the sweep ever runs.
        db.add(
            Event(
                event_id="evt-never-noted",
                repo_id=rid,
                source="dev",
                body="from before the migration",
                reply_to="{}",
                status=Event.STATUS_QUEUED,
                created_at=now - timedelta(days=5),
            )
        )
        db.commit()
        oldest_seq = db.execute(
            select(Event.seq).where(Event.event_id == "evt-never-noted")
        ).scalar_one()

    # A fresh, still-open event above the ancient row.
    fresh_id = client.post(
        "/v1/_dev/enqueue", json={"repo_id": rid, "body": "hello"}, headers=acc
    ).json()["event_id"]

    with app.state.SessionLocal() as db:
        before = inbox_service.clamp_since(db, rid, since=999_999)
        assert before == oldest_seq - 1  # pinned by the never-noted row

        inbox_service.gc_events(db, force=True)  # 48h default expires it

        after = inbox_service.clamp_since(db, rid, since=999_999)
        fresh_seq = db.execute(
            select(Event.seq).where(Event.event_id == fresh_id)
        ).scalar_one()
        # The ancient row no longer counts as queued, so the floor now
        # tracks the fresh event instead of the row that used to pin it.
        assert after == fresh_seq - 1
        assert after > before


def test_activity_publish_honors_configured_stale_event_horizon(tmp_path):
    """The horizon `PUT /v1/daemons/activity` threads into `gc_events` is a
    live setting (`inbox_stale_event_horizon_hours`), not the module
    constant — an operator override takes effect without touching
    `inbox.py`. A tight 1h horizon here expires a 2h-old queued row purely
    by publishing activity, the ordinary daemon heartbeat path."""
    from datetime import datetime, timedelta, timezone

    from brnrd import inbox as inbox_service
    from brnrd.inbox import RESPONSE_STATUS_EXPIRED

    forwarder = CapturingForwarder()
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'brnrd-test-horizon.db'}",
        inbox_stale_event_horizon_hours=1.0,
    )
    app = create_app(settings, forwarder=forwarder)
    client = TestClient(app)
    acc = _account(client)
    rid = _repo(client, acc)
    dmn = _connect(client, acc, rid)

    now = datetime.now(timezone.utc)
    with app.state.SessionLocal() as db:
        db.add(
            Event(
                event_id="evt-two-hours-old",
                repo_id=rid,
                source="dev",
                body="two hours old",
                reply_to="{}",
                status=Event.STATUS_QUEUED,
                created_at=now - timedelta(hours=2),
            )
        )
        db.commit()

    inbox_service.reset_gc_throttle()
    resp = client.put("/v1/daemons/activity", json={"records": []}, headers=dmn)
    assert resp.status_code == 200, resp.text

    with app.state.SessionLocal() as db:
        row = db.execute(
            select(Event).where(Event.event_id == "evt-two-hours-old")
        ).scalar_one()
        assert row.status == Event.STATUS_RESPONDED
        assert row.response_status == RESPONSE_STATUS_EXPIRED
        assert row.body is None


def test_response_sets_conversation_id_when_null(env):
    """#61 — the acceptor adopts the daemon-reported conversation_id."""
    app, client, forwarder = env
    acc = _account(client)
    rid = _repo(client, acc)
    dmn = _connect(client, acc, rid)
    event_id = client.post(
        "/v1/_dev/enqueue", json={"repo_id": rid, "body": "task"}, headers=acc
    ).json()["event_id"]

    resp = client.post(
        "/v1/daemons/responses",
        json={
            "event_id": event_id,
            "body_markdown": "done",
            "status": "done",
            "conversation_id": "telegram:-100123:",
        },
        headers=dmn,
    )
    assert resp.status_code == 200, resp.text

    with app.state.SessionLocal() as db:
        row = db.execute(select(Event).where(Event.event_id == event_id)).scalar_one()
        assert row.conversation_id == "telegram:-100123:"


def test_response_preserves_existing_conversation_id(env):
    """#61 — set-when-null only: a later post never overwrites the value."""
    app, client, forwarder = env
    acc = _account(client)
    rid = _repo(client, acc)
    dmn = _connect(client, acc, rid)
    event_id = client.post(
        "/v1/_dev/enqueue", json={"repo_id": rid, "body": "task"}, headers=acc
    ).json()["event_id"]

    # An interim post sets it first (persists even though the event stays open)…
    client.post(
        "/v1/daemons/responses",
        json={
            "event_id": event_id,
            "body_markdown": "working",
            "status": "processing",
            "conversation_id": "telegram:-100123:",
        },
        headers=dmn,
    )
    # …and the terminal post reporting a different value does not win.
    client.post(
        "/v1/daemons/responses",
        json={
            "event_id": event_id,
            "body_markdown": "done",
            "status": "done",
            "conversation_id": "slack:C42:1234.5",
        },
        headers=dmn,
    )

    with app.state.SessionLocal() as db:
        row = db.execute(select(Event).where(Event.event_id == event_id)).scalar_one()
        assert row.conversation_id == "telegram:-100123:"


def test_response_without_conversation_id_stays_compatible(env):
    """#61 — pre-#61 daemons omit the field; the post still lands unchanged."""
    app, client, forwarder = env
    acc = _account(client)
    rid = _repo(client, acc)
    dmn = _connect(client, acc, rid)
    event_id = client.post(
        "/v1/_dev/enqueue", json={"repo_id": rid, "body": "task"}, headers=acc
    ).json()["event_id"]

    resp = client.post(
        "/v1/daemons/responses",
        json={"event_id": event_id, "body_markdown": "done", "status": "done"},
        headers=dmn,
    )
    assert resp.status_code == 200, resp.text
    assert len(forwarder.items) == 1

    with app.state.SessionLocal() as db:
        row = db.execute(select(Event).where(Event.event_id == event_id)).scalar_one()
        assert row.status == Event.STATUS_RESPONDED
        assert row.conversation_id is None


# ── the forwarder learns a table ──────────────────────────────────────
#
# `make_default_forwarder`'s dispatch table replaced an if/elif chain that
# grew by one clause per platform. These pin the table's actual routing —
# each known platform's handler fires with the right arguments, an unknown
# one is a silent no-op (never an exception) — independent of any HTTP
# router, so a future platform addition here can't quietly break another
# platform's dispatch.


def test_forwarder_table_routes_telegram(monkeypatch):
    from brnrd.inbox import ForwardItem, make_default_forwarder

    sent = []
    monkeypatch.setattr(
        "brnrd.platforms.telegram.send_message",
        lambda token, chat_id, text, **kw: sent.append((token, chat_id, text, kw)),
    )
    forwarder = make_default_forwarder(
        Settings(database_url="sqlite:///:memory:", telegram_bot_token="bot:T")
    )
    forwarder(
        ForwardItem(
            event_id="e1",
            reply_to={"platform": "telegram", "chat_id": "555", "topic_id": 9, "message_id": 42},
            body="hi",
            status="done",
        )
    )
    assert sent == [("bot:T", "555", "hi", {"topic_id": 9, "reply_to_message_id": 42})]


def test_forwarder_table_skips_telegram_without_token():
    from brnrd.inbox import ForwardItem, make_default_forwarder

    # No telegram_bot_token configured -> the telegram handler is a no-op,
    # not an exception (parity with the old if/elif's guard clause).
    forwarder = make_default_forwarder(Settings(database_url="sqlite:///:memory:"))
    forwarder(
        ForwardItem(
            event_id="e1",
            reply_to={"platform": "telegram", "chat_id": "555"},
            body="hi",
            status="done",
        )
    )  # no exception


def test_forwarder_table_routes_github(monkeypatch):
    from brnrd.inbox import ForwardItem, make_default_forwarder

    posted = []
    monkeypatch.setattr(
        "brnrd.platforms.github.post_issue_comment",
        lambda token, base, version, repo, issue_number, body, **kw: posted.append(
            (token, repo, issue_number, body)
        ),
    )
    forwarder = make_default_forwarder(
        Settings(database_url="sqlite:///:memory:", github_bot_token="ghs_tok")
    )
    forwarder(
        ForwardItem(
            event_id="e1",
            reply_to={"platform": "github", "repo": "owner/repo", "issue_number": 17},
            body="fixed",
            status="done",
        )
    )
    assert len(posted) == 1
    assert posted[0][0] == "ghs_tok"
    assert posted[0][1] == "owner/repo"
    assert posted[0][2] == 17
    assert posted[0][3].endswith("fixed")


def test_forwarder_table_routes_whatsapp(monkeypatch):
    from brnrd.inbox import ForwardItem, make_default_forwarder

    sent = []
    monkeypatch.setattr(
        "brnrd.platforms.whatsapp.send_message",
        lambda token, phone_id, to, text, **kw: sent.append((token, phone_id, to, text, kw)),
    )
    forwarder = make_default_forwarder(
        Settings(
            database_url="sqlite:///:memory:",
            whatsapp_access_token="wa_tok",
            whatsapp_phone_number_id="pid1",
        )
    )
    forwarder(
        ForwardItem(
            event_id="e1",
            reply_to={"platform": "whatsapp", "chat_id": "15551234567", "message_id": "wamid.abc"},
            body="hi there",
            status="done",
        )
    )
    assert len(sent) == 1
    token, phone_id, to, text, kw = sent[0]
    assert (token, phone_id, to, text) == ("wa_tok", "pid1", "15551234567", "hi there")
    assert kw["reply_to_message_id"] == "wamid.abc"


def test_forwarder_table_trims_an_over_length_whatsapp_body(monkeypatch):
    """#the-wire-that-cuts-at-4096: a fully hosted resident has no
    self-hosted daemon in front of it trimming the body to
    ``gates/cloud.py``'s ``_RESPONSE_LIMITS`` — this forwarder is the only
    thing standing between an over-4096-char reply and a raw Meta API
    rejection. It must trim at a boundary (never mid-word) and land the
    whole thing, marker included, at or under WhatsApp's own cap.
    """
    from brnrd.inbox import ForwardItem, make_default_forwarder
    from brnrd.platforms import whatsapp

    sent = []
    monkeypatch.setattr(
        "brnrd.platforms.whatsapp.send_message",
        lambda token, phone_id, to, text, **kw: sent.append(text),
    )
    forwarder = make_default_forwarder(
        Settings(
            database_url="sqlite:///:memory:",
            whatsapp_access_token="wa_tok",
            whatsapp_phone_number_id="pid1",
        )
    )
    body = ("word " * 1000).strip()  # well past 4096, plenty of word boundaries
    forwarder(
        ForwardItem(
            event_id="e1",
            reply_to={"platform": "whatsapp", "chat_id": "155"},
            body=body,
            status="done",
        )
    )

    assert len(sent) == 1
    delivered = sent[0]
    assert len(delivered) <= whatsapp.MAX_BODY_LEN
    assert delivered.endswith("[truncated]")
    trimmed_body = delivered[: -len("\n\n[truncated]")]
    assert trimmed_body.endswith("word")  # cut after a whole word, never mid-word


def test_forwarder_table_leaves_a_body_within_the_whatsapp_limit_untouched(monkeypatch):
    from brnrd.inbox import ForwardItem, make_default_forwarder

    sent = []
    monkeypatch.setattr(
        "brnrd.platforms.whatsapp.send_message",
        lambda token, phone_id, to, text, **kw: sent.append(text),
    )
    forwarder = make_default_forwarder(
        Settings(
            database_url="sqlite:///:memory:",
            whatsapp_access_token="wa_tok",
            whatsapp_phone_number_id="pid1",
        )
    )
    forwarder(
        ForwardItem(
            event_id="e1",
            reply_to={"platform": "whatsapp", "chat_id": "155"},
            body="well within budget",
            status="done",
        )
    )

    assert sent == ["well within budget"]


def test_forwarder_table_skips_whatsapp_without_credentials():
    from brnrd.inbox import ForwardItem, make_default_forwarder

    forwarder = make_default_forwarder(Settings(database_url="sqlite:///:memory:"))
    forwarder(
        ForwardItem(
            event_id="e1",
            reply_to={"platform": "whatsapp", "chat_id": "155"},
            body="hi",
            status="done",
        )
    )  # no exception, no credentials configured


def test_forwarder_table_ignores_unknown_platform():
    from brnrd.inbox import ForwardItem, make_default_forwarder

    forwarder = make_default_forwarder(Settings(database_url="sqlite:///:memory:"))
    forwarder(
        ForwardItem(event_id="e1", reply_to={"platform": "slack"}, body="hi", status="done")
    )  # silently does nothing, same as the old if/elif falling through


def test_inbox_poll_is_paged_and_the_cursor_drains_the_rest():
    """One poll can never hand over an unbounded backlog again.

    The measurement behind the page (2026-08-14): an account home created
    fresh on a new machine had no `account/gates/cloud.json`, so the daemon
    polled `since = 0` and the server answered with **every** queued event
    across the account — 1,226 of them in a single response body, ingested
    at ~10/s while the wake that had to handle them was still being built.
    The free-tier burst ceiling (`limits.py`, 6 events/min) did not help:
    it governs *enqueue*, and a replay is all delivery.

    The cursor is what drains the remainder, so the page costs only round
    trips: the backlog still arrives in full, one bounded chunk at a time,
    and a daemon that dies mid-drain resumes at its last cursor instead of
    starting over.

    Five events against a page of two — deliberately under that same 6/min
    enqueue ceiling, which this test tripped on its first draft and which is
    exactly the kind of silent 429 a fixture hides.
    """
    forwarder = CapturingForwarder()
    settings = Settings(
        database_url="sqlite:///:memory:",
        inbox_long_poll_max_s=1.0,
        inbox_poll_interval_s=0.02,
        inbox_page_limit=2,
    )
    client = TestClient(create_app(settings, forwarder=forwarder))
    acc = _account(client)
    rid = _repo(client, acc)
    dmn = _connect(client, acc, rid)
    for i in range(5):
        enq = client.post(
            "/v1/_dev/enqueue",
            json={"repo_id": rid, "body": f"m{i}"},
            headers=acc,
        )
        assert enq.status_code == 201, enq.text

    seen: list[str] = []
    pages = 0
    cursor = 0
    for _ in range(6):  # a failure bound, not a schedule
        page = client.get(
            "/v1/daemons/inbox",
            params={"since": cursor, "wait": 0},
            headers=dmn,
        ).json()
        assert len(page["events"]) <= 2, "a page must never exceed the limit"
        if not page["events"]:
            break
        pages += 1
        seen.extend(e["body"] for e in page["events"])
        assert page["cursor"] > cursor, "the cursor must advance per page"
        cursor = page["cursor"]

    # Bounded per response, complete in aggregate, in order — and it really
    # took more than one response, or the bound proved nothing.
    assert seen == [f"m{i}" for i in range(5)]
    assert pages == 3


def test_inbox_page_limit_off_returns_the_whole_backlog():
    """The bound is opt-in at the query layer; absent, behaviour is unchanged.

    Guards the seam rather than the setting: `fetch_since_many` must be a
    no-op wrapper when no limit is passed, so every other caller (and any
    older deployment reading a config that predates `inbox_page_limit`)
    keeps the semantics it had.
    """
    forwarder = CapturingForwarder()
    settings = Settings(database_url="sqlite:///:memory:")
    app = create_app(settings, forwarder=forwarder)
    client = TestClient(app)
    acc = _account(client)
    rid = _repo(client, acc)
    for i in range(5):
        client.post(
            "/v1/_dev/enqueue",
            json={"repo_id": rid, "body": f"m{i}"},
            headers=acc,
        )
    with app.state.SessionLocal() as db:
        assert len(inbox_service.fetch_since_many(db, {rid}, 0)) == 5
        assert len(inbox_service.fetch_since_many(db, {rid}, 0, limit=2)) == 2
        # A non-positive limit means "no bound", never "no rows".
        assert len(inbox_service.fetch_since_many(db, {rid}, 0, limit=0)) == 5


def test_a_noted_close_retires_the_event_without_forwarding_anything():
    """`note:` must close the server row — silently, but really.

    A noted event used to stay `queued` forever: the resident's deliberate
    retire was a local file edit and nothing else, so the only exit from
    the queued set was a terminal `done`. That made the queued set grow
    without bound, and the queued set is the one structural defence
    against a replay — 554 of the 1,203 events replayed on 2026-08-14 were
    letters already read and deliberately closed on another machine.

    Two properties, and the second is why this is not just a `done` with an
    empty body: the row closes, and **the platform hears nothing**.
    """
    forwarder = CapturingForwarder()
    settings = Settings(database_url="sqlite:///:memory:")
    app = create_app(settings, forwarder=forwarder)
    client = TestClient(app)
    acc = _account(client)
    rid = _repo(client, acc)
    dmn = _connect(client, acc, rid)
    enq = client.post(
        "/v1/_dev/enqueue", json={"repo_id": rid, "body": "noise"}, headers=acc
    ).json()
    event_id = enq["event_id"]

    before = len(forwarder.items)
    ack = client.post(
        "/v1/daemons/responses",
        json={"event_id": event_id, "body_markdown": "", "status": "noted"},
        headers=dmn,
    )
    assert ack.status_code == 200, ack.text
    assert len(forwarder.items) == before, "a note must not reach the platform"

    # The row is closed, so it can never redeliver — cursor or no cursor.
    drained = client.get(
        "/v1/daemons/inbox", params={"since": 0, "wait": 0}, headers=dmn
    ).json()
    assert drained["events"] == []

    with app.state.SessionLocal() as db:
        row = db.execute(
            select(Event).where(Event.event_id == event_id)
        ).scalar_one()
        assert row.status == Event.STATUS_RESPONDED
        assert row.response_status == "noted"
        assert row.body is None
        assert row.attachments_json == "[]"

    # Idempotent: the daemon retries this sweep until it sees a 2xx.
    again = client.post(
        "/v1/daemons/responses",
        json={"event_id": event_id, "body_markdown": "", "status": "noted"},
        headers=dmn,
    )
    assert again.status_code == 200, again.text
    assert len(forwarder.items) == before


@pytest.mark.parametrize(
    "platform", ["telegram", "github", "whatsapp"],
)
def test_every_forward_handler_tolerates_an_incomplete_reply_to(platform):
    """A routable platform with nothing to route to is not an exception.

    `forward_github` and `forward_whatsapp` both `.get` their addressing
    fields and return early when they are missing. `forward_telegram` used
    a bare `reply_to["chat_id"]`, so the same shape raised KeyError from
    inside the forwarder — and `record_response` turns anything the
    forwarder raises into a `DeliveryError`, which the daemon retries
    forever at the poll cadence. Observed live 2026-08-15: four events
    retried 36 times against a non-2xx and never stopped.

    Parametrized over the platform names the routing table actually
    registers rather than over a list written by hand — a fourth handler
    added without the guard should fail here, not in production. If this
    list and `make_default_forwarder`'s table drift apart, that drift is
    the bug this test exists to catch.
    """
    settings = Settings(
        telegram_bot_token="t",
        whatsapp_access_token="w",
        whatsapp_phone_number_id="1",
    )
    forward = inbox_service.make_default_forwarder(settings)
    item = inbox_service.ForwardItem(
        event_id="ev_x",
        reply_to={"platform": platform},  # routable, and addressed to nothing
        body="hello",
        status="done",
    )
    forward(item)  # must return quietly, never raise


def test_the_handler_list_above_matches_the_routing_table():
    """Guard the guard: the parametrize list must not drift from the table.

    Enumerating a class by hand is how a test goes green on the very member
    it was written to cover. Ask the module what it registers.
    """
    import inspect

    source = inspect.getsource(inbox_service.make_default_forwarder)
    table = source.split("handlers: dict[str, Callable[[ForwardItem, dict], None]] = {", 1)[1]
    table = table.split("}", 1)[0]
    registered = {
        line.split('"')[1]
        for line in table.splitlines()
        if line.strip().startswith('"')
    }
    assert registered == {"telegram", "github", "whatsapp"}, (
        "a platform was added to the routing table without extending "
        "test_every_forward_handler_tolerates_an_incomplete_reply_to"
    )


# ── #1205 — POST /v1/daemons/messages, the fresh-send primitive ──────────
#
# Unlike `/v1/daemons/responses` above, this endpoint carries no event_id:
# resolution is keyed on platform chat identity, borrowed from whichever of
# the account's own events was most recently active on that platform.


def test_fresh_send_reaches_the_most_recently_active_telegram_conversation(
    env, monkeypatch,
):
    app, client, forwarder = env
    acc = _account(client)
    rid = _repo(client, acc)
    dmn = _connect(client, acc, rid)

    # Oldest first — the resolver must prefer the newest, not the first.
    older = client.post(
        "/v1/_dev/enqueue",
        json={
            "repo_id": rid, "body": "first",
            "reply_to": {"platform": "telegram", "chat_id": 111},
        },
        headers=acc,
    )
    assert older.status_code == 201, older.text
    newer = client.post(
        "/v1/_dev/enqueue",
        json={
            "repo_id": rid, "body": "second",
            "reply_to": {"platform": "telegram", "chat_id": 222, "topic_id": 5},
        },
        headers=acc,
    )
    assert newer.status_code == 201, newer.text

    sent = []
    monkeypatch.setitem(
        daemon_routes._MESSAGE_SENDERS,
        "telegram",
        lambda settings, reply_to, body: sent.append((reply_to, body)) or "999",
    )

    resp = client.post(
        "/v1/daemons/messages",
        json={"body_markdown": "unaddressed hello", "platform": "telegram"},
        headers=dmn,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"platform": "telegram", "message_id": "999"}
    [(reply_to, body)] = sent
    assert reply_to["chat_id"] == 222
    assert reply_to.get("topic_id") == 5
    assert body == "unaddressed hello"


def test_fresh_send_no_resolvable_conversation_is_honest_404_never_a_silent_200(env):
    app, client, forwarder = env
    acc = _account(client)
    rid = _repo(client, acc)
    dmn = _connect(client, acc, rid)

    # A github-addressed event exists, but nothing telegram-shaped does.
    enq = client.post(
        "/v1/_dev/enqueue",
        json={"repo_id": rid, "body": "issue chatter", "reply_to": {"platform": "github"}},
        headers=acc,
    )
    assert enq.status_code == 201, enq.text

    resp = client.post(
        "/v1/daemons/messages",
        json={"body_markdown": "hello?", "platform": "telegram"},
        headers=dmn,
    )
    assert resp.status_code == 404, resp.text


def test_fresh_send_unimplemented_platform_is_honest_501(env):
    app, client, forwarder = env
    acc = _account(client)
    rid = _repo(client, acc)
    dmn = _connect(client, acc, rid)

    resp = client.post(
        "/v1/daemons/messages",
        json={"body_markdown": "hello?", "platform": "signal"},
        headers=dmn,
    )
    assert resp.status_code == 501, resp.text


def test_fresh_send_never_resolves_a_different_accounts_conversation(env):
    app, client, forwarder = env
    other_acc = _account(client, email="other@b.com")
    other_rid = _repo(client, other_acc, name="other-repo")
    client.post(
        "/v1/_dev/enqueue",
        json={
            "repo_id": other_rid, "body": "not yours",
            "reply_to": {"platform": "telegram", "chat_id": 999},
        },
        headers=other_acc,
    )

    acc = _account(client, email="mine@b.com")
    rid = _repo(client, acc, name="mine")
    dmn = _connect(client, acc, rid)

    resp = client.post(
        "/v1/daemons/messages",
        json={"body_markdown": "hello?", "platform": "telegram"},
        headers=dmn,
    )
    # This account has no telegram history of its own — the other account's
    # conversation must never leak across the boundary as a fallback.
    assert resp.status_code == 404, resp.text


def test_fresh_send_registry_matches_what_this_test_module_covers():
    """Guard the guard, same shape as the forwarder-table test above."""
    import inspect

    source = inspect.getsource(daemon_routes)
    table = source.split(
        "_MESSAGE_SENDERS: dict[str, Callable[[Any, dict[str, Any], str], str]] = {", 1,
    )[1]
    table = table.split("}", 1)[0]
    registered = {
        line.split('"')[1] for line in table.splitlines() if line.strip().startswith('"')
    }
    assert registered == {"telegram"}, (
        "a platform was added to the fresh-send registry without extending "
        "the coverage above"
    )


def test_telegram_fresh_send_without_a_bot_token_is_honest_503(monkeypatch):
    class _Settings:
        telegram_bot_token = None

    with pytest.raises(Exception) as exc_info:
        daemon_routes._telegram_fresh_send(
            _Settings(), {"chat_id": 1}, "hello",
        )
    assert getattr(exc_info.value, "status_code", None) == 503

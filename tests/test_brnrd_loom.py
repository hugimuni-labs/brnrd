"""Loom on brnrd.dev: snapshot v1 mirror and the board's composer."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from brnrd import create_app  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.inbox import CapturingForwarder  # noqa: E402
from brnrd.models import Daemon  # noqa: E402
from _helpers import approve_pair, brnrd_account_headers, PUBLISH_EVERYTHING  # noqa: E402

SNAPSHOT = {
    "v": 1,
    "at": "2026-10-19T00:20:00Z",
    "presence": {"strand": "s-ae66-6vf7ze", "thread": "inbox", "state": "working"},
    "threads": [{"id": "answer-asks-build", "turn": "yours", "plan": {"done": 6, "total": 7}}],
    "log": [],
}


@pytest.fixture()
def client(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'brnrd-test.db'}",
        inbox_long_poll_max_s=0.5,
        inbox_poll_interval_s=0.02,
    )
    return TestClient(create_app(settings, forwarder=CapturingForwarder()))


def _account(client, login="arseni"):
    headers = brnrd_account_headers(client.app, login=login, email=f"{login}@example.com")
    return headers, headers["Authorization"].split(" ", 1)[1]


def _repo(client, headers, name="demo", publish_layers=PUBLISH_EVERYTHING):
    r = client.post(
        "/v1/accounts/repos",
        json={"repo_full_name": f"Gurio/{name}", "publish_layers": publish_layers},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["repo_id"]


def _relay_token(client, headers, repo_id):
    """A paired daemon token that never calls /register — the loom's shape."""
    pair = client.post("/v1/accounts/pair").json()
    approve = approve_pair(client, pair['pair_code'], {"repo_id": repo_id, "approve_secret": pair["approve_secret"]}, headers=headers)
    assert approve.status_code == 200, approve.text
    paired = client.get(f"/v1/accounts/pair/{pair['pair_code']}", params={"poll_secret": pair["poll_secret"]}).json()
    return {"Authorization": f"Bearer {paired['daemon_token']}"}


def _signed_in(client, session_token):
    client.cookies.clear()
    if session_token:
        client.cookies.set("brnrd_session", session_token)


def test_put_creates_loom_daemon_and_dashboard_reads_it(client):
    headers, session = _account(client)
    relay = _relay_token(client, headers, _repo(client, headers))
    _signed_in(client, session)
    assert client.get("/v1/dashboard/loom").status_code == 404

    r = client.put("/v1/daemons/loom", json=SNAPSHOT, headers=relay)
    assert r.status_code == 200, r.text
    assert r.json()["loom_updated_at"]
    with client.app.state.SessionLocal() as db:
        daemon = db.execute(select(Daemon)).scalar_one()
        assert daemon.daemon_name == "loom" and daemon.online

    got = client.get("/v1/dashboard/loom")
    assert got.status_code == 200, got.text
    body = got.json()
    stale = body.pop("stale_s")
    assert isinstance(stale, int) and 0 <= stale < 5
    assert body == SNAPSHOT

    # A second push reuses the row rather than minting another daemon.
    newer = {**SNAPSHOT, "at": "2026-10-19T00:21:00Z"}
    assert client.put("/v1/daemons/loom", json=newer, headers=relay).status_code == 200
    with client.app.state.SessionLocal() as db:
        assert len(db.execute(select(Daemon)).scalars().all()) == 1
    assert client.get("/v1/dashboard/loom").json()["at"] == newer["at"]


def test_dashboard_loom_signed_out_is_401(client):
    _signed_in(client, None)
    assert client.get("/v1/dashboard/loom").status_code == 401
    assert client.post("/v1/dashboard/loom/letters", json={"thread": "inbox", "text": "hi"}).status_code == 401


@pytest.mark.parametrize("doc", [[1, 2], "nope", {"at": "x"}, {"v": 2}, {"v": "1"}])
def test_put_rejects_bad_doc(client, doc):
    headers, _ = _account(client)
    relay = _relay_token(client, headers, _repo(client, headers))
    assert client.put("/v1/daemons/loom", json=doc, headers=relay).status_code == 422


def test_put_rejects_oversized_doc(client):
    headers, _ = _account(client)
    relay = _relay_token(client, headers, _repo(client, headers))
    doc = {"v": 1, "log": ["x" * 1_100_000]}
    assert client.put("/v1/daemons/loom", json=doc, headers=relay).status_code == 413


def test_put_respects_activity_consent(client):
    headers, session = _account(client)
    relay = _relay_token(client, headers, _repo(client, headers, publish_layers="quota"))
    assert client.put("/v1/daemons/loom", json=SNAPSHOT, headers=relay).status_code == 451
    _signed_in(client, session)
    assert client.get("/v1/dashboard/loom").status_code == 404


def test_withdrawing_activity_consent_purges_snapshot(client):
    from brnrd import publish_scope
    from brnrd.models import Repo

    headers, session = _account(client)
    repo_id = _repo(client, headers)
    relay = _relay_token(client, headers, repo_id)
    assert client.put("/v1/daemons/loom", json=SNAPSHOT, headers=relay).status_code == 200
    with client.app.state.SessionLocal() as db:
        repo = db.get(Repo, repo_id)
        publish_scope.purge_removed_scope(db, repo=repo, new_publish_layers="quota")
        repo.publish_layers = "quota"
        db.commit()
    _signed_in(client, session)
    assert client.get("/v1/dashboard/loom").status_code == 404


def test_other_account_cannot_see_snapshot(client):
    headers, _ = _account(client)
    relay = _relay_token(client, headers, _repo(client, headers))
    assert client.put("/v1/daemons/loom", json=SNAPSHOT, headers=relay).status_code == 200
    _, other_session = _account(client, login="mallory")
    _signed_in(client, other_session)
    assert client.get("/v1/dashboard/loom").status_code == 404


def test_composer_letter_reaches_relay_inbox(client):
    headers, session = _account(client)
    relay = _relay_token(client, headers, _repo(client, headers))
    _signed_in(client, session)
    r = client.post(
        "/v1/dashboard/loom/letters",
        json={"thread": "answer-asks-build", "text": "ship it", "body": "claude/opus"},
    )
    assert r.status_code == 202, r.text
    assert r.json()["ok"] is True
    event_id = r.json()["event_id"]
    plain = client.post("/v1/dashboard/loom/letters", json={"thread": "inbox", "text": "hello"})
    assert plain.status_code == 202, plain.text

    events = client.get("/v1/daemons/inbox", params={"wait": 0}, headers=relay).json()["events"]
    assert [e["event_id"] for e in events] == [event_id, plain.json()["event_id"]]
    first, second = events
    assert first["source"] == "web" and first["body"] == "ship it"
    reply_to = first["reply_to"]
    assert reply_to["message_id"]
    assert reply_to == {
        "platform": "loom",
        "thread": "answer-asks-build",
        "message_id": reply_to["message_id"],
        "user_id": "arseni",
        "body": "claude/opus",
    }
    assert not {"body", "wish", "verdict"} & set(second["reply_to"])
    assert second["reply_to"]["thread"] == "inbox"


@pytest.mark.parametrize("verdict", ["agree", "correct"])
def test_composer_verdict_names_its_wish(client, verdict):
    headers, session = _account(client)
    relay = _relay_token(client, headers, _repo(client, headers))
    _signed_in(client, session)
    r = client.post("/v1/dashboard/loom/letters",
                    json={"thread": "web", "text": "yes", "wish": "w-0a9zq3k7m2xp", "verdict": verdict})
    assert r.status_code == 202, r.text
    event, = client.get("/v1/daemons/inbox", params={"wait": 0}, headers=relay).json()["events"]
    assert (event["reply_to"]["wish"], event["reply_to"]["verdict"]) == ("w-0a9zq3k7m2xp", verdict)


def test_composer_prefers_loom_daemon_repo(client):
    headers, session = _account(client)
    _repo(client, headers, name="older")
    loom_repo = _repo(client, headers, name="loomed")
    relay = _relay_token(client, headers, loom_repo)
    assert client.put("/v1/daemons/loom", json=SNAPSHOT, headers=relay).status_code == 200
    _signed_in(client, session)
    assert client.post("/v1/dashboard/loom/letters", json={"thread": "inbox", "text": "hi"}).status_code == 202
    events = client.get("/v1/daemons/inbox", params={"wait": 0}, headers=relay).json()["events"]
    assert events[0]["repo_label"] == "Gurio/loomed"


def test_composer_without_repo_is_409(client):
    _, session = _account(client)
    _signed_in(client, session)
    r = client.post("/v1/dashboard/loom/letters", json={"thread": "inbox", "text": "hi"})
    assert r.status_code == 409


@pytest.mark.parametrize(
    "payload",
    [
        {"thread": "Inbox", "text": "hi"},
        {"thread": "-inbox", "text": "hi"},
        {"thread": "a" * 65, "text": "hi"},
        {"thread": "inbox", "text": ""},
        {"thread": "inbox", "text": "   "},
        {"thread": "inbox", "text": "x" * 20_001},
        {"thread": "inbox", "text": "hi", "body": "opus"},
        {"thread": "inbox", "text": "hi", "body": "Claude/opus"},
        {"text": "hi"},
        {"thread": "inbox"},
        {"thread": "inbox", "text": "yes", "wish": "w-abc123"},
        {"thread": "inbox", "text": "yes", "verdict": "agree"},
        {"thread": "inbox", "text": "yes", "wish": "abc123", "verdict": "agree"},
        {"thread": "inbox", "text": "yes", "wish": "w-" + "a" * 33, "verdict": "agree"},
        {"thread": "inbox", "text": "yes", "wish": "w-abc123", "verdict": "accept"},
    ],
)
def test_composer_validation(client, payload):
    headers, session = _account(client)
    _repo(client, headers)
    _signed_in(client, session)
    assert client.post("/v1/dashboard/loom/letters", json=payload).status_code == 422

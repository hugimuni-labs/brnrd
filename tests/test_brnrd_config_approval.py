"""Tests for loom-envelope Phase 2 — the config-change approve/confirm URL.

Mirrors ``tests/test_brnrd_web.py``'s pairing device-flow tests, and
``tests/test_brnrd_quota.py``'s daemon-auth setup shape, for the new
``ConfigChangeRequest`` flow: daemon mints a request
(``POST /v1/daemons/config-requests``), the account owner decides from a
session-cookie-gated browser page (``/config-approve/{id}``), and the
outcome lands as a plain inbox ``Event`` the daemon's existing
``/v1/daemons/inbox`` long-poll already delivers — see
``src/brr/daemon.py::_config_change_reply`` for the consuming side.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from brnrd import create_app  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.models import ConfigChangeRequest, Event  # noqa: E402
from _helpers import brnrd_account_headers  # noqa: E402


def _client() -> TestClient:
    app = create_app(
        Settings(
            database_url="sqlite:///:memory:",
            public_base_url="https://brnrd.example",
            github_oauth_client_id="gh-client",
            github_oauth_client_secret="gh-secret",
        )
    )
    return TestClient(app, base_url="https://testserver")


def _repo_and_daemon(client: TestClient) -> tuple[str, dict[str, str], dict[str, str], str]:
    account_headers = brnrd_account_headers(client.app, github_id="123", login="octocat", email="a@b.com")
    session_token = account_headers["Authorization"].split(" ", 1)[1]
    repo = client.post(
        "/v1/accounts/repos",
        json={"repo_full_name": "Gurio/brr", "default_branch": "main"},
        headers=account_headers,
    ).json()
    pair = client.post("/v1/accounts/pair").json()
    client.post(
        f"/v1/accounts/pair/{pair['pair_code']}/approve",
        json={"repo_id": repo["repo_id"]},
        headers=account_headers,
    )
    paired = client.get(
        f"/v1/accounts/pair/{pair['pair_code']}",
        params={"poll_secret": pair["poll_secret"]},
    ).json()
    daemon_headers = {"Authorization": f"Bearer {paired['daemon_token']}"}
    return session_token, account_headers, daemon_headers, repo["repo_id"]


def _mint(client: TestClient, daemon_headers: dict[str, str], **overrides) -> dict:
    payload = {
        "proposal_id": "cfgchg-260708-000000-abcd1234",
        "config_key": "spawn.max_concurrent",
        "current_value": "4",
        "requested_value": "8",
        "reason": "need headroom for a four-way fan-out",
    }
    payload.update(overrides)
    return client.post("/v1/daemons/config-requests", json=payload, headers=daemon_headers).json()


def test_daemon_can_mint_config_change_request():
    client = _client()
    _session, _account_headers, daemon_headers, repo_id = _repo_and_daemon(client)

    resp = client.post(
        "/v1/daemons/config-requests",
        json={
            "proposal_id": "cfgchg-260708-000000-abcd1234",
            "config_key": "spawn.max_concurrent",
            "current_value": "4",
            "requested_value": "8",
            "reason": "need headroom",
        },
        headers=daemon_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert body["approve_url"] == f"https://brnrd.example/config-approve/{body['request_id']}"

    with client.app.state.SessionLocal() as db:
        row = db.get(ConfigChangeRequest, body["request_id"])
        assert row is not None
        assert row.repo_id == repo_id
        assert row.config_key == "spawn.max_concurrent"
        assert row.status == ConfigChangeRequest.STATUS_PENDING


def test_daemon_cannot_mint_off_allowlist_key():
    client = _client()
    _session, _account_headers, daemon_headers, _repo_id = _repo_and_daemon(client)

    resp = client.post(
        "/v1/daemons/config-requests",
        json={
            "proposal_id": "cfgchg-x",
            "config_key": "pacing.quota_low_floor_pct",
            "current_value": "5",
            "requested_value": "0",
        },
        headers=daemon_headers,
    )
    assert resp.status_code == 422


def test_config_approve_page_requires_login():
    client = _client()
    _session, _account_headers, daemon_headers, _repo_id = _repo_and_daemon(client)
    minted = _mint(client, daemon_headers)

    r = client.get(f"/config-approve/{minted['request_id']}", follow_redirects=False)

    assert r.status_code == 303
    assert "/login" in r.headers["location"]


def test_config_approve_page_shows_details():
    client = _client()
    session_token, _account_headers, daemon_headers, _repo_id = _repo_and_daemon(client)
    minted = _mint(client, daemon_headers)
    client.cookies.set("brnrd_session", session_token)

    r = client.get(f"/config-approve/{minted['request_id']}")

    assert r.status_code == 200
    assert "spawn.max_concurrent" in r.text
    assert "need headroom for a four-way fan-out" in r.text


def test_config_approve_submit_approve_enqueues_inbox_event():
    client = _client()
    session_token, _account_headers, daemon_headers, repo_id = _repo_and_daemon(client)
    minted = _mint(client, daemon_headers)
    client.cookies.set("brnrd_session", session_token)

    r = client.post(
        f"/config-approve/{minted['request_id']}",
        data={"decision": "approve"},
    )

    assert r.status_code == 200
    assert "Approved" in r.text

    with client.app.state.SessionLocal() as db:
        row = db.get(ConfigChangeRequest, minted["request_id"])
        assert row.status == ConfigChangeRequest.STATUS_APPROVED
        events = list(db.execute(select(Event).where(Event.repo_id == repo_id)).scalars())
        assert len(events) == 1
        assert events[0].body == f"approve config-change {row.proposal_id}"
        assert events[0].source == "cloud"

    # A second decision on an already-decided request is a no-op, not a
    # second inbox event (the daemon side only ever expects one outcome
    # per proposal id).
    again = client.post(f"/config-approve/{minted['request_id']}", data={"decision": "reject"})
    assert again.status_code == 200
    with client.app.state.SessionLocal() as db:
        events = list(db.execute(select(Event).where(Event.repo_id == repo_id)).scalars())
        assert len(events) == 1


def test_config_approve_submit_reject_enqueues_reject_event():
    client = _client()
    session_token, _account_headers, daemon_headers, repo_id = _repo_and_daemon(client)
    minted = _mint(client, daemon_headers)
    client.cookies.set("brnrd_session", session_token)

    r = client.post(
        f"/config-approve/{minted['request_id']}",
        data={"decision": "reject"},
    )

    assert r.status_code == 200
    assert "Rejected" in r.text
    with client.app.state.SessionLocal() as db:
        events = list(db.execute(select(Event).where(Event.repo_id == repo_id)).scalars())
        assert len(events) == 1
        assert events[0].body.startswith("reject config-change ")


def test_config_approve_rejects_other_accounts_request():
    client = _client()
    _session, _account_headers, daemon_headers, _repo_id = _repo_and_daemon(client)
    minted = _mint(client, daemon_headers)

    other_headers = brnrd_account_headers(client.app, github_id="999", login="intruder", email="i@b.com")
    other_session = other_headers["Authorization"].split(" ", 1)[1]
    client.cookies.set("brnrd_session", other_session)

    r = client.get(f"/config-approve/{minted['request_id']}")
    assert r.status_code == 404

    submit = client.post(f"/config-approve/{minted['request_id']}", data={"decision": "approve"})
    assert submit.status_code == 403

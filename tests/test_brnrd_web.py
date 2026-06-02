"""Tests for the brnrd_web dashboard (login + device-flow approve page)."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402

from brnrd import create_app  # noqa: E402
from brnrd.config import Settings  # noqa: E402

_EMAIL = "owner@example.com"
_PASSWORD = "hunter2hunter2"


@pytest.fixture()
def client():
    app = create_app(Settings(database_url="sqlite:///:memory:"))
    return TestClient(app)


def _account_and_project(client):
    client.post("/v1/accounts", json={"email": _EMAIL, "password": _PASSWORD})
    # Log in via the API to get a key for project creation.
    key = client.post(
        "/v1/accounts/sessions", json={"email": _EMAIL, "password": _PASSWORD}
    ).json()["session_token"]
    headers = {"Authorization": f"Bearer {key}"}
    project_id = client.post(
        "/v1/accounts/projects", json={"name": "laptop"}, headers=headers
    ).json()["project_id"]
    return project_id


def _login_web(client):
    return client.post(
        "/login",
        data={"email": _EMAIL, "password": _PASSWORD, "next": "/"},
        follow_redirects=False,
    )


def test_login_sets_session_cookie(client):
    _account_and_project(client)
    r = _login_web(client)
    assert r.status_code == 303
    assert "brnrd_session" in r.cookies or "brnrd_session" in client.cookies


def test_bad_login_is_rejected(client):
    _account_and_project(client)
    r = client.post(
        "/login",
        data={"email": _EMAIL, "password": "wrong", "next": "/"},
        follow_redirects=False,
    )
    assert r.status_code == 401


def test_connect_page_requires_login(client):
    _account_and_project(client)
    pair = client.post("/v1/accounts/pair").json()
    r = client.get(f"/connect/{pair['pair_code']}", follow_redirects=False)
    assert r.status_code == 303
    assert "/login" in r.headers["location"]


def test_connect_page_lists_projects(client):
    _account_and_project(client)
    _login_web(client)
    pair = client.post("/v1/accounts/pair").json()
    r = client.get(f"/connect/{pair['pair_code']}")
    assert r.status_code == 200
    assert "laptop" in r.text
    assert pair["pair_code"] in r.text


def test_approve_makes_poll_return_token(client):
    project_id = _account_and_project(client)
    _login_web(client)
    pair = client.post("/v1/accounts/pair").json()

    approve = client.post(
        f"/connect/{pair['pair_code']}",
        data={"project_id": project_id},
        follow_redirects=False,
    )
    assert approve.status_code == 200
    assert "Approved" in approve.text

    # The CLI's poll now returns the freshly minted daemon token.
    polled = client.get(
        f"/v1/accounts/pair/{pair['pair_code']}",
        params={"poll_secret": pair["poll_secret"]},
    ).json()
    assert polled["status"] == "paired"
    assert polled["daemon_token"]
    assert polled["project_id"] == project_id

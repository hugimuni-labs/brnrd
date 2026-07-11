"""Tests for the runner-catalog mirror (#328 spool rack): daemon publish
endpoint + dashboard JSON twin. Mirrors ``tests/test_brnrd_quota.py``'s shape.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402

from brnrd import create_app  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.oauth import GitHubIdentity  # noqa: E402
from brnrd.routers.accounts import account_for_github_identity, issue_session_token  # noqa: E402
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


def _repo_and_daemon(client: TestClient) -> tuple[dict[str, str], dict[str, str], str]:
    account_headers = brnrd_account_headers(
        client.app, github_id="123", login="octocat", email="a@b.com",
    )
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
    return account_headers, daemon_headers, repo["repo_id"]


def _login_cookie(client: TestClient) -> None:
    with client.app.state.SessionLocal() as db:
        account = account_for_github_identity(
            db, GitHubIdentity(github_id="123", login="octocat", email="a@b.com")
        )
        token = issue_session_token(db, account)
    client.cookies.set("brnrd_session", token)


_CATALOG_PAYLOAD = {
    "default": "claude-fable",
    "profiles": [
        {
            "name": "claude-haiku",
            "shell": "claude",
            "model": "claude-haiku-4-5-20251001",
            "class": "economy",
            "cost_rank": 10,
            "quota_source": "claude-local",
        },
        {
            "name": "claude-fable",
            "shell": "claude",
            "model": "claude-fable-5",
            "class": "economy",
            "cost_rank": 15,
            "quota_source": "claude-local",
            "selected": True,
        },
        {
            "name": "codex",
            "shell": "codex",
            "class": "balanced",
            "cost_rank": 25,
            "quota_source": "codex-local",
        },
    ],
}


def test_daemon_runners_snapshot_replaces_catalog():
    client = _client()
    _, daemon_headers, _repo_id = _repo_and_daemon(client)
    assert client.post(
        "/v1/daemons/register", json={"daemon_name": "laptop"}, headers=daemon_headers,
    ).status_code == 200

    posted = client.put("/v1/daemons/runners", json=_CATALOG_PAYLOAD, headers=daemon_headers)
    assert posted.status_code == 200, posted.text
    body = posted.json()
    assert body["default"] == "claude-fable"
    # `class` survives the pydantic alias round-trip on the wire.
    assert body["profiles"][0]["class"] == "economy"
    assert body["runners_updated_at"] is not None

    # Last-write-wins, same shape as the quota/plans mirrors.
    replaced = client.put(
        "/v1/daemons/runners",
        json={"default": None, "profiles": [{"name": "codex", "shell": "codex"}]},
        headers=daemon_headers,
    )
    assert replaced.status_code == 200
    assert replaced.json()["default"] is None
    assert [p["name"] for p in replaced.json()["profiles"]] == ["codex"]


def test_dashboard_runners_api_serves_merged_catalog():
    client = _client()
    _, daemon_headers, _repo_id = _repo_and_daemon(client)
    assert client.post(
        "/v1/daemons/register", json={"daemon_name": "laptop"}, headers=daemon_headers,
    ).status_code == 200
    assert client.put(
        "/v1/daemons/runners", json=_CATALOG_PAYLOAD, headers=daemon_headers,
    ).status_code == 200

    # Unauthenticated fetch: JSON 401, not a redirect.
    anon = client.get("/v1/dashboard/runners")
    assert anon.status_code == 401

    _login_cookie(client)
    res = client.get("/v1/dashboard/runners")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["default"] == "claude-fable"
    assert body["stale"] is False
    assert body["reported_at"] is not None
    # Sorted cheapest-first by cost_rank.
    assert [p["name"] for p in body["profiles"]] == ["claude-haiku", "claude-fable", "codex"]
    assert body["profiles"][1]["selected"] is True

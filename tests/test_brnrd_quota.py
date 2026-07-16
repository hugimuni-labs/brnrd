"""Tests for the runner-quota mirror (#237): daemon publish endpoint +
dashboard rendering. Mirrors ``tests/test_brnrd_plans.py``'s shape for the
CPS plan/ledger mirror.
"""

from __future__ import annotations

import json

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


_SHELLS_PAYLOAD = {
    "shells": [
        {
            "shell": "claude",
            "status": "known",
            "windows": [
                {"label": "5h window", "used": None, "limit": None, "percent": 61.0, "reset": "resets 9:00PM", "resets_at": 1783360000.0},
                {"label": "weekly", "used": None, "limit": None, "percent": 48.0, "reset": None, "resets_at": None},
            ],
        }
    ]
}


def test_daemon_quota_snapshot_replaces_shells():
    client = _client()
    _, daemon_headers, _repo_id = _repo_and_daemon(client)
    assert client.post(
        "/v1/daemons/register", json={"daemon_name": "laptop"}, headers=daemon_headers,
    ).status_code == 200

    posted = client.put("/v1/daemons/quota", json=_SHELLS_PAYLOAD, headers=daemon_headers)
    assert posted.status_code == 200, posted.text
    body = posted.json()
    assert body["shells"][0]["shell"] == "claude"
    assert body["shells"][0]["windows"][0]["percent"] == 61.0
    assert body["shells"][0]["windows"][0]["resets_at"] == 1783360000.0
    assert body["quota_updated_at"] is not None

    # Republishing overwrites rather than accumulating (last-write-wins,
    # same shape as the Activity/Plans mirrors).
    replaced = client.put(
        "/v1/daemons/quota",
        json={"shells": [{"shell": "claude", "status": "known", "windows": []}]},
        headers=daemon_headers,
    )
    assert replaced.status_code == 200
    assert replaced.json()["shells"][0]["windows"] == []


def test_daemon_quota_round_trips_gate_health():
    client = _client()
    _, daemon_headers, _repo_id = _repo_and_daemon(client)
    assert client.post(
        "/v1/daemons/register", json={"daemon_name": "laptop"}, headers=daemon_headers,
    ).status_code == 200

    gates = [
        {
            "gate": "telegram",
            "last_poll_ok": "2026-07-13T12:00:00+00:00",
            "age_seconds": 12,
            "last_error": None,
            "status": "ok",
        },
        {
            "gate": "github",
            "last_poll_ok": None,
            "age_seconds": None,
            "last_error": "bad credentials",
            "status": "never",
        },
    ]
    posted = client.put(
        "/v1/daemons/quota",
        json={"shells": [], "gates": gates},
        headers=daemon_headers,
    )

    assert posted.status_code == 200, posted.text
    assert posted.json()["gates"] == gates

    from brnrd.models import Daemon

    with client.app.state.SessionLocal() as db:
        daemon = db.query(Daemon).one()
        assert json.loads(daemon.gate_health_json) == gates


def test_daemon_quota_round_trips_updated_at_and_credits():
    """2026-07-07: `updated_at` (the scrape's own age, for real staleness
    detection) and `credits` (real per-run USD once metered overage kicks
    in) are declared schema fields, not free-form extras `extra="ignore"`
    would silently drop."""
    client = _client()
    _, daemon_headers, _repo_id = _repo_and_daemon(client)
    assert client.post(
        "/v1/daemons/register", json={"daemon_name": "laptop"}, headers=daemon_headers,
    ).status_code == 200

    payload = {
        "shells": [
            {
                "shell": "claude",
                "status": "known",
                "updated_at": "2026-07-07T20:17:03Z",
                "windows": [
                    {"label": "5h window", "used": None, "limit": None, "percent": 1.0},
                ],
                "credits": {
                    "total_cost_usd": 1.15,
                    "summary": "usage credits 79% left; \u20ac8.69 / \u20ac40.00 spent; resets Aug 1 (Europe/Berlin)",
                    "updated_at": "2026-07-07T20:20:00Z",
                    "enabled": True,
                    "remaining_percentage": 79.0,
                    "spent_amount": 8.69,
                    "limit_amount": 40.0,
                    "currency": "\u20ac",
                    "reset": "Aug 1 (Europe/Berlin)",
                },
            }
        ]
    }
    posted = client.put("/v1/daemons/quota", json=payload, headers=daemon_headers)
    assert posted.status_code == 200, posted.text
    shell = posted.json()["shells"][0]
    assert shell["updated_at"] == "2026-07-07T20:17:03Z"
    assert shell["credits"]["total_cost_usd"] == 1.15
    assert shell["credits"]["remaining_percentage"] == 79.0
    assert shell["credits"]["spent_amount"] == 8.69
    assert shell["credits"]["currency"] == "\u20ac"


def test_daemon_quota_requires_registration():
    client = _client()
    _, daemon_headers, _repo_id = _repo_and_daemon(client)
    unregistered = client.put("/v1/daemons/quota", json=_SHELLS_PAYLOAD, headers=daemon_headers)
    assert unregistered.status_code == 404


def test_dashboard_shows_real_quota_not_unknown_placeholder():
    """The GET /v1/dashboard/quota JSON endpoint surfaces real daemon-reported
    quota rather than the "unknown" placeholder. The Jinja dashboard at GET /
    was removed when brnrd_web moved into src/brnrd/routers/; coverage is
    preserved via the JSON API the SPA now reads."""
    client = _client()
    _, daemon_headers, _repo_id = _repo_and_daemon(client)
    client.post("/v1/daemons/register", json={"daemon_name": "laptop"}, headers=daemon_headers)
    client.put("/v1/daemons/quota", json=_SHELLS_PAYLOAD, headers=daemon_headers)
    _login_cookie(client)

    page = client.get("/v1/dashboard/quota")

    assert page.status_code == 200
    body = page.json()
    # Verify the daemon's real quota is surfaced (not an "unknown" placeholder).
    assert body["runner_quotas"], "quota list should be non-empty"
    assert any(q["status"] != "unknown" for q in body["runner_quotas"])

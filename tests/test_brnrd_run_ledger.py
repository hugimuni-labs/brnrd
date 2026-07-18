"""Tests for the closed-run receipt mirror (#271)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402

from brnrd import create_app  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.models import Daemon, Repo  # noqa: E402
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


def _login(client: TestClient) -> None:
    with client.app.state.SessionLocal() as db:
        account = account_for_github_identity(
            db, GitHubIdentity(github_id="123", login="octocat", email="a@b.com")
        )
        token = issue_session_token(db, account)
    client.cookies.set("brnrd_session", token)


_ROW = {
    "run_id": "run-1",
    "event_id": "evt-1",
    "started_at": "2026-07-07T19:00:00Z",
    "ended_at": "2026-07-07T19:10:00Z",
    "wall_clock_seconds": 600.0,
    "runner_shell": "codex",
    "runner_core": "gpt-5-codex",
    "core_expected": "gpt-5-codex",
    "core_mismatch": False,
    "repo_label": "Gurio/brr",
    "source_system": "telegram",
    "name": "",
    "external_refs": [{"gate": "telegram"}],
    "task_classification": "dashboard-slice",
    "parent_run_id": None,
    "is_subspawn": False,
    "tokens_input": 1200,
    "tokens_output": 340,
    "tokens_cache_read": None,
    "tokens_cache_creation": None,
    "context_window_used": 12.5,
    "weekly_pct_delta": 1.25,
    "five_hour_pct_delta": 4.5,
    "usd_subscription_attributed": 0.25,
    "usd_credits_equivalent": None,
    "estimate_vs_actual": "actual",
}


def test_run_ledger_model_and_migration_columns_exist():
    from brnrd import migrations

    assert "run_ledger_json" in Daemon.__table__.c
    assert "run_ledger_updated_at" in Daemon.__table__.c

    statements: list[str] = []

    class FakeConn:
        def execute(self, statement):
            statements.append(str(statement))

    migrations._migrate_daemons(FakeConn())

    assert any("run_ledger_json" in sql for sql in statements)
    assert any("run_ledger_updated_at" in sql for sql in statements)


def test_run_ledger_schema_accepts_partial_receipts():
    from brnrd.schemas import RunLedgerReport

    report = RunLedgerReport.model_validate({"rows": [{**_ROW, "tokens_input": None}, {}]})

    assert report.rows[0].run_id == "run-1"
    assert report.rows[0].tokens_input is None
    assert report.rows[1].run_id is None


def test_daemon_run_ledger_snapshot_replaces_rows():
    client = _client()
    _, daemon_headers, _repo_id = _repo_and_daemon(client)
    assert client.post(
        "/v1/daemons/register", json={"daemon_name": "laptop"}, headers=daemon_headers,
    ).status_code == 200

    posted = client.put("/v1/daemons/run-ledger", json={"rows": [_ROW]}, headers=daemon_headers)
    assert posted.status_code == 200, posted.text
    body = posted.json()
    assert body["rows"][0]["task_classification"] == "dashboard-slice"
    assert body["rows"][0]["tokens_cache_read"] is None
    assert body["run_ledger_updated_at"] is not None

    replaced = client.put(
        "/v1/daemons/run-ledger",
        json={"rows": [{**_ROW, "run_id": "run-2", "task_classification": None}]},
        headers=daemon_headers,
    )
    assert replaced.status_code == 200
    assert replaced.json()["rows"] == [{**_ROW, "run_id": "run-2", "task_classification": None}]


def test_daemon_run_ledger_requires_registration():
    client = _client()
    _, daemon_headers, _repo_id = _repo_and_daemon(client)
    unregistered = client.put("/v1/daemons/run-ledger", json={"rows": [_ROW]}, headers=daemon_headers)
    assert unregistered.status_code == 404


def test_dashboard_run_ledger_api_requires_login():
    client = _client()
    r = client.get("/v1/dashboard/run-ledger")
    assert r.status_code == 401


def test_dashboard_run_ledger_dedupes_limits_newest_first():
    from brnrd.routers.dashboard import _run_ledger_views

    client = _client()
    _account_headers, _daemon_headers, pid_a = _repo_and_daemon(client)
    # Same account, second repo registration for the same physical daemon.
    account_headers = brnrd_account_headers(
        client.app, github_id="123", login="octocat", email="a@b.com",
    )
    pid_b = client.post(
        "/v1/accounts/repos",
        json={"repo_full_name": "Gurio/other", "default_branch": "main"},
        headers=account_headers,
    ).json()["repo_id"]

    now = datetime.now(timezone.utc)
    older_rows = [
        {**_ROW, "run_id": "run-a", "ended_at": "2026-07-07T19:00:00Z", "task_classification": "old"},
        {**_ROW, "run_id": "run-b", "ended_at": "2026-07-07T19:20:00Z"},
    ]
    newer_rows = [
        {**_ROW, "run_id": "run-a", "ended_at": "2026-07-07T19:30:00Z", "task_classification": "new"},
        {**_ROW, "run_id": "run-c", "ended_at": "2026-07-07T18:00:00Z"},
    ]
    with client.app.state.SessionLocal() as db:
        db.add_all(
            [
                Daemon(
                    id="dmn-ledger-a", repo_id=pid_a, token_id="tok-ledger-a", daemon_name="laptop",
                    run_ledger_json=json.dumps(older_rows), run_ledger_updated_at=now - timedelta(seconds=30),
                ),
                Daemon(
                    id="dmn-ledger-b", repo_id=pid_b, token_id="tok-ledger-b", daemon_name="laptop",
                    run_ledger_json=json.dumps(newer_rows), run_ledger_updated_at=now,
                ),
            ]
        )
        db.commit()

        repos = [db.get(Repo, pid_a), db.get(Repo, pid_b)]
        view = _run_ledger_views(db, repos, limit=2)

    assert [row["run_id"] for row in view["rows"]] == ["run-a", "run-b"]
    assert view["rows"][0]["task_classification"] == "new"
    assert view["stale"] is False


def test_dashboard_run_ledger_api_returns_rows():
    client = _client()
    _account_headers, _daemon_headers, pid = _repo_and_daemon(client)
    _login(client)

    with client.app.state.SessionLocal() as db:
        db.add(
            Daemon(
                id="dmn-ledger-c", repo_id=pid, token_id="tok-ledger-c", daemon_name="laptop",
                run_ledger_json=json.dumps([_ROW]), run_ledger_updated_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

    r = client.get("/v1/dashboard/run-ledger?limit=10")
    assert r.status_code == 200
    body = r.json()
    assert body["rows"][0]["run_id"] == "run-1"
    assert body["rows"][0]["usd_subscription_attributed"] == 0.25
    assert body["stale"] is False


def test_dashboard_run_ledger_api_caps_a_busy_window_at_published_envelope():
    client = _client()
    _account_headers, _daemon_headers, pid = _repo_and_daemon(client)
    _login(client)
    rows = [
        {
            **_ROW,
            "run_id": f"run-{i:03d}",
            "ended_at": (datetime.now(timezone.utc) - timedelta(seconds=i)).isoformat(),
        }
        for i in range(300)
    ]

    with client.app.state.SessionLocal() as db:
        db.add(
            Daemon(
                id="dmn-ledger-cap",
                repo_id=pid,
                token_id="tok-ledger-cap",
                daemon_name="laptop",
                run_ledger_json=json.dumps(rows),
                run_ledger_updated_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

    response = client.get("/v1/dashboard/run-ledger?limit=500")

    assert response.status_code == 200
    assert len(response.json()["rows"]) == 256
    assert response.json()["rows"][0]["run_id"] == "run-000"

"""Tests for `GET /v1/machines` (design-machines-and-guests.md R1, #1365).

Same test-client harness as `test_brnrd_dashboard.py` — copied rather than
imported, matching this suite's existing convention of one self-contained
`_client`/`_login`/`_create_repo` trio per test file instead of a shared
fixture module.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402

from _helpers import PUBLISH_EVERYTHING  # noqa: E402
from brnrd import create_app  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.models import Account, Daemon  # noqa: E402
from brnrd.oauth import GitHubIdentity  # noqa: E402
from brnrd.routers.accounts import account_for_github_identity, issue_session_token  # noqa: E402


def _client(**settings_overrides) -> TestClient:
    kwargs = dict(
        database_url="sqlite:///:memory:",
        public_base_url="https://brnrd.example",
        github_oauth_client_id="gh-client",
        github_oauth_client_secret="gh-secret",
    )
    kwargs.update(settings_overrides)
    app = create_app(Settings(**kwargs))
    return TestClient(app, base_url="https://testserver")


def _login(client: TestClient, *, github_id: str = "12345", login: str = "Gurio") -> str:
    with client.app.state.SessionLocal() as db:
        account = account_for_github_identity(
            db, GitHubIdentity(github_id=github_id, login=login, email=None)
        )
        token = issue_session_token(db, account)
    client.cookies.set("brnrd_session", token)
    return token


def _create_repo(client: TestClient, token: str, repo: str = "Gurio/brr") -> str:
    r = client.post(
        "/v1/accounts/repos",
        json={"repo_full_name": repo, "default_branch": "main", "publish_layers": PUBLISH_EVERYTHING},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["repo_id"]


def _account_id(client: TestClient, login: str = "Gurio") -> str:
    with client.app.state.SessionLocal() as db:
        account = db.query(Account).filter(Account.github_login == login).one()
        return account.id


def test_machines_api_requires_a_session():
    client = _client()
    r = client.get("/v1/machines")
    assert r.status_code == 401


def test_machines_api_lists_a_paired_machine_with_no_enabled_repo():
    """The exact #1365 fixture: a daemon paired at the account level, never
    registered against any repo (`repo_id is None`) — must still appear,
    with an empty `enabled_repos`, rather than being invisible because no
    repo row exists to hang it off.
    """
    client = _client()
    _login(client, login="Gurio")
    account_id = _account_id(client)
    with client.app.state.SessionLocal() as db:
        db.add(
            Daemon(
                id="dmn-zero-repo",
                account_id=account_id,
                repo_id=None,
                token_id="tok-zero-repo",
                daemon_name="iMac",
                online=True,
            )
        )
        db.commit()

    r = client.get("/v1/machines")

    assert r.status_code == 200
    body = r.json()
    assert len(body["machines"]) == 1
    machine = body["machines"][0]
    assert machine["daemon_id"] == "dmn-zero-repo"
    assert machine["daemon_name"] == "iMac"
    assert machine["enabled_repos"] == []


def test_machines_api_reports_the_enabled_repo_and_liveness():
    from datetime import datetime, timedelta, timezone

    client = _client()
    token = _login(client, login="Gurio")
    repo_id = _create_repo(client, token, repo="Gurio/brr")
    account_id = _account_id(client)
    stale = datetime.now(timezone.utc) - timedelta(minutes=10)
    with client.app.state.SessionLocal() as db:
        db.add(
            Daemon(
                id="dmn-with-repo",
                account_id=account_id,
                repo_id=repo_id,
                token_id="tok-with-repo",
                daemon_name="laptop",
                online=True,
                last_seen_at=stale,
            )
        )
        db.commit()

    body = client.get("/v1/machines").json()

    machine = body["machines"][0]
    assert machine["enabled_repos"] == [{"repo_id": repo_id, "repo_full_name": "Gurio/brr"}]
    # `online=True` but the heartbeat is 10 minutes stale — outside the
    # 2-minute liveness window, same predicate `_detect_daemon_live` /
    # `_repo_views` already use.
    assert machine["online"] is False


def test_machines_api_is_account_scoped_not_cross_tenant():
    """Two accounts, one database — each session only ever sees its own
    account's daemons, never the other's.
    """
    client = _client()
    _login(client, github_id="1", login="Gurio")
    gurio_account_id = _account_id(client, login="Gurio")
    _login(client, github_id="2", login="Other")
    other_account_id = _account_id(client, login="Other")
    with client.app.state.SessionLocal() as db:
        db.add(
            Daemon(
                id="dmn-gurio",
                account_id=gurio_account_id,
                repo_id=None,
                token_id="tok-gurio",
                daemon_name="gurio-box",
                online=True,
            )
        )
        db.add(
            Daemon(
                id="dmn-other",
                account_id=other_account_id,
                repo_id=None,
                token_id="tok-other",
                daemon_name="other-box",
                online=True,
            )
        )
        db.commit()

    _login(client, github_id="1", login="Gurio")
    body = client.get("/v1/machines").json()
    assert [m["daemon_id"] for m in body["machines"]] == ["dmn-gurio"]

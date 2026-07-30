"""Tests for where brnrd-bot's marker sync (#874, github_marker.py) actually
fires: repo bind (both the browser and API-key paths), installation sync,
and the dashboard's coarse recheck — plus the JSON shape the repo view
exposes. Unit behavior of the sync itself lives in test_github_marker.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient  # noqa: E402

from brnrd import create_app, github_marker, ids  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.models import Account, GitHubInstallation, Repo  # noqa: E402
from brnrd.oauth import GitHubIdentity  # noqa: E402
from brnrd.platforms import github as gh  # noqa: E402
from brnrd.routers import github_app as github_app_router  # noqa: E402
from brnrd.routers.accounts import account_for_github_identity, issue_session_token  # noqa: E402


def _client(**overrides) -> TestClient:
    kwargs = dict(database_url="sqlite:///:memory:", github_bot_token="ghs_bot")
    kwargs.update(overrides)
    app = create_app(Settings(**kwargs))
    return TestClient(app, base_url="https://testserver")


def _login(client: TestClient, *, github_id: str = "12345", login: str = "Gurio") -> tuple[str, str]:
    with client.app.state.SessionLocal() as db:
        account = account_for_github_identity(
            db, GitHubIdentity(github_id=github_id, login=login, email=None)
        )
        token = issue_session_token(db, account)
        account_id = account.id
    client.cookies.set("brnrd_session", token)
    return token, account_id


def _spy_sync(monkeypatch, calls: list):
    def fake(db, settings, repos):
        calls.append([r.repo_full_name for r in repos])
        return github_marker.MarkerSyncResult()

    monkeypatch.setattr(github_marker, "sync_marker_for_repos", fake)


def test_dashboard_connect_triggers_marker_sync(monkeypatch):
    calls: list = []
    _spy_sync(monkeypatch, calls)
    client = _client()
    _login(client)

    r = client.post("/v1/repos/connect", json={"repo_full_name": "Gurio/brr"})

    assert r.status_code == 200, r.text
    assert calls == [["Gurio/brr"]]


def test_account_api_connect_triggers_marker_sync(monkeypatch):
    calls: list = []
    _spy_sync(monkeypatch, calls)
    client = _client()
    token, _account_id = _login(client)

    r = client.post(
        "/v1/accounts/repos",
        json={"repo_full_name": "Gurio/brr"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert r.status_code == 201, r.text
    assert calls == [["Gurio/brr"]]


def test_reconnecting_an_existing_repo_does_not_resync_the_marker(monkeypatch):
    """`create_repo` short-circuits on an existing row before the marker
    sync call — this pins that the idempotent-reconnect path stays cheap and
    doesn't hit GitHub on every repeat call."""
    calls: list = []
    _spy_sync(monkeypatch, calls)
    client = _client()
    token, _account_id = _login(client)
    payload = {"repo_full_name": "Gurio/brr"}
    headers = {"Authorization": f"Bearer {token}"}

    assert client.post("/v1/accounts/repos", json=payload, headers=headers).status_code == 201
    assert client.post("/v1/accounts/repos", json=payload, headers=headers).status_code == 201

    assert calls == [["Gurio/brr"]], "the second (idempotent) call must not resync"


def test_installation_sync_triggers_marker_sync_for_the_bound_accounts_repos(monkeypatch):
    calls: list = []
    _spy_sync(monkeypatch, calls)
    app = create_app(Settings(database_url="sqlite:///:memory:", github_bot_token="ghs_bot"))

    with app.state.SessionLocal() as db:
        account = Account(id="acc-1", github_id="1", github_login="Gurio")
        db.add(account)
        db.add(
            Repo(
                id=ids.repo_id(),
                account_id="acc-1",
                forge="github",
                repo_full_name="Gurio/brr",
                repo_owner="Gurio",
                repo_name="brr",
            )
        )
        db.commit()

    monkeypatch.setattr(
        github_app_router.gh_app, "installation_access_token", lambda *_a: "ghs_install"
    )
    monkeypatch.setattr(
        github_app_router.gh_app,
        "list_installation_repositories",
        lambda *_a, **_k: [],
    )

    with app.state.SessionLocal() as db:
        github_app_router.sync_installation(db, app.state.settings, "77", "acc-1")

    assert calls == [["Gurio/brr"]]


def test_installation_sync_without_a_bound_account_never_calls_marker_sync(monkeypatch):
    calls: list = []
    _spy_sync(monkeypatch, calls)
    app = create_app(Settings(database_url="sqlite:///:memory:", github_bot_token="ghs_bot"))
    monkeypatch.setattr(
        github_app_router.gh_app, "installation_access_token", lambda *_a: "ghs_install"
    )
    monkeypatch.setattr(
        github_app_router.gh_app,
        "list_installation_repositories",
        lambda *_a, **_k: [],
    )

    with app.state.SessionLocal() as db:
        github_app_router.sync_installation(db, app.state.settings, "77")

    assert calls == []


def test_dashboard_coarse_recheck_only_touches_stale_repos(monkeypatch):
    calls: list = []
    _spy_sync(monkeypatch, calls)
    client = _client()
    _token, account_id = _login(client)

    with client.app.state.SessionLocal() as db:
        fresh = Repo(
            id=ids.repo_id(),
            account_id=account_id,
            forge="github",
            repo_full_name="Gurio/fresh",
            repo_owner="Gurio",
            repo_name="fresh",
            github_bot_checked_at=datetime.now(timezone.utc),
        )
        stale = Repo(
            id=ids.repo_id(),
            account_id=account_id,
            forge="github",
            repo_full_name="Gurio/stale",
            repo_owner="Gurio",
            repo_name="stale",
            github_bot_checked_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        )
        never_checked = Repo(
            id=ids.repo_id(),
            account_id=account_id,
            forge="github",
            repo_full_name="Gurio/never-checked",
            repo_owner="Gurio",
            repo_name="never-checked",
        )
        db.add_all([fresh, stale, never_checked])
        db.commit()

    r = client.get("/v1/dashboard/repos")

    assert r.status_code == 200
    assert len(calls) == 1
    assert sorted(calls[0]) == ["Gurio/never-checked", "Gurio/stale"]


def test_dashboard_coarse_recheck_is_a_no_op_when_nothing_is_stale(monkeypatch):
    calls: list = []
    _spy_sync(monkeypatch, calls)
    client = _client()
    _token, account_id = _login(client)

    with client.app.state.SessionLocal() as db:
        db.add(
            Repo(
                id=ids.repo_id(),
                account_id=account_id,
                forge="github",
                repo_full_name="Gurio/fresh",
                repo_owner="Gurio",
                repo_name="fresh",
                github_bot_checked_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

    assert client.get("/v1/dashboard/repos").status_code == 200
    assert calls == []


def test_dashboard_repo_view_exposes_marker_state_and_the_absence_line(monkeypatch):
    client = _client(github_bot_login="brnrd-bot")
    _token, account_id = _login(client)
    monkeypatch.setattr(gh, "list_repository_invitations", lambda *a, **k: [])
    monkeypatch.setattr(gh, "check_repository_collaborator", lambda *a, **k: False)

    r = client.post("/v1/repos/connect", json={"repo_full_name": "Gurio/brr"})
    assert r.status_code == 200, r.text

    body = client.get("/v1/dashboard/repos").json()
    row = next(row for row in body["connected_repos"] if row["repo_full_name"] == "Gurio/brr")
    assert row["github_bot_collaborator"] is False
    assert row["github_bot_marker_notice"] == (
        "brnrd-bot not a collaborator — assigns / review-requests / "
        "comment-tags addressed to it won't reach the resident; invite it "
        "in Settings → Collaborators."
    )
    assert row["github_bot_notice"] is None


def test_dashboard_repo_view_never_renders_the_absence_line_for_unknown_state(monkeypatch):
    """Token unset ⇒ every check is skipped and the state stays unknown
    (`None`) — the view must not fall back to claiming "not a collaborator"
    just because it never got a real answer."""
    client = _client(github_bot_token="")
    _login(client)

    r = client.post("/v1/repos/connect", json={"repo_full_name": "Gurio/brr"})
    assert r.status_code == 200, r.text

    body = client.get("/v1/dashboard/repos").json()
    row = next(row for row in body["connected_repos"] if row["repo_full_name"] == "Gurio/brr")
    assert row["github_bot_collaborator"] is None
    assert row["github_bot_marker_notice"] is None

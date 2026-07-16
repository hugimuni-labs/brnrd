"""Tests for the CPS (Current Planned State) plan/ledger mirror + dashboard view."""

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


def test_daemon_plans_snapshot_replaces_repo_and_account_fields():
    client = _client()
    _, daemon_headers, _repo_id = _repo_and_daemon(client)
    assert client.post(
        "/v1/daemons/register",
        json={"daemon_name": "laptop"},
        headers=daemon_headers,
    ).status_code == 200

    posted = client.put(
        "/v1/daemons/plans",
        json={
            "repo_plan_md": "# active plan\n\nship the CPS view",
            "cross_repo_plan_md": "# cross-repo\n\ncoordinate release",
            "decision_ledger_md": "## decisions\n\n- shipped X",
            "workflow_md": "## Autonomy\nself-woken -> agenda",
        },
        headers=daemon_headers,
    )
    assert posted.status_code == 200, posted.text
    body = posted.json()
    assert body["repo_plan_md"] == "# active plan\n\nship the CPS view"
    assert body["cross_repo_plan_md"] == "# cross-repo\n\ncoordinate release"
    assert body["decision_ledger_md"] == "## decisions\n\n- shipped X"
    assert body["workflow_md"] == "## Autonomy\nself-woken -> agenda"
    assert body["plans_updated_at"] is not None

    # Republishing overwrites rather than accumulating (last-write-wins).
    replaced = client.put(
        "/v1/daemons/plans",
        json={"repo_plan_md": "# active plan\n\nrevised", "cross_repo_plan_md": "", "decision_ledger_md": ""},
        headers=daemon_headers,
    )
    assert replaced.status_code == 200
    assert replaced.json()["repo_plan_md"] == "# active plan\n\nrevised"
    assert replaced.json()["cross_repo_plan_md"] == ""


def test_plans_page_redirects_to_dashboard_decisions_space():
    """The Jinja /plans page is retired (first real template cut of the
    jinja-removal plan): the decisions-space panel on "/" renders the same
    mirror structured. The URL survives as a permanent redirect so old
    nav links and bookmarks don't 404."""
    client = _client()
    page = client.get("/plans", follow_redirects=False)
    assert page.status_code == 308
    assert page.headers["location"] == "/"


def test_dashboard_plans_api_returns_mirrored_decisions_space():
    """#324 Phase 0: the SvelteKit dashboard's JSON view of the same
    `PUT /v1/daemons/plans` mirror the Jinja `/plans` page renders raw."""
    client = _client()
    _, daemon_headers, _repo_id = _repo_and_daemon(client)
    assert client.post(
        "/v1/daemons/register",
        json={"daemon_name": "laptop"},
        headers=daemon_headers,
    ).status_code == 200
    client.put(
        "/v1/daemons/plans",
        json={
            "repo_plan_md": "# Active plan\n\nUpdated: 2026-07-09\n\n## Ranked moves\n1. **Merge PR** now.\n2. **Fix bug** later.",
            "cross_repo_plan_md": "",
            "decision_ledger_md": "## Chose X (2026-07-08)\nBecause Y.",
            "workflow_md": "## Gating\nvisibility over approval",
        },
        headers=daemon_headers,
    )
    _login_cookie(client)

    res = client.get("/v1/dashboard/plans")

    assert res.status_code == 200
    body = res.json()
    assert body["plans"] == [
        {
            "repo_label": "Gurio/brr",
            "plan_md": "# Active plan\n\nUpdated: 2026-07-09\n\n## Ranked moves\n1. **Merge PR** now.\n2. **Fix bug** later.",
            "updated_at": body["plans"][0]["updated_at"],
        }
    ]
    assert body["plans"][0]["updated_at"] is not None
    assert body["decisions_md"] == "## Chose X (2026-07-08)\nBecause Y."
    assert body["cross_repo_plan_md"] == ""
    assert body["workflow_md"] == "## Gating\nvisibility over approval"
    assert body["reported_at"] is not None


def test_dashboard_plans_api_requires_session():
    client = _client()
    res = client.get("/v1/dashboard/plans")
    assert res.status_code == 401

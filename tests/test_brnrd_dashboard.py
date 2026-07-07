"""Tests for the brnrd dashboard repo view."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402

from brnrd import create_app  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.models import Repo  # noqa: E402
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
    app = create_app(
        Settings(**kwargs)
    )
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
        json={"repo_full_name": repo, "default_branch": "main"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["repo_id"]


def test_dashboard_shows_enabled_repo():
    client = _client()
    token = _login(client, login="Gurio")
    _create_repo(client, token)

    r = client.get("/")

    assert r.status_code == 200
    assert "Gurio/brr" in r.text
    assert "Waiting for local daemon" in r.text
    assert "/activity" in r.text


def test_dashboard_disconnect_removes_repo():
    client = _client()
    token = _login(client, login="Gurio")
    repo_id = _create_repo(client, token)

    r = client.post(f"/repos/{repo_id}/disconnect", follow_redirects=False)

    assert r.status_code == 303
    with client.app.state.SessionLocal() as db:
        assert db.get(Repo, repo_id) is None


def test_dashboard_renders_real_quota_and_flags_stale_reports():
    """#237: the runner-quota card reads a daemon's real report instead of
    the hardcoded UNKNOWN placeholder, and flags a report that's gone
    quiet as stale rather than silently trusting old numbers."""
    import json
    from datetime import datetime, timedelta, timezone

    from brnrd.models import Daemon
    from brnrd_web.activity_dashboard import _quota_views

    client = _client()
    token = _login(client)
    pid = _create_repo(client, token)

    with client.app.state.SessionLocal() as db:
        repo = db.get(Repo, pid)
        daemon = Daemon(
            id="dmn-quota-1",
            repo_id=pid,
            token_id="tok-quota-1",
            daemon_name="laptop",
            quota_json=json.dumps(
                [{"shell": "claude", "status": "known", "windows": [{"label": "5h window", "used": None, "limit": None, "percent": 61.0}]}]
            ),
            quota_updated_at=datetime.now(timezone.utc),
        )
        db.add(daemon)
        db.commit()

        fresh = _quota_views(db, [repo], runner_stats=[])
        assert fresh == [
            {
                "shell": "claude",
                "status": "known",
                "windows": [{"label": "5h window", "used": None, "limit": None, "percent": 61.0}],
                "credits": None,
            }
        ]

        daemon.quota_updated_at = datetime.now(timezone.utc) - timedelta(seconds=999)
        db.commit()
        stale = _quota_views(db, [repo], runner_stats=[])
        assert stale[0]["status"] == "stale"
        assert stale[0]["windows"][0]["percent"] is None


def test_dashboard_quota_staleness_measures_scrape_age_not_publish_cadence():
    """The 'lying Claude usage panel' bug (2026-07-07): a daemon that keeps
    publishing every ~25-30s makes `quota_updated_at` (the publish time)
    always fresh, even when the underlying Claude `/usage` scrape a shell
    carries has gone stale for hours because no Claude run has been active
    to refresh it. Staleness must be measured against the shell's own
    `updated_at`, not the publish timestamp."""
    import json
    from datetime import datetime, timedelta, timezone

    from brnrd.models import Daemon
    from brnrd_web.activity_dashboard import _quota_views

    client = _client()
    token = _login(client)
    pid = _create_repo(client, token)

    with client.app.state.SessionLocal() as db:
        repo = db.get(Repo, pid)
        stale_scrape_at = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        daemon = Daemon(
            id="dmn-quota-2",
            repo_id=pid,
            token_id="tok-quota-2",
            daemon_name="laptop",
            quota_json=json.dumps(
                [
                    {
                        "shell": "claude",
                        "status": "known",
                        "updated_at": stale_scrape_at,
                        "windows": [
                            {"label": "5h window", "used": None, "limit": None, "percent": 61.0}
                        ],
                    }
                ]
            ),
            # The daemon just published this instant — the bug this guards
            # is exactly that a fresh publish timestamp used to be treated
            # as proof the *data* was fresh too.
            quota_updated_at=datetime.now(timezone.utc),
        )
        db.add(daemon)
        db.commit()

        views = _quota_views(db, [repo], runner_stats=[])
        assert views[0]["status"] == "stale"
        assert views[0]["windows"][0]["percent"] is None


def test_dashboard_quota_api_requires_login():
    """The SvelteKit frontend (`src/frontend`) fetches this JSON endpoint
    client-side; a 401 is the right shape for an unauthenticated fetch(),
    not a login-page redirect."""
    client = _client()
    r = client.get("/v1/dashboard/quota")
    assert r.status_code == 401


def test_dashboard_quota_api_returns_real_windows():
    """JSON twin of `test_dashboard_renders_real_quota_and_flags_stale_reports`
    for slice 2's window-track view — same `_quota_views` data, fetched over
    `/v1/dashboard/quota` instead of rendered into `dashboard.html`."""
    import json
    from datetime import datetime, timezone

    from brnrd.models import Daemon

    client = _client()
    token = _login(client)
    pid = _create_repo(client, token)

    with client.app.state.SessionLocal() as db:
        daemon = Daemon(
            id="dmn-quota-api-1",
            repo_id=pid,
            token_id="tok-quota-api-1",
            daemon_name="laptop",
            quota_json=json.dumps(
                [
                    {
                        "shell": "claude",
                        "status": "known",
                        "windows": [
                            {
                                "label": "5h window",
                                "used": None,
                                "limit": None,
                                "percent": 61.0,
                                "reset": "resets 9:00PM",
                                "resets_at": 1783360000.0,
                            }
                        ],
                    }
                ]
            ),
            quota_updated_at=datetime.now(timezone.utc),
        )
        db.add(daemon)
        db.commit()

    r = client.get("/v1/dashboard/quota")
    assert r.status_code == 200
    body = r.json()
    assert "generated_at" in body
    windows = body["runner_quotas"][0]["windows"]
    assert windows[0]["percent"] == 61.0
    assert windows[0]["resets_at"] == 1783360000.0


def test_dashboard_live_runs_api_requires_login():
    """#258, same auth shape as the quota JSON endpoint: fetched by JS, a
    401 not a login-page redirect."""
    client = _client()
    r = client.get("/v1/dashboard/live-runs")
    assert r.status_code == 401


def test_dashboard_live_runs_api_dedupes_across_repo_registrations():
    """A single physical daemon registers one `Daemon` row per repo it's
    connected to (`Daemon.repo_id`), and each row would publish the same
    underlying presence entries — the account view must dedupe by run
    identity, freshest report wins, not show the same live run twice."""
    import json
    from datetime import datetime, timedelta, timezone

    from brnrd.models import Daemon
    from brnrd_web.activity_dashboard import _live_runs_views

    client = _client()
    token = _login(client)
    pid_a = _create_repo(client, token, repo="Gurio/brr")
    pid_b = _create_repo(client, token, repo="Gurio/other")

    now = datetime.now(timezone.utc)
    run_row = [{"id": "pres-1", "kind": "daemon", "stream": "telegram:x:", "label": "Add live run labels", "run_id": "run-a", "repo_label": "Gurio/brr", "started_at": "2026-07-06T23:00:00Z", "last_seen": "2026-07-06T23:05:00Z"}]
    with client.app.state.SessionLocal() as db:
        older = Daemon(
            id="dmn-live-a", repo_id=pid_a, token_id="tok-live-a", daemon_name="laptop",
            live_runs_json=json.dumps(run_row), live_runs_updated_at=now - timedelta(seconds=30),
        )
        newer = Daemon(
            id="dmn-live-b", repo_id=pid_b, token_id="tok-live-b", daemon_name="laptop",
            live_runs_json=json.dumps(run_row), live_runs_updated_at=now,
        )
        db.add_all([older, newer])
        db.commit()

        repos = [db.get(Repo, pid_a), db.get(Repo, pid_b)]
        view = _live_runs_views(db, repos)
    assert len(view["runs"]) == 1
    assert view["runs"][0]["run_id"] == "run-a"
    assert view["runs"][0]["label"] == "Add live run labels"
    assert view["stale"] is False


def test_dashboard_live_runs_api_returns_runs():
    import json
    from datetime import datetime, timezone

    from brnrd.models import Daemon

    client = _client()
    token = _login(client)
    pid = _create_repo(client, token)

    with client.app.state.SessionLocal() as db:
        daemon = Daemon(
            id="dmn-live-c", repo_id=pid, token_id="tok-live-c", daemon_name="laptop",
            live_runs_json=json.dumps(
                [{"id": "pres-2", "kind": "daemon", "stream": "telegram:x:", "label": "Ship dashboard slice", "run_id": "run-b", "repo_label": "Gurio/brr", "started_at": "2026-07-06T23:00:00Z", "last_seen": "2026-07-06T23:05:00Z"}]
            ),
            live_runs_updated_at=datetime.now(timezone.utc),
        )
        db.add(daemon)
        db.commit()

    r = client.get("/v1/dashboard/live-runs")
    assert r.status_code == 200
    body = r.json()
    assert body["runs"][0]["run_id"] == "run-b"
    assert body["runs"][0]["label"] == "Ship dashboard slice"
    assert body["stale"] is False


def test_dashboard_pr_review_queue_api_requires_login():
    """#259, same auth shape as quota/live-runs JSON endpoints."""
    client = _client()
    r = client.get("/v1/dashboard/pr-review-queue")
    assert r.status_code == 401


def test_dashboard_pr_review_queue_dedupes_across_repo_registrations():
    """A single physical daemon can report the same account queue through
    multiple repo registrations; freshest report wins by repo + PR number."""
    import json
    from datetime import datetime, timedelta, timezone

    from brnrd.models import Daemon
    from brnrd_web.activity_dashboard import _pr_review_queue_views

    client = _client()
    token = _login(client)
    pid_a = _create_repo(client, token, repo="Gurio/brr")
    pid_b = _create_repo(client, token, repo="Gurio/other")

    now = datetime.now(timezone.utc)
    older_prs = [
        {
            "number": 259,
            "title": "Old title",
            "url": "https://github.com/Gurio/brr/pull/259",
            "repo_label": "Gurio/brr",
            "created_at": "2026-07-07T09:00:00Z",
            "draft": False,
            "author": "gurio",
        }
    ]
    newer_prs = [{**older_prs[0], "title": "Fresh title"}]
    with client.app.state.SessionLocal() as db:
        older = Daemon(
            id="dmn-pr-a", repo_id=pid_a, token_id="tok-pr-a", daemon_name="laptop",
            pr_review_queue_json=json.dumps(older_prs), pr_review_queue_updated_at=now - timedelta(seconds=30),
        )
        newer = Daemon(
            id="dmn-pr-b", repo_id=pid_b, token_id="tok-pr-b", daemon_name="laptop",
            pr_review_queue_json=json.dumps(newer_prs), pr_review_queue_updated_at=now,
        )
        db.add_all([older, newer])
        db.commit()

        repos = [db.get(Repo, pid_a), db.get(Repo, pid_b)]
        view = _pr_review_queue_views(db, repos)
    assert len(view["prs"]) == 1
    assert view["prs"][0]["title"] == "Fresh title"
    assert view["prs"][0]["number"] == 259
    assert view["stale"] is False


def test_dashboard_pr_review_queue_api_returns_prs_oldest_first():
    import json
    from datetime import datetime, timezone

    from brnrd.models import Daemon

    client = _client()
    token = _login(client)
    pid = _create_repo(client, token)

    with client.app.state.SessionLocal() as db:
        daemon = Daemon(
            id="dmn-pr-c", repo_id=pid, token_id="tok-pr-c", daemon_name="laptop",
            pr_review_queue_json=json.dumps(
                [
                    {
                        "number": 260,
                        "title": "Newer PR",
                        "url": "https://github.com/Gurio/brr/pull/260",
                        "repo_label": "Gurio/brr",
                        "created_at": "2026-07-07T10:00:00Z",
                        "draft": True,
                        "author": "alice",
                    },
                    {
                        "number": 259,
                        "title": "Older PR",
                        "url": "https://github.com/Gurio/brr/pull/259",
                        "repo_label": "Gurio/brr",
                        "created_at": "2026-07-07T09:00:00Z",
                        "draft": False,
                        "author": "gurio",
                    },
                ]
            ),
            pr_review_queue_updated_at=datetime.now(timezone.utc),
        )
        db.add(daemon)
        db.commit()

    r = client.get("/v1/dashboard/pr-review-queue")
    assert r.status_code == 200
    body = r.json()
    assert [pr["number"] for pr in body["prs"]] == [259, 260]
    assert body["prs"][0]["draft"] is False
    assert body["prs"][1]["draft"] is True
    assert body["stale"] is False


def test_dashboard_can_issue_telegram_pair_link():
    client = _client(telegram_bot_username="@brnrd_bot")
    token = _login(client, login="Gurio")
    repo_id = _create_repo(client, token)

    page = client.get("/")
    assert page.status_code == 200
    assert f"/repos/{repo_id}/telegram-pair" in page.text

    r = client.post(f"/repos/{repo_id}/telegram-pair")
    assert r.status_code == 200
    assert "https://t.me/brnrd_bot?start=TG-" in r.text
    assert "Open Telegram and press Start" in r.text

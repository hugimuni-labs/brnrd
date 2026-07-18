"""Tests for the brnrd dashboard repo view."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402

from brnrd import create_app  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.models import Account, GitHubInstallation, GitHubInstalledRepo, Repo  # noqa: E402
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


def _account_id(client: TestClient, login: str = "Gurio") -> str:
    with client.app.state.SessionLocal() as db:
        account = db.query(Account).filter(Account.github_login == login).one()
        return account.id


def _add_installation_repo(
    client: TestClient,
    account_id: str,
    repo_full_name: str,
    *,
    forge_repo_id: str = "123",
    default_branch: str = "main",
) -> None:
    with client.app.state.SessionLocal() as db:
        installation = db.query(GitHubInstallation).filter(GitHubInstallation.account_id == account_id).one_or_none()
        if installation is None:
            installation = GitHubInstallation(
                id=f"ghi-{account_id}",
                account_id=account_id,
                installation_id="42",
                target_login="Gurio",
                target_type="User",
            )
            db.add(installation)
            db.flush()
        db.add(
            GitHubInstalledRepo(
                id=f"ghr-{repo_full_name.replace('/', '-')}",
                github_installation_id=installation.id,
                repo_full_name=repo_full_name,
                forge_repo_id=forge_repo_id,
                default_branch=default_branch,
            )
        )
        db.commit()


def test_dashboard_shows_enabled_repo():
    """The /v1/dashboard/repos JSON endpoint exposes the connected repo with
    its daemon status — this is the data surface the SvelteKit dashboard
    renders at '/'; the old Jinja GET / route was removed when brnrd_web
    was merged into src/brnrd/routers/ and the SPA took over '/'."""
    client = _client()
    token = _login(client, login="Gurio")
    _create_repo(client, token)

    r = client.get("/v1/dashboard/repos")

    assert r.status_code == 200
    body = r.json()
    repo_names = [row["repo_full_name"] for row in body["connected_repos"]]
    assert "Gurio/brr" in repo_names
    daemon_labels = [row["daemon_label"] for row in body["connected_repos"]]
    assert "Waiting for local daemon" in daemon_labels


def test_repos_page_redirects_to_dashboard():
    client = _client()

    r = client.get("/repos", follow_redirects=False)

    assert r.status_code == 308
    assert r.headers["location"] == "/"


@pytest.mark.parametrize(
    ("method", "path", "json_body"),
    [
        ("get", "/v1/dashboard/repos", None),
        ("post", "/v1/repos/connect", {"repo_full_name": "Gurio/brr"}),
        ("post", "/v1/repos/repo_missing/invite-bot", {}),
        ("post", "/v1/repos/repo_missing/telegram-pair", {}),
        ("post", "/v1/repos/repo_missing/disconnect", {}),
    ],
)
def test_repos_json_endpoints_require_login(method, path, json_body):
    client = _client()

    request = getattr(client, method)
    r = request(path, json=json_body) if json_body is not None else request(path)

    assert r.status_code == 401
    assert r.json()["detail"] == "unauthenticated"


def test_dashboard_repos_api_returns_repo_management_payload():
    client = _client()
    token = _login(client, login="Gurio")
    _create_repo(client, token, repo="Gurio/brr")
    account_id = _account_id(client)
    _add_installation_repo(client, account_id, "Gurio/brr", forge_repo_id="100")
    _add_installation_repo(client, account_id, "Gurio/new", forge_repo_id="101", default_branch="trunk")

    r = client.get("/v1/dashboard/repos")

    assert r.status_code == 200
    body = r.json()
    assert body["account"]["github_login"] == "Gurio"
    assert body["connected_count"] == 1
    assert body["connected_repos"][0]["repo_full_name"] == "Gurio/brr"
    assert body["connected_repos"][0]["daemon_status"] == "missing"
    assert body["connected_repos"][0]["setup_command"].startswith("cd brr\n")
    installed = {repo["repo_full_name"]: repo for repo in body["installed_repos"]}
    assert installed["Gurio/brr"]["connected"] is True
    assert installed["Gurio/new"]["connected"] is False
    assert installed["Gurio/new"]["default_branch"] == "trunk"
    assert body["github_app_slug"] == "brnrd-dev"
    assert body["github_bot_user_login"] == "brnrd-bot"
    assert body["oauth_ready"] is True


def test_dashboard_repos_api_returns_latest_daemon_gate_health():
    import json

    from brnrd.models import Daemon

    client = _client()
    token = _login(client, login="Gurio")
    repo_id = _create_repo(client, token, repo="Gurio/brr")
    gates = [
        {
            "gate": "telegram",
            "last_poll_ok": "2026-07-13T12:00:00+00:00",
            "age_seconds": 9,
            "last_error": None,
            "status": "ok",
        }
    ]
    with client.app.state.SessionLocal() as db:
        db.add(
            Daemon(
                id="dmn-health-dashboard",
                repo_id=repo_id,
                token_id="tok-health-dashboard",
                daemon_name="laptop",
                gate_health_json=json.dumps(gates),
            )
        )
        db.commit()

    response = client.get("/v1/dashboard/repos")

    assert response.status_code == 200
    assert response.json()["connected_repos"][0]["gates"] == gates


def test_dashboard_connect_repo_api_enables_repo():
    client = _client()
    _login(client, login="Gurio")

    r = client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/new", "forge_repo_id": "999", "default_branch": "trunk"},
    )

    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert "Repo enabled" in r.json()["notice"]
    with client.app.state.SessionLocal() as db:
        repo = db.query(Repo).filter(Repo.repo_full_name == "Gurio/new").one()
        assert repo.default_branch == "trunk"
        assert repo.forge_repo_id == "999"


def test_dashboard_connect_repo_api_returns_error_notice_for_bad_name():
    client = _client()
    _login(client, login="Gurio")

    r = client.post("/v1/repos/connect", json={"repo_full_name": "not-a-full-name"})

    assert r.status_code == 400
    assert r.json() == {"ok": False, "notice": "repo must look like owner/name"}


def test_dashboard_invite_bot_api_returns_notice():
    client = _client()
    token = _login(client, login="Gurio")
    repo_id = _create_repo(client, token)

    r = client.post(f"/v1/repos/{repo_id}/invite-bot")

    assert r.status_code == 200
    assert r.json() == {
        "ok": True,
        "notice": "Could not find a synced installation for the bot-user invite.",
    }


def test_dashboard_repo_action_returns_error_notice_for_missing_repo():
    client = _client()
    _login(client, login="Gurio")

    r = client.post("/v1/repos/repo_missing/invite-bot")

    assert r.status_code == 404
    assert r.json() == {"ok": False, "notice": "repo not found"}


def test_dashboard_disconnect_removes_repo():
    client = _client()
    token = _login(client, login="Gurio")
    repo_id = _create_repo(client, token)

    r = client.post(f"/v1/repos/{repo_id}/disconnect")

    assert r.status_code == 200
    assert r.json() == {"ok": True, "notice": "Repo disconnected from brnrd."}
    with client.app.state.SessionLocal() as db:
        assert db.get(Repo, repo_id) is None


def test_dashboard_renders_real_quota_and_flags_stale_reports():
    """#237: the runner-quota card reads a daemon's real report instead of
    the hardcoded UNKNOWN placeholder, and flags a report that's gone
    quiet as stale rather than silently trusting old numbers."""
    import json
    from datetime import datetime, timedelta, timezone

    from brnrd.models import Daemon
    from brnrd.routers.dashboard import _quota_views

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
                "reset_credits": None,
                "spend": None,
                "burn": None,
            }
        ]

        daemon.quota_updated_at = datetime.now(timezone.utc) - timedelta(seconds=999)
        db.commit()
        stale = _quota_views(db, [repo], runner_stats=[])
        assert stale[0]["status"] == "stale"
        assert stale[0]["windows"][0]["percent"] is None


def test_dashboard_carries_the_burn_rate_and_drops_it_when_the_report_goes_stale():
    """The Codex burn rate (2026-07-13) is the dashboard's only short-horizon
    reading now that OpenAI has stopped publishing the 5h window — so it has to
    reach the panel. It also *decays*, unlike `reset_credits`: a rate measured
    off a daemon that went quiet hours ago describes an account that may have
    been idle since, and "burning 22 points every 4 hours" is a lie the moment
    it stops being current."""
    import json
    from datetime import datetime, timedelta, timezone

    from brnrd.models import Daemon
    from brnrd.routers.dashboard import _quota_views

    client = _client()
    token = _login(client)
    pid = _create_repo(client, token)
    burn = {
        "window_minutes": 10080.0,
        "hours": 5.0,
        "burned_percent": 22.0,
        "to_remaining_percent": 53.0,
        "projected_remaining_percent": 23.2,
        "sustainable": False,
    }

    with client.app.state.SessionLocal() as db:
        repo = db.get(Repo, pid)
        db.add(
            Daemon(
                id="dmn-burn-1",
                repo_id=pid,
                token_id="tok-burn-1",
                daemon_name="laptop",
                quota_json=json.dumps(
                    [
                        {
                            "shell": "codex",
                            "status": "known",
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                            "windows": [
                                {"label": "weekly", "used": None, "limit": None, "percent": 53.0}
                            ],
                            "reset_credits": 3,
                            "burn": burn,
                        }
                    ]
                ),
                quota_updated_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

        assert _quota_views(db, [repo], runner_stats=[])[0]["burn"] == burn

        daemon = db.get(Daemon, "dmn-burn-1")
        daemon.quota_updated_at = datetime.now(timezone.utc) - timedelta(seconds=999)
        daemon.quota_json = json.dumps(
            [
                {
                    "shell": "codex",
                    "status": "known",
                    "updated_at": (
                        datetime.now(timezone.utc) - timedelta(seconds=999)
                    ).isoformat(),
                    "windows": [
                        {"label": "weekly", "used": None, "limit": None, "percent": 53.0}
                    ],
                    "reset_credits": 3,
                    "burn": burn,
                }
            ]
        )
        db.commit()

        stale = _quota_views(db, [repo], runner_stats=[])[0]
        assert stale["status"] == "stale"
        assert stale["burn"] is None
        # …while a granted reset credit does not decay, and still shows.
        assert stale["reset_credits"] == 3


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
    from brnrd.routers.dashboard import _quota_views

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
    from brnrd.routers.dashboard import _live_runs_views

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
                [{"id": "pres-2", "kind": "daemon", "stream": "telegram:x:", "label": "Ship dashboard slice", "name": "run naming", "run_id": "run-b", "repo_label": "Gurio/brr", "started_at": "2026-07-06T23:00:00Z", "last_seen": "2026-07-06T23:05:00Z"}]
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
    assert body["runs"][0]["name"] == "run naming"
    assert body["stale"] is False


def test_dashboard_live_runs_api_returns_spawn_max_concurrent():
    """Loom envelope Phase 1 (kb/design-multi-workstream-concurrency.md
    §"Loom envelope"): the configured spawn: pool width piggybacks on the
    live-runs publish so the dashboard's "limits" panel has something to
    render against, with no new endpoint."""
    import json
    from datetime import datetime, timezone

    from brnrd.models import Daemon

    client = _client()
    token = _login(client)
    pid = _create_repo(client, token)

    with client.app.state.SessionLocal() as db:
        daemon = Daemon(
            id="dmn-live-d", repo_id=pid, token_id="tok-live-d", daemon_name="laptop",
            live_runs_json=json.dumps(
                [
                    {"id": "pres-3", "kind": "daemon", "stream": "telegram:x:", "label": "primary", "run_id": "run-c", "repo_label": "Gurio/brr", "started_at": "2026-07-08T23:00:00Z", "last_seen": "2026-07-08T23:05:00Z", "is_subspawn": False},
                    {"id": "pres-4", "kind": "daemon", "stream": "telegram:x:", "label": "worker", "run_id": "run-d", "repo_label": "Gurio/brr", "started_at": "2026-07-08T23:01:00Z", "last_seen": "2026-07-08T23:05:00Z", "is_subspawn": True, "parent_run_id": "run-c"},
                ]
            ),
            live_runs_updated_at=datetime.now(timezone.utc),
            spawn_max_concurrent=4,
        )
        db.add(daemon)
        db.commit()

    r = client.get("/v1/dashboard/live-runs")
    assert r.status_code == 200
    body = r.json()
    assert body["spawn_max_concurrent"] == 4
    assert sum(1 for run in body["runs"] if run.get("is_subspawn")) == 1


def test_put_live_runs_stores_spawn_max_concurrent():
    """The daemon-side write half of the same field — `PUT
    /v1/daemons/live-runs` (`src/brr/gates/cloud.py::_publish_live_runs`)
    now sends `spawn_max_concurrent` alongside `runs`; confirm the router
    stores and echoes it back rather than silently dropping the new key."""
    client = _client()
    account_token = _login(client)
    repo_id = _create_repo(client, account_token)
    account_headers = {"Authorization": f"Bearer {account_token}"}

    pair = client.post("/v1/accounts/pair").json()
    client.post(
        f"/v1/accounts/pair/{pair['pair_code']}/approve",
        json={"repo_id": repo_id},
        headers=account_headers,
    )
    paired = client.get(
        f"/v1/accounts/pair/{pair['pair_code']}",
        params={"poll_secret": pair["poll_secret"]},
    ).json()
    daemon_headers = {"Authorization": f"Bearer {paired['daemon_token']}"}
    client.post("/v1/daemons/register", json={"daemon_name": "laptop"}, headers=daemon_headers)

    r = client.put(
        "/v1/daemons/live-runs",
        json={"runs": [], "spawn_max_concurrent": 4},
        headers=daemon_headers,
    )
    assert r.status_code == 200
    assert r.json()["spawn_max_concurrent"] == 4

    with client.app.state.SessionLocal() as db:
        from brnrd.models import Daemon

        daemon = db.query(Daemon).filter(Daemon.repo_id == repo_id).one()
        assert daemon.spawn_max_concurrent == 4


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
    from brnrd.routers.dashboard import _pr_review_queue_views

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


def test_dashboard_config_requests_api_requires_login():
    """Loom envelope Phase 2 dashboard surface, same auth shape as the
    other JSON dashboard endpoints."""
    client = _client()
    r = client.get("/v1/dashboard/config-requests")
    assert r.status_code == 401


def test_dashboard_run_ledger_span_filters_before_limit():
    """The loom's longer shelf windows refill from the closed ledger, not
    merely re-filter the endpoint's latest fixed batch."""
    import json
    from datetime import datetime, timedelta, timezone

    from brnrd.models import Daemon

    client = _client()
    token = _login(client)
    pid = _create_repo(client, token)
    now = datetime.now(timezone.utc)
    rows = [
        {"run_id": "recent", "ended_at": (now - timedelta(hours=2)).isoformat()},
        {"run_id": "older", "ended_at": (now - timedelta(days=2)).isoformat()},
    ]
    with client.app.state.SessionLocal() as db:
        db.add(Daemon(
            id="dmn-ledger-span", repo_id=pid, token_id="tok-ledger-span",
            daemon_name="laptop", run_ledger_json=json.dumps(rows),
            run_ledger_updated_at=now,
        ))
        db.commit()

    one_day = client.get("/v1/dashboard/run-ledger?limit=1&span_seconds=86400")
    assert one_day.status_code == 200
    assert [row["run_id"] for row in one_day.json()["rows"]] == ["recent"]

    seven_days = client.get("/v1/dashboard/run-ledger?limit=10&span_seconds=604800")
    assert [row["run_id"] for row in seven_days.json()["rows"]] == ["recent", "older"]


def test_dashboard_config_requests_api_returns_pending_oldest_first():
    """Reads the `config_change_requests` table directly (no daemon
    publish/mirror step, unlike live-runs/PR-queue/run-ledger) — only
    pending rows for the account's own repos, oldest first, with an
    approve_url built from the request's own id."""
    from datetime import datetime, timedelta, timezone

    from brnrd.models import ConfigChangeRequest

    client = _client(public_base_url="https://brnrd.example")
    token = _login(client)
    pid = _create_repo(client, token)

    now = datetime.now(timezone.utc)
    with client.app.state.SessionLocal() as db:
        account_id = db.get(Repo, pid).account_id
        newer = ConfigChangeRequest(
            id="ccr-newer", account_id=account_id, repo_id=pid,
            proposal_id="prop-newer", config_key="spawn.max_concurrent",
            current_value="4", requested_value="8", reason="burst of ranked work",
            status=ConfigChangeRequest.STATUS_PENDING,
            created_at=now, expires_at=now + timedelta(days=7),
        )
        older = ConfigChangeRequest(
            id="ccr-older", account_id=account_id, repo_id=pid,
            proposal_id="prop-older", config_key="spawn.max_concurrent",
            current_value="2", requested_value="4", reason="",
            status=ConfigChangeRequest.STATUS_PENDING,
            created_at=now - timedelta(hours=1), expires_at=now + timedelta(days=6),
        )
        decided = ConfigChangeRequest(
            id="ccr-decided", account_id=account_id, repo_id=pid,
            proposal_id="prop-decided", config_key="spawn.max_concurrent",
            current_value="4", requested_value="6", reason="",
            status=ConfigChangeRequest.STATUS_APPROVED,
            created_at=now - timedelta(hours=2), expires_at=now + timedelta(days=5),
        )
        db.add_all([newer, older, decided])
        db.commit()

    r = client.get("/v1/dashboard/config-requests")
    assert r.status_code == 200
    body = r.json()
    assert [row["id"] for row in body["requests"]] == ["ccr-older", "ccr-newer"]
    assert body["requests"][0]["config_key"] == "spawn.max_concurrent"
    assert body["requests"][0]["approve_url"] == "https://brnrd.example/config-approve/ccr-older"


def test_dashboard_can_issue_telegram_pair_link():
    client = _client(telegram_bot_username="@brnrd_bot")
    token = _login(client, login="Gurio")
    repo_id = _create_repo(client, token)

    r = client.post(f"/v1/repos/{repo_id}/telegram-pair")

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["pairing_code"].startswith("TG-")
    assert body["action_url"].startswith("https://t.me/brnrd_bot?start=TG-")
    assert "Open https://t.me/brnrd_bot?start=TG-" in body["instructions"]

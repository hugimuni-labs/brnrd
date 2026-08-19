"""Tests for the brnrd dashboard repo view."""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402

from _helpers import PUBLISH_EVERYTHING  # noqa: E402
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
        json={"repo_full_name": repo, "default_branch": "main", "publish_layers": PUBLISH_EVERYTHING},
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


def test_the_backend_does_not_claim_the_repos_path():
    """#847: the `/repos` 308 shim is gone — see test_spa_serving.py.

    `src/frontend/src/routes/repos/` is the real page. A backend route on the
    same path now beats it, so its absence is what keeps the deep link alive.
    """
    client = _client()
    assert "repos" not in client.app.state.backend_namespaces


@pytest.mark.parametrize(
    ("method", "path", "json_body"),
    [
        ("get", "/v1/dashboard/repos", None),
        ("post", "/v1/repos/connect", {"repo_full_name": "Gurio/brr"}),
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
    # The retired "enable" button's replacement (2026-08-06): an installed,
    # not-yet-connected repo now carries its own setup command — running it
    # from that checkout is what connects it, no website click first.
    assert installed["Gurio/new"]["setup_command"].startswith("cd new\n")
    assert body["github_app_slug"] == "brnrd-dev"
    assert body["oauth_ready"] is True


def test_dashboard_repos_api_carries_the_machines_summary():
    """design-machines-and-guests.md R1 / #1365 — the `machines` summary
    additive on this payload, feeding ColdStart's account-level gate.

    A daemon paired with `repo_id is None` (the exact #1365 fixture: a
    freshly paired machine, no repo enabled yet) must flip `paired` true
    while `any_enabled_repo` stays false — that's precisely the state the
    old repo-scoped-only gate couldn't see.
    """
    from brnrd.models import Daemon

    client = _client()
    token = _login(client, login="Gurio")
    account_id = _account_id(client)

    cold = client.get("/v1/dashboard/repos").json()
    assert cold["machines"] == {"paired": False, "any_enabled_repo": False}

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

    paired_no_repo = client.get("/v1/dashboard/repos").json()
    assert paired_no_repo["machines"] == {"paired": True, "any_enabled_repo": False}

    repo_id = _create_repo(client, token, repo="Gurio/brr")
    with client.app.state.SessionLocal() as db:
        daemon = db.get(Daemon, "dmn-zero-repo")
        daemon.repo_id = repo_id
        db.commit()

    warm = client.get("/v1/dashboard/repos").json()
    assert warm["machines"] == {"paired": True, "any_enabled_repo": True}


def test_dashboard_repos_api_serves_one_pairing_command_to_a_cold_account():
    """The cold-start block's command and a repo's ``setup_command`` are one string.

    The dashboard's cold-start block prints the pairing lines to an account
    with *no* repos — which is precisely when there is no repo row to read
    ``setup_command`` off, so the payload carries an account-level spelling
    with a ``<repo>`` placeholder. Both come from ``_session.pairing_command``
    and this pins that they stay one fact: they may differ in the ``cd``
    target and in nothing else. Two hand-typed copies of a command line drift
    apart the first time the CLI renames a verb, and the copy on the surface
    a brand-new reader sees is the worse one to have gone stale.
    """
    client = _client()
    token = _login(client, login="Gurio")

    cold = client.get("/v1/dashboard/repos").json()
    assert cold["connected_repos"] == []
    # One word, not a spelled verb (2026-08-14, the iMac trace): the served
    # command is the bare-`brnrd` guided front door, which announces and
    # runs `account connect` itself and survives every CLI verb rename by
    # construction — the drift this endpoint exists to prevent, measured
    # live when this string taught a retired spelling to a fresh machine.
    assert cold["pairing_command"].splitlines() == [
        "cd <repo>",
        "brnrd",
    ]

    _create_repo(client, token, repo="Gurio/brr")
    warm = client.get("/v1/dashboard/repos").json()
    setup = warm["connected_repos"][0]["setup_command"].splitlines()
    assert setup[0] == "cd brr"
    assert setup[1:] == cold["pairing_command"].splitlines()[1:]


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


def test_dashboard_repos_api_reports_offline_for_a_daemon_that_has_really_run():
    """A daemon that completed a publish cycle and went quiet reads `offline`.

    "Local daemon not running" is an accurate claim about a real heartbeat
    that stopped, not a lie about one that never happened (#1243, the
    counterpart to the `never_started` test below).
    """
    from datetime import datetime, timedelta, timezone

    from brnrd.models import Daemon

    client = _client()
    token = _login(client, login="Gurio")
    repo_id = _create_repo(client, token, repo="Gurio/brr")
    stale = datetime.now(timezone.utc) - timedelta(minutes=10)
    with client.app.state.SessionLocal() as db:
        db.add(
            Daemon(
                id="dmn-offline-real",
                repo_id=repo_id,
                token_id="tok-offline-real",
                daemon_name="laptop",
                online=True,
                last_seen_at=stale,
                runners_updated_at=stale,
            )
        )
        db.commit()

    body = client.get("/v1/dashboard/repos").json()

    row = body["connected_repos"][0]
    assert row["daemon_status"] == "offline"
    assert row["daemon_label"] == "Local daemon not running"


def test_dashboard_repos_api_distinguishes_a_daemon_that_registered_and_never_ran():
    """#1243 — `last_seen_at` is stamped at registration, not at a daemon's

    first successful publish cycle (``daemons.py``'s ``register()``). A
    daemon that paired and then crash-looped without ever completing one
    (`daemon.start` refuses without `AGENTS.md`, #1238's exact trace) must
    not read as `offline` — that label's copy ("Local daemon not running"
    beside "Last heartbeat Xm ago") would claim a heartbeat this daemon
    never actually sent. `runners_updated_at` (written only from inside the
    publish loop a crash-looping process never reaches) is the fact that
    tells the two states apart.
    """
    from datetime import datetime, timedelta, timezone

    from brnrd.models import Daemon

    client = _client()
    token = _login(client, login="Gurio")
    repo_id = _create_repo(client, token, repo="Gurio/brr")
    registered = datetime.now(timezone.utc) - timedelta(minutes=3)
    with client.app.state.SessionLocal() as db:
        db.add(
            Daemon(
                id="dmn-never-started",
                repo_id=repo_id,
                token_id="tok-never-started",
                daemon_name="laptop",
                online=True,
                last_seen_at=registered,
                runners_updated_at=None,
            )
        )
        db.commit()

    body = client.get("/v1/dashboard/repos").json()

    row = body["connected_repos"][0]
    assert row["daemon_status"] == "never_started"
    assert row["daemon_label"] != "Local daemon not running"
    assert row["daemon_label"] == "Paired, never started"


def test_dashboard_repos_api_never_syncs_installations_inline(monkeypatch):
    """#885: installation sync used to run inline on this GET — a paginated
    App-API repo listing plus per-repo collaborator checks, the reported
    ~minute page load. It must now happen on a background thread with its
    own DB session, never blocking the response."""
    client = _client(
        github_app_id="123",
        github_app_private_key_b64="cHJpdmF0ZSBrZXk=",
    )
    token = _login(client, login="Gurio")
    _create_repo(client, token)

    calls: list[tuple[threading.Thread, str]] = []
    started = threading.Event()

    def fake_sync(db, settings, account_id):
        calls.append((threading.current_thread(), account_id))
        started.set()

    monkeypatch.setattr(
        "brnrd.routers._session.sync_app_installations_for_account", fake_sync
    )

    r = client.get("/v1/dashboard/repos")

    assert r.status_code == 200
    assert r.json()["notice"] is None
    assert started.wait(timeout=2), "background sync never ran"
    assert len(calls) == 1
    thread, account_id = calls[0]
    assert thread is not threading.current_thread()
    assert account_id == _account_id(client)


def test_dashboard_repos_api_background_sync_is_single_flight(monkeypatch):
    """A second GET while a sync is already running for the account must not
    start a second one (#885 single-flight)."""
    client = _client(
        github_app_id="123",
        github_app_private_key_b64="cHJpdmF0ZSBrZXk=",
    )
    token = _login(client, login="Gurio")
    _create_repo(client, token)

    entered = threading.Event()
    release = threading.Event()
    call_count = 0
    lock = threading.Lock()

    def fake_sync(db, settings, account_id):
        nonlocal call_count
        with lock:
            call_count += 1
        entered.set()
        release.wait(timeout=2)

    monkeypatch.setattr(
        "brnrd.routers._session.sync_app_installations_for_account", fake_sync
    )

    r1 = client.get("/v1/dashboard/repos")
    assert r1.status_code == 200
    assert entered.wait(timeout=2), "first background sync never started"

    r2 = client.get("/v1/dashboard/repos")
    assert r2.status_code == 200

    release.set()
    # Give the in-flight thread a moment to clear the in-progress marker.
    import time

    time.sleep(0.05)
    assert call_count == 1


def test_dashboard_repos_api_reports_telegram_paired_state():
    """#885: `telegram_paired` reflects a real `ChannelRoute` row with a
    non-NULL `paired_user_id` — a route with a NULL principal authorizes
    nobody (models.py ~line 339) and must read as unpaired, same as no route
    at all."""
    from brnrd.models import ChannelRoute

    client = _client()
    token = _login(client, login="Gurio")
    paired_repo_id = _create_repo(client, token, repo="Gurio/paired")
    null_principal_repo_id = _create_repo(client, token, repo="Gurio/null-principal")
    _create_repo(client, token, repo="Gurio/no-route")
    account_id = _account_id(client)

    with client.app.state.SessionLocal() as db:
        db.add(
            ChannelRoute(
                id="cr-paired",
                platform="telegram",
                channel_id="chat-1",
                account_id=account_id,
                repo_id=paired_repo_id,
                paired_user_id=555,
            )
        )
        db.add(
            ChannelRoute(
                id="cr-null-principal",
                platform="telegram",
                channel_id="chat-2",
                account_id=account_id,
                repo_id=null_principal_repo_id,
                paired_user_id=None,
            )
        )
        db.commit()

    r = client.get("/v1/dashboard/repos")

    assert r.status_code == 200
    by_name = {row["repo_full_name"]: row for row in r.json()["connected_repos"]}
    assert by_name["Gurio/paired"]["telegram_paired"] is True
    assert by_name["Gurio/null-principal"]["telegram_paired"] is False
    assert by_name["Gurio/no-route"]["telegram_paired"] is False
    assert "telegram_pair_enabled" not in by_name["Gurio/paired"]


def test_dashboard_repos_api_carries_the_channel_directory_onto_the_wire():
    """The branch is named *reaches the wire*, and this is the wire.

    `_repo_views` assembling `channels` is not the same as a client seeing
    it: `dashboard.py`'s `_repo_view_out` builds an **explicit** dict, so a
    key added upstream is dropped here unless someone adds it here too.
    That is the whole failure class this branch exists to close, one layer
    up — a correct classifier behind a serializer that never learned about
    it (#786). Asserted through the HTTP round trip on purpose; the
    `_repo_views`-level test below cannot see this gap by construction.

    Three states, all distinguishable by a reader of the payload:
    a paired non-Telegram route, a route with a NULL principal (#885 — it
    authorizes nobody, so `paired: false`), and a repo with no route at all
    (absent from its list entirely, which is *not* the same as unpaired).
    """
    from brnrd.models import ChannelRoute

    client = _client()
    token = _login(client, login="Gurio")
    wa_repo_id = _create_repo(client, token, repo="Gurio/wire-wa")
    null_repo_id = _create_repo(client, token, repo="Gurio/wire-null")
    _create_repo(client, token, repo="Gurio/wire-none")
    account_id = _account_id(client)

    with client.app.state.SessionLocal() as db:
        db.add(
            ChannelRoute(
                id="cr-wire-wa",
                platform="whatsapp",
                channel_id="wa-1",
                account_id=account_id,
                repo_id=wa_repo_id,
                paired_user_id=777,
            )
        )
        db.add(
            ChannelRoute(
                id="cr-wire-null",
                platform="whatsapp",
                channel_id="wa-2",
                account_id=account_id,
                repo_id=null_repo_id,
                paired_user_id=None,
            )
        )
        db.commit()

    r = client.get("/v1/dashboard/repos")

    assert r.status_code == 200
    by_name = {row["repo_full_name"]: row for row in r.json()["connected_repos"]}
    assert "channels" in by_name["Gurio/wire-wa"], (
        "the channel directory never reached the payload — `_repo_view_out` "
        "builds an explicit dict and drops any key it was not told about"
    )
    assert by_name["Gurio/wire-wa"]["channels"] == [
        {"platform": "whatsapp", "paired": True}
    ]
    assert by_name["Gurio/wire-null"]["channels"] == [
        {"platform": "whatsapp", "paired": False}
    ], "a NULL principal authorizes nobody (#885), on every platform"
    assert by_name["Gurio/wire-none"]["channels"] == [], (
        "no route at all is an empty directory, not a fabricated unpaired row"
    )
    # The deprecated bit stays derived from the directory rather than tracked
    # beside it, so the two cannot disagree: a whatsapp-only repo is not
    # telegram-paired.
    assert by_name["Gurio/wire-wa"]["telegram_paired"] is False


def test_repo_views_channel_directory_carries_a_paired_whatsapp_route():
    """brr/the-directory-reaches-the-wire: `ChannelRoute` already carries a
    `platform` column and rows are written for more than Telegram
    (`webhooks.py` writes `platform="whatsapp"` too, `_handle_whatsapp_pair`)
    — `_repo_views`' `channels` must report the whole directory, not narrow
    every platform down to one Telegram-shaped boolean the way the old
    `_telegram_paired_repo_ids` did. #885's NULL-`paired_user_id`-means-
    unpaired rule is a pairing predicate, not a Telegram one, so it must
    hold identically on a non-Telegram platform too.

    Exercised as a direct call to `_repo_views` (not the `/v1/dashboard/repos`
    HTTP round trip the sibling `telegram_paired` test above uses) — this
    branch is scoped to `_session.py`/`capabilities.py`; forwarding
    `channels` onto the actual wire response is `dashboard.py`'s
    `_repo_view_out`, out of scope here (see the PR body / report)."""
    from sqlalchemy import select

    from brnrd.models import ChannelRoute, Repo
    from brnrd.routers._session import _repo_views

    client = _client()
    token = _login(client, login="Gurio")
    wa_paired_repo_id = _create_repo(client, token, repo="Gurio/wa-paired")
    wa_null_principal_repo_id = _create_repo(client, token, repo="Gurio/wa-null-principal")
    no_route_repo_id = _create_repo(client, token, repo="Gurio/no-route")
    account_id = _account_id(client)

    # A second account's paired route must never leak into the first
    # account's directory — `_account_channel_directory` is account-scoped,
    # not global.
    other_token = _login(client, github_id="99999", login="Other")
    other_repo_id = _create_repo(client, other_token, repo="Other/repo")
    other_account_id = _account_id(client, login="Other")

    with client.app.state.SessionLocal() as db:
        db.add(
            ChannelRoute(
                id="cr-wa-paired",
                platform="whatsapp",
                channel_id="wa-chat-1",
                account_id=account_id,
                repo_id=wa_paired_repo_id,
                paired_user_id=999,
            )
        )
        db.add(
            ChannelRoute(
                id="cr-wa-null-principal",
                platform="whatsapp",
                channel_id="wa-chat-2",
                account_id=account_id,
                repo_id=wa_null_principal_repo_id,
                paired_user_id=None,
            )
        )
        db.add(
            ChannelRoute(
                id="cr-other-account",
                platform="whatsapp",
                channel_id="wa-chat-3",
                account_id=other_account_id,
                repo_id=other_repo_id,
                paired_user_id=1,
            )
        )
        db.commit()

    with client.app.state.SessionLocal() as db:
        repos = list(
            db.execute(
                select(Repo).where(
                    Repo.id.in_([wa_paired_repo_id, wa_null_principal_repo_id, no_route_repo_id])
                )
            ).scalars()
        )
        views = {v["repo"].id: v for v in _repo_views(db, repos)}

    assert views[wa_paired_repo_id]["channels"] == [{"platform": "whatsapp", "paired": True}]
    # #885, generalised: a NULL principal is not paired, on any platform.
    assert views[wa_null_principal_repo_id]["channels"] == [{"platform": "whatsapp", "paired": False}]
    # Never attempted on any platform -> absent from the directory entirely,
    # distinct from "attempted, not yet paired".
    assert views[no_route_repo_id]["channels"] == []
    # The compatibility alias stays keyed on Telegram specifically; none of
    # these routes are Telegram, so it stays False for all three.
    assert views[wa_paired_repo_id]["telegram_paired"] is False
    assert views[wa_null_principal_repo_id]["telegram_paired"] is False
    assert views[no_route_repo_id]["telegram_paired"] is False


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


def test_dashboard_quota_staleness_carries_a_dated_last_known_measurement():
    """A stale measurement remains distinct from both a live reading and a
    window that has never been measured: live fields stay null, while the
    collector's values survive under an explicit shell scrape timestamp."""
    import json
    from datetime import datetime, timedelta, timezone

    from brnrd.models import Daemon
    from brnrd.routers.dashboard import _quota_views

    client = _client()
    token = _login(client)
    pid = _create_repo(client, token)
    stale_scrape_at = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    measured = {
        "label": "weekly",
        "used": 44.0,
        "limit": 100.0,
        "percent": 56.0,
        "reset": "resets Sunday",
        "resets_at": 1_786_320_000.0,
    }

    with client.app.state.SessionLocal() as db:
        repo = db.get(Repo, pid)
        db.add(
            Daemon(
                id="dmn-quota-last-known",
                repo_id=pid,
                token_id="tok-quota-last-known",
                daemon_name="laptop",
                quota_json=json.dumps(
                    [
                        {
                            "shell": "codex",
                            "status": "known",
                            "updated_at": stale_scrape_at,
                            "windows": [measured],
                        }
                    ]
                ),
                quota_updated_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

        stale = _quota_views(db, [repo], runner_stats=[])[0]

    assert stale["status"] == "stale"
    assert stale["as_of"] == stale_scrape_at
    assert stale["windows"][0]["percent"] is None
    assert stale["windows"][0]["last_known"] == {
        key: measured[key] for key in ("used", "limit", "percent", "reset", "resets_at")
    }


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


def test_dashboard_live_runs_api_round_trips_claimed_topics():
    """The `topics` field (the-run-that-claims-its-thread) survives the
    server: the daemon's presence heartbeat publishes raw slugs, and the
    dashboard reads them back off the stored snapshot — the whitelist
    (`LiveRunIn`) must carry the member or the wire silently drops it,
    which is exactly how the live lane rendered topicless for a run whose
    claim the daemon was already publishing."""
    import json
    from datetime import datetime, timezone

    from brnrd.models import Daemon

    client = _client()
    token = _login(client)
    pid = _create_repo(client, token)

    with client.app.state.SessionLocal() as db:
        daemon = Daemon(
            id="dmn-live-t", repo_id=pid, token_id="tok-live-t", daemon_name="laptop",
            live_runs_json=json.dumps(
                [
                    {"id": "pres-t", "kind": "daemon", "stream": "telegram:x:",
                     "run_id": "run-threaded", "repo_label": "Gurio/brr",
                     "topics": ["the-loom", "the-post"]},
                    {"id": "pres-u", "kind": "daemon", "stream": "telegram:y:",
                     "run_id": "run-unclaimed", "repo_label": "Gurio/brr"},
                ]
            ),
            live_runs_updated_at=datetime.now(timezone.utc),
        )
        db.add(daemon)
        db.commit()

    r = client.get("/v1/dashboard/live-runs")
    assert r.status_code == 200
    rows = {row["run_id"]: row for row in r.json()["runs"]}
    assert rows["run-threaded"]["topics"] == ["the-loom", "the-post"]
    # A snapshot stored before this field existed serves no key at all;
    # a validated unclaimed row serves []. Both read as "no claim" — the
    # frontend type is optional for exactly this reason.
    assert rows["run-unclaimed"].get("topics", []) == []

    # The PUT-side whitelist is the half that silently dropped the field
    # before this change — prove the isolate keeps it, not just the
    # stored-snapshot passthrough above.
    from brnrd import schemas as _schemas

    intake = _schemas.isolate_live_runs(
        [{"id": "pres-w", "run_id": "run-w", "topics": ["the-loom"]}]
    )
    assert intake.runs[0].topics == ["the-loom"]
    assert _schemas.isolate_live_runs([{"id": "pres-x", "run_id": "run-x"}]).runs[0].topics == []


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
        json={"repo_id": repo_id, "approve_secret": pair["approve_secret"]},
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
    assert one_day.json()["span_seconds_served"] == 86400

    seven_days = client.get("/v1/dashboard/run-ledger?limit=10&span_seconds=604800")
    assert [row["run_id"] for row in seven_days.json()["rows"]] == ["recent", "older"]
    assert seven_days.json()["span_seconds_served"] == 604800


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
    assert body["pairing_code"].startswith("PK-")
    assert body["action_url"].startswith("https://t.me/brnrd_bot?start=PK-")
    assert "Open https://t.me/brnrd_bot?start=PK-" in body["instructions"]


def test_dashboard_disconnect_purges_repo_rows_and_last_repo_clears_surface():
    """#502: disconnect must not 500 on repo-FK rows, and the corpus mirror
    must not outlive the account's last connection."""
    from datetime import datetime, timedelta, timezone

    from brnrd import ids
    from brnrd.models import ActivityRecord, ConfigChangeRequest, Token

    client = _client()
    token = _login(client, login="Gurio")
    first = _create_repo(client, token, repo="Gurio/one")
    second = _create_repo(client, token, repo="Gurio/two")
    with client.app.state.SessionLocal() as db:
        account = db.query(Account).filter(Account.github_login == "Gurio").one()
        account_id = account.id
        account.surface_json = '[{"path": "surface/index.md"}]'
        tok = db.query(Token).first()
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        db.add(
            ActivityRecord(
                id=ids.activity_id(), repo_id=first, token_id=tok.id, record_id="run:x"
            )
        )
        db.add(
            ConfigChangeRequest(
                id="ccr-1",
                account_id=account_id,
                repo_id=first,
                proposal_id="p-1",
                config_key="spawn.max_concurrent",
                expires_at=expires,
            )
        )
        db.commit()

    r = client.post(f"/v1/repos/{first}/disconnect")
    assert r.status_code == 200, r.text
    with client.app.state.SessionLocal() as db:
        assert db.get(Repo, first) is None
        assert db.query(ActivityRecord).count() == 0
        assert db.query(ConfigChangeRequest).count() == 0
        # Another repo still connected: the account corpus stays.
        assert db.get(Account, account_id).surface_json != "[]"

    r = client.post(f"/v1/repos/{second}/disconnect")
    assert r.status_code == 200, r.text
    with client.app.state.SessionLocal() as db:
        assert db.get(Account, account_id).surface_json == "[]"


def test_live_runs_mood_round_trips_daemon_to_dashboard():
    """#566 slice 0, server half: per-run mood fields survive `LiveRunIn`
    intake, and the daemon-level face is stored and served beside
    `spawn_max_concurrent` on the same envelope."""
    import json
    from datetime import datetime, timezone

    from brnrd.models import Daemon

    client = _client()
    token = _login(client)
    pid = _create_repo(client, token)

    with client.app.state.SessionLocal() as db:
        daemon = Daemon(
            id="dmn-mood", repo_id=pid, token_id="tok-mood", daemon_name="laptop",
            live_runs_json=json.dumps([
                {
                    "id": "pres-m", "kind": "daemon", "stream": "telegram:x:",
                    "label": "primary", "run_id": "run-m", "repo_label": "Gurio/brr",
                    "started_at": "2026-07-24T00:00:00Z",
                    "last_seen": "2026-07-24T00:05:00Z", "is_subspawn": False,
                    "mood": "fo.cus", "mood_glyph": "b·_·d", "mood_pitch": 0.45,
                },
            ]),
            live_runs_updated_at=datetime.now(timezone.utc),
            daemon_mood_json=json.dumps({
                "state": "running", "name": "rnn>", "glyph": "brnrd",
                "frames": ["brnrd", "brvrd", "brnrd"], "pitch": 0.5,
            }),
        )
        db.add(daemon)
        db.commit()

    r = client.get("/v1/dashboard/live-runs")
    assert r.status_code == 200
    body = r.json()
    row = next(run for run in body["runs"] if run["run_id"] == "run-m")
    assert row["mood"] == "fo.cus"
    assert row["mood_glyph"] == "b·_·d"
    assert row["mood_pitch"] == 0.45
    assert body["daemon_mood"]["state"] == "running"
    assert body["daemon_mood"]["glyph"] == "brnrd"


def test_put_live_runs_stores_mood_fields():
    """Intake half: `LiveRunIn` must carry the new mood fields (an unknown
    field is dropped by `model_dump`, so schema presence is the load-bearing
    fact) and `daemon_mood` must persist on the daemon row."""
    from brnrd import schemas

    run = schemas.LiveRunIn(
        id="pres-1", mood="fo.cus", mood_glyph="b·_·d", mood_pitch=0.45,
    )
    dumped = run.model_dump()
    assert dumped["mood"] == "fo.cus"
    assert dumped["mood_glyph"] == "b·_·d"
    assert dumped["mood_pitch"] == 0.45

    report = schemas.LiveRunsReport(
        runs=[run],
        daemon_mood={
            "state": "idle", "name": "id_l", "glyph": "brnrd",
            "frames": ["brnrd", "b-n-d", "brnrd"], "pitch": 0.5,
        },
    )
    assert report.daemon_mood.state == "idle"
    assert report.daemon_mood.frames == ["brnrd", "b-n-d", "brnrd"]


# --- #685 ask 2: one bad row must cost one row -------------------------------
#
# The card (#722) was the instance; this is the class. `PUT /v1/daemons/live-runs`
# publishes every live run on a daemon in one request, so while `LiveRunsReport.runs`
# was a typed `list[LiveRunIn]`, FastAPI rejected the whole body before the handler
# ran and the daemon re-attempted the doomed PUT every 3 seconds
# (`src/brr/gates/cloud.py::_DASHBOARD_PUBLISH_INTERVAL_S`) — a continuous outage of
# every run's live surface, caused by one over-long field on one row.


def _daemon_client():
    """A paired daemon's client + headers + repo id, for the live-runs lane."""
    client = _client()
    account_token = _login(client)
    repo_id = _create_repo(client, account_token)
    account_headers = {"Authorization": f"Bearer {account_token}"}
    pair = client.post("/v1/accounts/pair").json()
    client.post(
        f"/v1/accounts/pair/{pair['pair_code']}/approve",
        json={"repo_id": repo_id, "approve_secret": pair["approve_secret"]},
        headers=account_headers,
    )
    paired = client.get(
        f"/v1/accounts/pair/{pair['pair_code']}",
        params={"poll_secret": pair["poll_secret"]},
    ).json()
    headers = {"Authorization": f"Bearer {paired['daemon_token']}"}
    client.post("/v1/daemons/register", json={"daemon_name": "laptop"}, headers=headers)
    return client, headers, repo_id


def _stored_live_runs(client, repo_id):
    """The rows the server actually *stored* — the only honest assertion here.

    A 200 that stored nothing is the same outage with a friendlier face, so
    every test below reads the persisted snapshot rather than the status code.
    """
    import json

    from brnrd.models import Daemon

    with client.app.state.SessionLocal() as db:
        daemon = db.query(Daemon).filter(Daemon.repo_id == repo_id).one()
        return json.loads(daemon.live_runs_json or "[]")


def test_one_unparseable_live_run_row_costs_one_row():
    """The driven repro from #685 ask 2, as a test.

    Two innocent rows and one whose *identity* is unusable: the two must be
    stored. Before per-row isolation this PUT was a 422 and the stored snapshot
    stayed at whatever the last good publish left — every live run dark.
    """
    client, headers, repo_id = _daemon_client()
    r = client.put(
        "/v1/daemons/live-runs",
        json={
            "runs": [
                {"id": "run-a", "repo_label": "Gurio/brr"},
                {"id": "run-b", "repo_label": "Gurio/brr"},
                {"id": "z" * 65, "repo_label": "Gurio/brr"},
            ]
        },
        headers=headers,
    )
    assert r.status_code == 200
    stored = _stored_live_runs(client, repo_id)
    assert [row["id"] for row in stored] == ["run-a", "run-b"]


def test_an_over_long_identity_key_costs_its_own_row_and_no_other():
    """Identity keys reject rather than truncate — a shortened join key is
    *wrong data*, not short data, and would silently re-point the row at
    another run. So the isolation has to be real, not cosmetic: the bad row is
    gone and its neighbours on both sides survive intact."""
    client, headers, repo_id = _daemon_client()
    r = client.put(
        "/v1/daemons/live-runs",
        json={
            "runs": [
                {"id": "keep-1", "run_id": "run-1", "repo_label": "Gurio/brr"},
                {"id": "drop-me", "run_id": "r" * 65, "repo_label": "Gurio/brr"},
                {"id": "keep-2", "run_id": "run-2", "repo_label": "Gurio/brr"},
            ]
        },
        headers=headers,
    )
    assert r.status_code == 200
    stored = _stored_live_runs(client, repo_id)
    assert [row["id"] for row in stored] == ["keep-1", "keep-2"]
    assert [row["run_id"] for row in stored] == ["run-1", "run-2"]
    # And the loss is *said*, not left for a reader to infer from a row that
    # quietly stopped appearing (#632).
    rejected = r.json()["runs_rejected"]
    assert [(row["id"], row["fields"]) for row in rejected] == [("drop-me", ["run_id"])]


def test_over_long_display_field_is_stored_truncated_and_marked():
    """`name` is the `.card`-neighbour control file `.name` — resident-authored
    prose whose cap is stated only as an instruction. One character over used to
    darken the dashboard for every run on the daemon; now it costs the tail of
    one string, and the mark is what tells a reader the value continues rather
    than merely ends."""
    from brnrd import schemas

    client, headers, repo_id = _daemon_client()
    r = client.put(
        "/v1/daemons/live-runs",
        json={"runs": [{"id": "run-a", "name": "x" * 61, "repo_label": "Gurio/brr"}]},
        headers=headers,
    )
    assert r.status_code == 200
    stored = _stored_live_runs(client, repo_id)
    assert len(stored) == 1
    assert len(stored[0]["name"]) == 60
    assert stored[0]["name"].endswith(schemas.LIVE_RUN_TRUNCATION_MARK)
    assert stored[0]["name"].startswith("x" * 59)
    assert r.json()["fields_truncated"] == ["run-a.name"]


def test_a_display_field_at_the_cap_is_stored_byte_identical():
    """The other direction, and the reason the test above is not enough on its
    own: a field that is *always* truncated passes the long case perfectly. At
    exactly the cap nothing may be cut and nothing may be marked."""
    client, headers, repo_id = _daemon_client()
    at_cap_name = "n" * 60
    at_cap_card = "c" * 4096
    r = client.put(
        "/v1/daemons/live-runs",
        json={
            "runs": [{
                "id": "run-a",
                "name": at_cap_name,
                "card_text": at_cap_card,
                "repo_label": "Gurio/brr",
            }]
        },
        headers=headers,
    )
    assert r.status_code == 200
    stored = _stored_live_runs(client, repo_id)
    assert stored[0]["name"] == at_cap_name
    assert stored[0]["card_text"] == at_cap_card
    assert r.json()["fields_truncated"] == []


def test_live_run_bounds_fixture_matches_the_model():
    """The fixture table is *generated* from `LiveRunIn` (#723).

    `src/brr` cannot import `src/brnrd` — they ship separately — so the
    daemon-side bound in `cloud.py` is a deliberate duplication. A parity test
    between the two implementations would be the wrong pin: #722's four
    implementations of one rule agreed with each other perfectly for the bug's
    whole life. Both copies are pinned to this table instead, and this test is
    what makes the table honest: change a `max_length` on `LiveRunIn` without
    regenerating the fixture and it reddens here.

    `mood_frames` is deliberately absent — its `max_length=4` counts
    *sequences*, not characters, and a table that conflated the two would tell
    the daemon to truncate a list of faces to four letters.
    """
    import json
    from pathlib import Path

    from brnrd import schemas

    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "live_run_bounds.json").read_text(
            encoding="utf-8"
        )
    )
    regenerate = (
        "regenerate with:\n"
        "  python -c \"import json,pathlib;from brnrd import schemas;"
        "p=pathlib.Path('tests/fixtures/live_run_bounds.json');"
        "d=json.loads(p.read_text());"
        "d['string_bounds']=schemas.LiveRunIn.string_bounds();"
        "d['identity_fields']=sorted(schemas.LIVE_RUN_IDENTITY_FIELDS);"
        "d['truncation_mark']=schemas.LIVE_RUN_TRUNCATION_MARK;"
        "p.write_text(json.dumps(d,indent=2,ensure_ascii=False)+chr(10))\"\n"
        "then update `_LIVE_RUN_STRING_BOUNDS` in src/brr/gates/cloud.py to match."
    )
    assert fixture["string_bounds"] == schemas.LiveRunIn.string_bounds(), regenerate
    assert fixture["identity_fields"] == sorted(schemas.LIVE_RUN_IDENTITY_FIELDS), regenerate
    assert fixture["truncation_mark"] == schemas.LIVE_RUN_TRUNCATION_MARK, regenerate
    assert "mood_frames" not in fixture["string_bounds"]


def test_every_bounded_display_string_truncates_rather_than_rejects():
    """The positive control for the split, driven over the model's own field
    list rather than a hand-written sample.

    A test naming three fields proves three fields. This walks
    `string_bounds()`, so a bounded display field added to `LiveRunIn` later is
    covered the moment it exists — the property this whole guard rests on.
    """
    from brnrd import schemas

    bounds = schemas.LiveRunIn.string_bounds()
    display = {f: c for f, c in bounds.items() if f not in schemas.LIVE_RUN_IDENTITY_FIELDS}
    assert display, "no display fields — the split has lost its subject"
    for field, cap in display.items():
        model = schemas.LiveRunIn.model_validate({"id": "run-a", field: "y" * (cap + 1)})
        value = getattr(model, field)
        assert len(value) == cap, f"{field} not truncated to its bound"
        assert value.endswith(schemas.LIVE_RUN_TRUNCATION_MARK), f"{field} truncated unmarked"
    for field in sorted(schemas.LIVE_RUN_IDENTITY_FIELDS):
        payload = {"id": "run-a", field: "y" * (bounds[field] + 1)}
        with pytest.raises(Exception):
            schemas.LiveRunIn.model_validate(payload)


def test_a_row_that_is_not_even_an_object_costs_one_row():
    """Found by breaking an existing test, not by predicting it.

    The first cut typed `runs` as `list[dict[str, Any]]`, which reads like
    isolation and is not: a `null` or a bare string in the list fails at the
    *report* level, before the handler runs, and 422s the whole batch — the
    exact class this change exists to close, reintroduced one type annotation
    up. The row type has to be unconstrained for per-row validation to be the
    only thing that can reject a row.
    """
    client, headers, repo_id = _daemon_client()
    r = client.put(
        "/v1/daemons/live-runs",
        json={
            "runs": [
                {"id": "run-a", "repo_label": "Gurio/brr"},
                None,
                "not a row at all",
                {"id": "run-b", "repo_label": "Gurio/brr"},
            ]
        },
        headers=headers,
    )
    assert r.status_code == 200
    assert [row["id"] for row in _stored_live_runs(client, repo_id)] == ["run-a", "run-b"]
    assert [row["index"] for row in r.json()["runs_rejected"]] == [1, 2]


def test_dashboard_runners_api_stamps_per_row_report_freshness():
    """#1500 "the rack of dead spools" — the account-wide `stale` flag is

    only ever as fresh as whichever daemon reported most recently
    (`_runners_views`'s single `newest` tracker), but rows merge into one
    dict *keyed by profile name* across every daemon on the account with no
    per-row expiry. A daemon that retired never gets its rows overwritten
    unless a live daemon happens to report a profile of the *same name* —
    so its rows can sit on the rack looking exactly as fresh as a daemon
    reporting every minute. `daemon_reported_at` / `daemon_stale` are the
    per-row facts that let a reader (the frontend rack) catch that even
    while the account-wide `stale` reads green.
    """
    import json
    from datetime import datetime, timedelta, timezone

    from brnrd.models import Daemon

    client = _client()
    token = _login(client, login="Gurio")
    repo_id = _create_repo(client, token, repo="Gurio/brr")
    now = datetime.now(timezone.utc)
    fresh = now - timedelta(seconds=5)
    retired = now - timedelta(days=5)
    with client.app.state.SessionLocal() as db:
        db.add(
            Daemon(
                id="dmn-fresh",
                repo_id=repo_id,
                token_id="tok-fresh",
                daemon_name="laptop",
                runners_updated_at=fresh,
                runners_default="claude",
                runners_json=json.dumps(
                    [{"name": "claude", "shell": "claude", "available": True}]
                ),
            )
        )
        db.add(
            Daemon(
                id="dmn-retired",
                repo_id=repo_id,
                token_id="tok-retired",
                daemon_name="old-machine",
                runners_updated_at=retired,
                runners_json=json.dumps(
                    [{"name": "codex-gpt-5.4", "shell": "codex", "available": True}]
                ),
            )
        )
        db.commit()

    body = client.get("/v1/dashboard/runners").json()

    # The account-wide chip reads fresh — the live daemon reported 5s ago —
    # exactly the case where a reader trusting only that flag gets fooled.
    assert body["stale"] is False

    by_name = {row["name"]: row for row in body["profiles"]}
    assert by_name["claude"]["daemon_stale"] is False
    assert by_name["codex-gpt-5.4"]["daemon_stale"] is True
    assert by_name["codex-gpt-5.4"]["available"] is True, (
        "still honestly reporting what the retired daemon last said — "
        "daemon_stale is what tells a reader not to trust it as current"
    )

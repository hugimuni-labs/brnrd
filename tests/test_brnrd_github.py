"""Tests for brnrd's managed GitHub webhook ingress."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from brnrd import create_app, ids  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.models import Event, GitHubInstallation, GitHubInstalledRepo, Repo  # noqa: E402
from brnrd.platforms import github_app as github_app_platform  # noqa: E402
from _helpers import brnrd_account_headers  # noqa: E402

_SECRET = "github-webhook-secret"


def test_installation_credential_request_is_restricted_to_repo(monkeypatch):
    seen = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"token": "ghs_one", "expires_at": "2099-01-01T00:00:00Z"}

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, *, headers, json):
            seen.update(url=url, headers=headers, json=json)
            return Response()

    monkeypatch.setattr(github_app_platform, "app_jwt", lambda settings: "jwt")
    monkeypatch.setattr(github_app_platform.httpx, "Client", Client)
    credential = github_app_platform.installation_access_credential(
        Settings(), "73", repository_ids=[4242],
    )

    assert credential == {"token": "ghs_one", "expires_at": "2099-01-01T00:00:00Z"}
    assert seen["json"] == {"repository_ids": [4242]}


def _build_env(monkeypatch, **extra_settings):
    posts: list[dict] = []

    def fake_post(token, api_base_url, api_version, repo, issue_number, body, *,
                  timeout=30.0):
        posts.append(
            {
                "token": token,
                "repo": repo,
                "issue_number": issue_number,
                "body": body,
            }
        )

    monkeypatch.setattr("brnrd.platforms.github.post_issue_comment", fake_post)
    monkeypatch.setattr(
        "brnrd.platforms.github.fetch_pull_head_ref",
        lambda *a, **k: "feature-x",
    )
    settings = Settings(
        database_url="sqlite:///:memory:",
        inbox_long_poll_max_s=0.2,
        inbox_poll_interval_s=0.02,
        github_webhook_secret=_SECRET,
        github_bot_login="brr-bot",
        github_bot_token="ghs_test",
        **extra_settings,
    )
    app = create_app(settings)
    return app, TestClient(app), posts


@pytest.fixture()
def env(monkeypatch):
    return _build_env(monkeypatch)


@pytest.fixture()
def env_allowlist(monkeypatch):
    # "alice" is the default commenter login in _payload() below.
    return _build_env(monkeypatch, github_authz_allowlist=("alice",))


def _account(client):
    return brnrd_account_headers(
        client.app, github_id="123", login="octocat", email="a@b.com"
    )


def _repo(client, headers, repo="owner/repo"):
    return client.post(
        "/v1/accounts/repos", json={"repo_full_name": repo}, headers=headers
    ).json()["repo_id"]


def _daemon_headers(client, acc, repo_id):
    pair = client.post("/v1/accounts/pair").json()
    client.post(
        f"/v1/accounts/pair/{pair['pair_code']}/approve",
        json={"repo_id": repo_id},
        headers=acc,
    )
    token = client.get(
        f"/v1/accounts/pair/{pair['pair_code']}",
        params={"poll_secret": pair["poll_secret"]},
    ).json()["daemon_token"]
    return {"Authorization": f"Bearer {token}"}


def test_daemon_mints_repo_scoped_app_publishing_credential(env, monkeypatch):
    app, client, _ = env
    acc = _account(client)
    repo_id = _repo(client, acc)
    daemon_headers = _daemon_headers(client, acc, repo_id)
    with app.state.SessionLocal() as db:
        repo = db.get(Repo, repo_id)
        repo.forge_repo_id = "4242"
        installation = GitHubInstallation(
            id=ids.github_installation_id(),
            account_id=repo.account_id,
            installation_id="73",
            target_login="owner",
            target_type="User",
        )
        db.add(installation)
        db.flush()
        db.add(
            GitHubInstalledRepo(
                id=ids.github_installed_repo_id(),
                github_installation_id=installation.id,
                repo_full_name=repo.repo_full_name,
                forge_repo_id="4242",
            )
        )
        db.commit()

    seen = {}

    def fake_credential(
        settings, installation_id, *, repository_ids=None, repositories=None,
    ):
        seen.update(
            installation_id=installation_id,
            repository_ids=repository_ids,
            repositories=repositories,
        )
        return {"token": "ghs_repo_scoped", "expires_at": "2099-01-01T00:00:00Z"}

    monkeypatch.setattr(
        "brnrd.routers.daemons.github_app_client.installation_access_credential",
        fake_credential,
    )
    response = client.post(
        "/v1/daemons/publishing-credential", headers=daemon_headers,
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "token": "ghs_repo_scoped",
        "expires_at": "2099-01-01T00:00:00Z",
        "login": "brnrd-dev[bot]",
    }
    assert seen == {
        "installation_id": "73",
        "repository_ids": [4242],
        "repositories": None,
    }


def _payload(*, repo="owner/repo", body="@brr-bot do the thing",
             installation_id=42, number=17, comment_id=100, is_pr=False,
             action="created", association="COLLABORATOR", author="alice"):
    issue = {"number": number, "title": "Work item"}
    if is_pr:
        issue["pull_request"] = {
            "url": f"https://api.github.com/repos/{repo}/pulls/{number}",
        }
    kind = "pull" if is_pr else "issues"
    return {
        "action": action,
        "installation": {"id": installation_id},
        "repository": {"full_name": repo},
        "issue": issue,
        "comment": {
            "id": comment_id,
            "body": body,
            "html_url": (
                f"https://github.com/{repo}/{kind}/{number}"
                f"#issuecomment-{comment_id}"
            ),
            "user": {"login": author},
            "author_association": association,
        },
    }


def _github_post(client, payload, *, event="issue_comment", secret=_SECRET):
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sig = "sha256=" + hmac.new(
        secret.encode("utf-8"), raw, hashlib.sha256
    ).hexdigest()
    return client.post(
        "/v1/webhooks/github",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": event,
            "X-Hub-Signature-256": sig,
        },
    )


def test_repo_create_list_is_idempotent(env):
    _, client, _ = env
    acc = _account(client)

    first = client.post(
        "/v1/accounts/repos",
        json={"repo_full_name": "owner/repo"},
        headers=acc,
    )
    assert first.status_code == 201, first.text
    second = client.post(
        "/v1/accounts/repos",
        json={"repo_full_name": "owner/repo"},
        headers=acc,
    )
    assert second.status_code == 201, second.text
    assert second.json()["repo_id"] == first.json()["repo_id"]

    listing = client.get("/v1/accounts/repos", headers=acc).json()
    assert len(listing["repos"]) == 1
    assert listing["repos"][0]["repo_full_name"] == "owner/repo"


def test_github_webhook_rejects_bad_signature(env):
    _, client, _ = env
    r = _github_post(client, _payload(), secret="wrong")
    assert r.status_code == 403


def test_unbound_repo_gets_setup_comment_without_enqueue(env):
    app, client, posts = env

    r = _github_post(client, _payload(repo="owner/unbound", number=5))
    assert r.status_code == 200

    with app.state.SessionLocal() as db:
        assert db.execute(select(Event)).scalars().all() == []
    assert len(posts) == 1
    assert posts[0]["repo"] == "owner/unbound"
    assert posts[0]["issue_number"] == 5
    assert "not connected" in posts[0]["body"]


def test_bound_pr_comment_enqueues_and_response_posts_back(env):
    app, client, posts = env
    acc = _account(client)
    rid = _repo(client, acc)

    r = _github_post(client, _payload(is_pr=True))
    assert r.status_code == 200

    with app.state.SessionLocal() as db:
        event = db.execute(select(Event).where(Event.source == "github")).scalar_one()
        assert event.repo_id == rid
        assert "@brr-bot do the thing" in (event.body or "")

    dmn = _daemon_headers(client, acc, rid)
    drained = client.get(
        "/v1/daemons/inbox", params={"since": 0, "wait": 0}, headers=dmn
    ).json()
    ev = drained["events"][0]
    assert ev["source"] == "github"
    assert ev["reply_to"] == {
        "platform": "github",
        "repo": "owner/repo",
        "issue_number": 17,
        "comment_id": 100,
        "kind": "pr-comment",
        "author": "alice",
        "html_url": "https://github.com/owner/repo/pull/17#issuecomment-100",
        "trigger": "mention",
        "mention": "@brr-bot",
        "pr_number": 17,
        "branch_target": "feature-x",
    }

    resp = client.post(
        "/v1/daemons/responses",
        json={
            "event_id": ev["event_id"],
            "body_markdown": "fixed on the branch",
            "status": "done",
        },
        headers=dmn,
    )
    assert resp.status_code == 200, resp.text
    assert len(posts) == 1
    assert posts[0]["repo"] == "owner/repo"
    assert posts[0]["issue_number"] == 17
    assert posts[0]["body"].startswith(
        "> Replying to [@alice's comment]"
        "(https://github.com/owner/repo/pull/17#issuecomment-100)"
    )
    assert posts[0]["body"].endswith("fixed on the branch")


def test_github_webhook_ignores_unaddressed_comments(env):
    app, client, posts = env
    acc = _account(client)
    _repo(client, acc)

    r = _github_post(client, _payload(body="plain repo chatter"))
    assert r.status_code == 200

    with app.state.SessionLocal() as db:
        assert db.execute(select(Event)).scalars().all() == []
    assert posts == []


# ── authorization gate (#408) ───────────────────────────────────────
#
# Default-closed: an autonomous run may only be enqueued for a comment
# whose author_association is OWNER/MEMBER/COLLABORATOR, or whose login
# is on the configured allowlist. Everything else is rejected — 200 to
# ack the webhook, no enqueue, no reply to the commenter.


def test_github_webhook_rejects_unauthorized_association(env):
    app, client, posts = env
    acc = _account(client)
    _repo(client, acc)

    r = _github_post(client, _payload(association="NONE"))
    assert r.status_code == 200

    with app.state.SessionLocal() as db:
        assert db.execute(select(Event)).scalars().all() == []
    assert posts == [], "no reply to the commenter on an authz rejection"


def test_github_webhook_allows_collaborator_association(env):
    app, client, posts = env
    acc = _account(client)
    rid = _repo(client, acc)

    r = _github_post(client, _payload(association="COLLABORATOR"))
    assert r.status_code == 200

    with app.state.SessionLocal() as db:
        event = db.execute(select(Event).where(Event.source == "github")).scalar_one()
        assert event.repo_id == rid


def test_github_webhook_allows_allowlisted_login_despite_none_association(env_allowlist):
    app, client, posts = env_allowlist
    acc = _account(client)
    rid = _repo(client, acc)

    r = _github_post(client, _payload(association="NONE"))
    assert r.status_code == 200

    with app.state.SessionLocal() as db:
        event = db.execute(select(Event).where(Event.source == "github")).scalar_one()
        assert event.repo_id == rid


def test_github_webhook_ignores_edited_comment_action(env):
    """Hard cutover: editing a comment must never (re)trigger a run —
    only the original 'created' action does."""
    app, client, posts = env
    acc = _account(client)
    _repo(client, acc)

    r = _github_post(client, _payload(action="edited"))
    assert r.status_code == 200

    with app.state.SessionLocal() as db:
        assert db.execute(select(Event)).scalars().all() == []
    assert posts == []


# --- publishing-credential transfer survival (2026-07-22 incident) ---------


def _mint_env(app, *, repo_id, forge_repo_id):
    """Seed a transfer scenario: a stale installed row (old installation,
    old name, older last_seen_at, but NEWER installation sync time — the
    exact shape that fooled the old ordering) plus a fresh row under a new
    installation carrying the post-transfer name."""
    from datetime import datetime, timezone

    with app.state.SessionLocal() as db:
        repo = db.get(Repo, repo_id)
        repo.forge_repo_id = forge_repo_id
        old_name = repo.repo_full_name
        stale_inst = GitHubInstallation(
            id=ids.github_installation_id(),
            account_id=repo.account_id,
            installation_id="140994877",
            target_login="owner",
            target_type="User",
            last_synced_at=datetime(2099, 1, 2, tzinfo=timezone.utc),
        )
        fresh_inst = GitHubInstallation(
            id=ids.github_installation_id(),
            account_id=repo.account_id,
            installation_id="200",
            target_login="neworg",
            target_type="Organization",
            last_synced_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        )
        db.add_all([stale_inst, fresh_inst])
        db.flush()
        db.add_all(
            [
                GitHubInstalledRepo(
                    id=ids.github_installed_repo_id(),
                    github_installation_id=stale_inst.id,
                    repo_full_name=old_name,
                    forge_repo_id=forge_repo_id,
                    last_seen_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
                ),
                GitHubInstalledRepo(
                    id=ids.github_installed_repo_id(),
                    github_installation_id=fresh_inst.id,
                    repo_full_name="neworg/newrepo",
                    forge_repo_id=forge_repo_id,
                    last_seen_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
                ),
            ]
        )
        db.commit()


def _capture_credential(monkeypatch):
    seen = {}

    def fake_credential(
        settings, installation_id, *, repository_ids=None, repositories=None,
    ):
        seen.update(
            installation_id=installation_id,
            repository_ids=repository_ids,
            repositories=repositories,
        )
        return {"token": "ghs_fresh", "expires_at": "2099-01-01T00:00:00Z"}

    monkeypatch.setattr(
        "brnrd.routers.daemons.github_app_client.installation_access_credential",
        fake_credential,
    )
    return seen


def test_publishing_credential_survives_repo_transfer(env, monkeypatch):
    """forge_repo_id-first matching targets the fresh installation and
    self-heals the Repo row to the post-transfer name."""
    app, client, _ = env
    acc = _account(client)
    repo_id = _repo(client, acc)  # named owner/repo — the pre-transfer name
    daemon_headers = _daemon_headers(client, acc, repo_id)
    _mint_env(app, repo_id=repo_id, forge_repo_id="1194527686")
    seen = _capture_credential(monkeypatch)

    response = client.post(
        "/v1/daemons/publishing-credential", headers=daemon_headers,
    )

    assert response.status_code == 200
    assert seen["installation_id"] == "200"
    assert seen["repository_ids"] == [1194527686]
    with app.state.SessionLocal() as db:
        repo = db.get(Repo, repo_id)
        assert repo.repo_full_name == "neworg/newrepo"
        assert repo.repo_owner == "neworg"
        assert repo.repo_name == "newrepo"


def test_publishing_credential_name_fallback_without_forge_repo_id(
    env, monkeypatch,
):
    app, client, _ = env
    acc = _account(client)
    repo_id = _repo(client, acc)
    daemon_headers = _daemon_headers(client, acc, repo_id)
    with app.state.SessionLocal() as db:
        repo = db.get(Repo, repo_id)
        assert repo.forge_repo_id is None
        installation = GitHubInstallation(
            id=ids.github_installation_id(),
            account_id=repo.account_id,
            installation_id="73",
            target_login="owner",
            target_type="User",
        )
        db.add(installation)
        db.flush()
        db.add(
            GitHubInstalledRepo(
                id=ids.github_installed_repo_id(),
                github_installation_id=installation.id,
                repo_full_name=repo.repo_full_name,
                forge_repo_id=None,
            )
        )
        db.commit()
    seen = _capture_credential(monkeypatch)

    response = client.post(
        "/v1/daemons/publishing-credential", headers=daemon_headers,
    )

    assert response.status_code == 200
    assert seen["installation_id"] == "73"
    assert seen["repositories"] == ["repo"]
    with app.state.SessionLocal() as db:
        assert db.get(Repo, repo_id).repo_full_name == "owner/repo"


def test_publishing_credential_maps_github_error_to_502(env, monkeypatch):
    import httpx

    app, client, _ = env
    acc = _account(client)
    repo_id = _repo(client, acc)
    daemon_headers = _daemon_headers(client, acc, repo_id)
    _mint_env(app, repo_id=repo_id, forge_repo_id="4242")

    def raise_422(settings, installation_id, **kwargs):
        request = httpx.Request(
            "POST", "https://api.github.com/app/installations/200/access_tokens",
        )
        raise httpx.HTTPStatusError(
            "422 Unprocessable Entity",
            request=request,
            response=httpx.Response(422, request=request),
        )

    monkeypatch.setattr(
        "brnrd.routers.daemons.github_app_client.installation_access_credential",
        raise_422,
    )
    response = client.post(
        "/v1/daemons/publishing-credential", headers=daemon_headers,
    )

    assert response.status_code == 502
    assert "422" in response.json()["detail"]


def test_sync_installation_prunes_rows_dropped_from_listing(env, monkeypatch):
    from brnrd.routers import github_app as github_app_router

    app, client, _ = env
    acc = _account(client)
    _repo(client, acc)

    with app.state.SessionLocal() as db:
        other_inst = GitHubInstallation(
            id=ids.github_installation_id(),
            account_id=None,
            installation_id="999",
            target_login="other",
            target_type="User",
        )
        db.add(other_inst)
        db.flush()
        db.add(
            GitHubInstalledRepo(
                id=ids.github_installed_repo_id(),
                github_installation_id=other_inst.id,
                repo_full_name="other/kept-elsewhere",
            )
        )
        db.commit()

    listings = [
        [
            {"full_name": "owner/kept", "id": 1, "owner": {"login": "owner", "type": "User"}},
            {"full_name": "owner/dropped", "id": 2, "owner": {"login": "owner", "type": "User"}},
        ],
        [
            {"full_name": "owner/kept", "id": 1, "owner": {"login": "owner", "type": "User"}},
        ],
    ]
    monkeypatch.setattr(
        github_app_router.gh_app,
        "list_installation_repositories",
        lambda settings, installation_id: listings.pop(0),
    )

    with app.state.SessionLocal() as db:
        installation = github_app_router.sync_installation(
            db, app.state.settings, "555",
        )
        inst_id = installation.id
        names = {
            row.repo_full_name
            for row in db.execute(
                select(GitHubInstalledRepo).where(
                    GitHubInstalledRepo.github_installation_id == inst_id
                )
            ).scalars()
        }
        assert names == {"owner/kept", "owner/dropped"}

        github_app_router.sync_installation(db, app.state.settings, "555")
        names = {
            row.repo_full_name
            for row in db.execute(
                select(GitHubInstalledRepo).where(
                    GitHubInstalledRepo.github_installation_id == inst_id
                )
            ).scalars()
        }
        assert names == {"owner/kept"}

        all_names = {
            row.repo_full_name
            for row in db.execute(select(GitHubInstalledRepo)).scalars()
        }
        assert "other/kept-elsewhere" in all_names

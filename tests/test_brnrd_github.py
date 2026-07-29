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
        "repository": {
            "id": 8675309,
            "name": repo.rsplit("/", 1)[-1],
            "full_name": repo,
        },
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


def _github_app_post(client, payload, *, event, secret=_SECRET):
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sig = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        raw,
        hashlib.sha256,
    ).hexdigest()
    return client.post(
        "/api/github/webhook",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": event,
            "X-Hub-Signature-256": sig,
        },
    )


def _assignment_payload(
    *,
    repo="owner/repo",
    number=17,
    assignee="brr-bot",
    assigner="alice",
    is_pr=False,
):
    issue = {
        "number": number,
        "title": "Fix the capacity cliff",
        "body": "The long poll owns a worker.",
        "html_url": (
            f"https://github.com/{repo}/"
            f"{'pull' if is_pr else 'issues'}/{number}"
        ),
    }
    if is_pr:
        issue["pull_request"] = {
            "url": f"https://api.github.com/repos/{repo}/pulls/{number}",
        }
    return {
        "action": "assigned",
        "installation": {"id": 42},
        "repository": {
            "id": 8675309,
            "name": repo.rsplit("/", 1)[-1],
            "full_name": repo,
        },
        "issue": issue,
        "assignee": {"login": assignee},
        "sender": {"login": assigner, "type": "User"},
    }


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


def test_app_assignment_enqueues_and_replies_as_installation_bot(
    env,
    monkeypatch,
):
    app, client, posts = env
    acc = _account(client)
    rid = _repo(client, acc)
    minted = []

    def mint(_settings, installation_id, **scope):
        minted.append((installation_id, scope))
        return {
            "token": "ghs_installation",
            "expires_at": "2099-01-01T00:00:00Z",
        }

    monkeypatch.setattr(
        github_app_platform,
        "installation_access_credential",
        mint,
    )

    response = _github_app_post(
        client,
        _assignment_payload(),
        event="issues",
    )
    assert response.status_code == 200, response.text

    dmn = _daemon_headers(client, acc, rid)
    drained = client.get(
        "/v1/daemons/inbox",
        params={"since": 0, "wait": 0},
        headers=dmn,
    ).json()
    event = drained["events"][0]
    assert event["body"] == (
        "# Fix the capacity cliff\n\nThe long poll owns a worker.\n"
    )
    assert event["reply_to"] == {
        "platform": "github",
        "repo": "owner/repo",
        "issue_number": 17,
        "kind": "issue-assignment",
        "author": "alice",
        "html_url": "https://github.com/owner/repo/issues/17",
        "trigger": "assignee",
        "assignee": "brr-bot",
        "installation_id": "42",
    }

    posted = client.post(
        "/v1/daemons/responses",
        json={
            "event_id": event["event_id"],
            "body_markdown": "fixed on the branch",
            "status": "done",
        },
        headers=dmn,
    )
    assert posted.status_code == 200, posted.text
    assert minted == [
        (
            "42",
            {
                "repository_ids": [8675309],
                "repositories": None,
            },
        ),
        (
            "42",
            {
                "repositories": ["repo"],
            },
        ),
    ]
    assert posts == [
        {
            "token": "ghs_installation",
            "repo": "owner/repo",
            "issue_number": 17,
            "body": (
                "> Work assigned by [@alice]"
                "(https://github.com/owner/repo/issues/17)\n\n"
                "fixed on the branch"
            ),
        },
    ]


def test_app_assignment_ignores_a_different_assignee(env, monkeypatch):
    app, client, _posts = env
    acc = _account(client)
    _repo(client, acc)
    minted = []
    monkeypatch.setattr(
        github_app_platform,
        "installation_access_credential",
        lambda *_args, **_kwargs: minted.append(True),
    )

    response = _github_app_post(
        client,
        _assignment_payload(assignee="someone-else"),
        event="issues",
    )
    assert response.status_code == 200

    with app.state.SessionLocal() as db:
        assert db.execute(select(Event)).scalars().all() == []
    assert minted == []


def test_app_assignment_token_failure_asks_github_to_retry(env, monkeypatch):
    app, client, _posts = env
    acc = _account(client)
    _repo(client, acc)

    def fail(*_args, **_kwargs):
        raise RuntimeError("temporary mint failure")

    monkeypatch.setattr(
        github_app_platform,
        "installation_access_credential",
        fail,
    )

    response = _github_app_post(
        client,
        _assignment_payload(),
        event="issues",
    )
    assert response.status_code == 502

    with app.state.SessionLocal() as db:
        assert db.execute(select(Event)).scalars().all() == []


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


# ── the GitHub App router's own webhook and session surface ─────────


def _app_env(monkeypatch, *, github_webhook_secret: str = _SECRET):
    """A client whose GitHub App webhook secret is configurable per test.

    Built directly rather than through `_build_env`, which pins the secret to
    `_SECRET` — the unset-secret case is exactly what these tests need to reach.
    """
    settings = Settings(
        database_url="sqlite:///:memory:",
        github_webhook_secret=github_webhook_secret,
        github_bot_login="brr-bot",
        github_bot_token="ghs_test",
    )
    app = create_app(settings)
    return app, TestClient(app), []


def _app_webhook_body(installation_id: str = "42") -> bytes:
    return json.dumps({"installation": {"id": installation_id}}).encode()


def _app_signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_app_webhook_refuses_an_unsigned_request_when_no_secret_is_configured(monkeypatch):
    """The fail-open this closes: with `BRNRD_GITHUB_WEBHOOK_SECRET` unset the
    guard's leading `settings.github_webhook_secret and …` conjunct skipped
    verification entirely, so anyone on the internet could POST an
    `installation` event and drive `sync_installation`.

    Refused before the body is parsed or synced, and 403 — the shape all three
    siblings already use (`routers/webhooks.py::github_webhook`,
    `::telegram_webhook`, `::stripe_webhook`).
    """
    called = []
    monkeypatch.setattr(
        "brnrd.routers.github_app.sync_installation",
        lambda *a, **k: called.append(a),
    )
    _app, client, _ = _app_env(monkeypatch, github_webhook_secret="")

    r = client.post(
        "/api/github/webhook",
        content=_app_webhook_body(),
        headers={"X-GitHub-Event": "installation", "Content-Type": "application/json"},
    )

    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "bad secret"
    assert called == [], "sync ran despite an unverifiable request"


def test_app_webhook_refuses_a_signed_request_when_no_secret_is_configured(monkeypatch):
    """Even a *correctly* signed body cannot be accepted while the server holds
    no secret — there is nothing to verify against, so any signature the sender
    chose would do."""
    called = []
    monkeypatch.setattr(
        "brnrd.routers.github_app.sync_installation",
        lambda *a, **k: called.append(a),
    )
    _app, client, _ = _app_env(monkeypatch, github_webhook_secret="")
    body = _app_webhook_body()

    r = client.post(
        "/api/github/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "installation",
            "X-Hub-Signature-256": _app_signature("attacker-picked", body),
            "Content-Type": "application/json",
        },
    )

    assert r.status_code == 403, r.text
    assert called == []


def test_app_webhook_refuses_a_bad_signature_when_a_secret_is_configured(monkeypatch):
    called = []
    monkeypatch.setattr(
        "brnrd.routers.github_app.sync_installation",
        lambda *a, **k: called.append(a),
    )
    _app, client, _ = _app_env(monkeypatch)
    body = _app_webhook_body()

    r = client.post(
        "/api/github/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "installation",
            "X-Hub-Signature-256": _app_signature("wrong-secret", body),
            "Content-Type": "application/json",
        },
    )

    assert r.status_code == 403, r.text
    assert called == []


def test_app_webhook_accepts_a_correctly_signed_request(monkeypatch):
    """The guard closes on the unconfigured case without shutting the door on
    the configured one."""
    called = []
    monkeypatch.setattr(
        "brnrd.routers.github_app.sync_installation",
        lambda *a, **k: called.append(a),
    )
    _app, client, _ = _app_env(monkeypatch)
    body = _app_webhook_body()

    r = client.post(
        "/api/github/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "installation",
            "X-Hub-Signature-256": _app_signature(_SECRET, body),
            "Content-Type": "application/json",
        },
    )

    assert r.status_code == 200, r.text
    assert len(called) == 1, "a correctly signed installation event did not sync"


# ── expired session cookies, at both routers that accept one ────────


def _expire_sessions(app, account_id: str) -> None:
    """Age every session token for this account past its expiry.

    Session tokens really do get one — `routers/accounts.py::issue_session_token`
    writes `expires_at = now + SESSION_TTL` — so this is the state a browser
    reaches by simply holding a cookie long enough, not a synthetic one.
    """
    from datetime import datetime, timedelta, timezone

    from brnrd.models import Token

    with app.state.SessionLocal() as db:
        for token in db.execute(
            select(Token).where(Token.account_id == account_id, Token.kind == Token.KIND_SESSION)
        ).scalars():
            token.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()


def _session_cookie_client(monkeypatch):
    """A client holding a real session **cookie** — the credential these two
    routers actually resolve, as a browser would present it."""
    from brnrd.models import Account

    app, client, _ = _app_env(monkeypatch)
    acc = _account(client)
    client.cookies.set(app.state.settings.session_cookie, acc["Authorization"].split()[1])
    with app.state.SessionLocal() as db:
        account_id = db.execute(select(Account)).scalars().one().id
    return app, client, acc, account_id


def test_github_sync_refuses_an_expired_session_cookie(monkeypatch):
    """The drift this closes: `github_app.py` carried a third copy of "resolve a
    caller from a credential" that checked `revoked` but **not** `expires_at`,
    so an expired cookie still authenticated against `/api/github/sync` and
    could drive `github_installation_sync` over the account's repos — while
    every other surface in the app rejected it.

    All three resolvers now share one predicate
    (`brnrd.auth.account_id_from_session_cookie`), so there is no fourth copy
    to drift.
    """
    synced = []
    monkeypatch.setattr(
        "brnrd.routers.github_app.sync_app_installations_for_account",
        lambda *a, **k: synced.append(a) or 1,
    )
    app, client, _acc, account_id = _session_cookie_client(monkeypatch)

    # Sanity: the cookie works while it is live, so the assertion below is
    # about expiry and not about the cookie never having been accepted.
    r = client.post("/api/github/sync", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/?notice=github-synced"
    assert len(synced) == 1

    _expire_sessions(app, account_id)

    r = client.post("/api/github/sync", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login?next=/", "expired cookie still authenticated"
    assert len(synced) == 1, "sync ran for an expired session"


def test_github_setup_does_not_attribute_an_install_to_an_expired_session(monkeypatch):
    """The same resolver on the App's other cookie surface: an expired cookie
    must not attach a discovered installation to that account."""
    seen = []
    monkeypatch.setattr(
        "brnrd.routers.github_app.sync_installation",
        lambda db, settings, installation_id, account_id=None: seen.append(account_id),
    )
    app, client, _acc, account_id = _session_cookie_client(monkeypatch)
    _expire_sessions(app, account_id)

    r = client.get(
        "/api/github/setup", params={"installation_id": "42"}, follow_redirects=False
    )

    assert r.status_code == 303
    assert seen == [None], "an expired cookie attributed the installation to its account"


def test_dashboard_json_refuses_an_expired_session_cookie(monkeypatch):
    """The `_session` resolver's own path, pinned alongside the GitHub App one
    so the two cannot drift apart again."""
    app, client, _acc, account_id = _session_cookie_client(monkeypatch)

    assert client.get("/v1/dashboard/repos").status_code == 200

    _expire_sessions(app, account_id)

    assert client.get("/v1/dashboard/repos").status_code == 401

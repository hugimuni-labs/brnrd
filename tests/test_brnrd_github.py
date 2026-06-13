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

from brnrd import create_app  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.models import Event  # noqa: E402
from _helpers import brnrd_account_headers  # noqa: E402

_SECRET = "github-webhook-secret"


@pytest.fixture()
def env(monkeypatch):
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
    )
    app = create_app(settings)
    return app, TestClient(app), posts


def _account(client):
    return brnrd_account_headers(
        client.app, github_id="123", login="octocat", email="a@b.com"
    )


def _project(client, headers, name="demo"):
    return client.post(
        "/v1/accounts/projects", json={"name": name}, headers=headers
    ).json()["project_id"]


def _daemon_headers(client, acc, pid):
    pair = client.post("/v1/accounts/pair").json()
    client.post(
        f"/v1/accounts/pair/{pair['pair_code']}/approve",
        json={"project_id": pid},
        headers=acc,
    )
    token = client.get(
        f"/v1/accounts/pair/{pair['pair_code']}",
        params={"poll_secret": pair["poll_secret"]},
    ).json()["daemon_token"]
    return {"Authorization": f"Bearer {token}"}


def _bind_repo(client, acc, pid, *, repo="owner/repo", installation_id="42"):
    return client.post(
        "/v1/accounts/bindings/repo",
        json={
            "installation_id": installation_id,
            "repo_full_name": repo,
            "project_id": pid,
        },
        headers=acc,
    )


def _payload(*, repo="owner/repo", body="@brr-bot do the thing",
             installation_id=42, number=17, comment_id=100, is_pr=False):
    issue = {"number": number, "title": "Work item"}
    if is_pr:
        issue["pull_request"] = {
            "url": f"https://api.github.com/repos/{repo}/pulls/{number}",
        }
    kind = "pull" if is_pr else "issues"
    return {
        "action": "created",
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
            "user": {"login": "alice"},
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


def test_repo_binding_create_list_and_rebind(env):
    _, client, _ = env
    acc = _account(client)
    pid_a = _project(client, acc, name="alpha")
    pid_b = _project(client, acc, name="beta")

    first = _bind_repo(client, acc, pid_a)
    assert first.status_code == 201, first.text
    assert first.json()["project_id"] == pid_a

    second = _bind_repo(client, acc, pid_b)
    assert second.status_code == 201, second.text
    assert second.json()["binding_id"] == first.json()["binding_id"]
    assert second.json()["project_id"] == pid_b

    listing = client.get("/v1/accounts/bindings/repo", headers=acc).json()
    assert len(listing["bindings"]) == 1
    assert listing["bindings"][0]["repo_full_name"] == "owner/repo"
    assert listing["bindings"][0]["project_id"] == pid_b


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
    pid = _project(client, acc)
    assert _bind_repo(client, acc, pid).status_code == 201

    r = _github_post(client, _payload(is_pr=True))
    assert r.status_code == 200

    with app.state.SessionLocal() as db:
        event = db.execute(select(Event).where(Event.source == "github")).scalar_one()
        assert event.project_id == pid
        assert "@brr-bot do the thing" in (event.body or "")

    dmn = _daemon_headers(client, acc, pid)
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
    pid = _project(client, acc)
    assert _bind_repo(client, acc, pid).status_code == 201

    r = _github_post(client, _payload(body="plain repo chatter"))
    assert r.status_code == 200

    with app.state.SessionLocal() as db:
        assert db.execute(select(Event)).scalars().all() == []
    assert posts == []

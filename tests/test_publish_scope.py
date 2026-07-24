"""Explicit publish-scope consent at repo connect (legal pack item 2, #417
follow-on).

#417 built ``publish.layers`` as a daemon-local gate (`.brr/config`) over the
seven dashboard-mirror lanes. This is the second half: an explicit consent
captured at connect on brnrd.dev (`brnrd.publish_scope`, wired into
`_connect_repo_core` / `/v1/repos/connect` and enforced again at the
`PUT /v1/daemons/*` publish seam), not only hidden behind a UI control.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("multipart")

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from brnrd import create_app, publish_scope  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.models import Repo  # noqa: E402
from brnrd.oauth import GitHubIdentity  # noqa: E402
from brnrd.routers.accounts import account_for_github_identity, issue_session_token  # noqa: E402
from brr.gates import cloud  # noqa: E402


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


def _login(client: TestClient, *, github_id: str = "12345", login: str = "Gurio") -> str:
    with client.app.state.SessionLocal() as db:
        account = account_for_github_identity(
            db, GitHubIdentity(github_id=github_id, login=login, email=None)
        )
        token = issue_session_token(db, account)
    client.cookies.set("brnrd_session", token)
    return token


def _pair_daemon(client: TestClient, repo_id: str) -> str:
    """Full device-flow handshake (mirrors test_brnrd_web.py) — the browser
    session already set by `_login` approves the pair code and the CLI-side
    poll returns the minted daemon token."""
    pair = client.post("/v1/accounts/pair").json()
    approved = client.post(f"/v1/connect/{pair['pair_code']}", json={"repo_id": repo_id})
    assert approved.status_code == 200, approved.text
    polled = client.get(
        f"/v1/accounts/pair/{pair['pair_code']}",
        params={"poll_secret": pair["poll_secret"]},
    ).json()
    return polled["daemon_token"]


# ── normalize_publish_layers ────────────────────────────────────────


def test_normalize_absent_or_empty_is_off():
    assert publish_scope.normalize_publish_layers(None) == "none"
    assert publish_scope.normalize_publish_layers("") == "none"
    assert publish_scope.normalize_publish_layers("   ") == "none"


def test_normalize_none_wins_over_anything_named_beside_it():
    assert publish_scope.normalize_publish_layers("quota,none,activity") == "none"


def test_normalize_canonicalizes_order_and_dedupes():
    assert publish_scope.normalize_publish_layers("quota,activity,quota") == "activity,quota"
    # Different input order, same set -> same stored string, so two consents
    # naming the same scope always compare equal.
    assert publish_scope.normalize_publish_layers(
        "activity,quota"
    ) == publish_scope.normalize_publish_layers("quota,activity")


def test_normalize_rejects_unknown_token_loudly():
    """#417's own lesson, one layer up: `totalnonsense` and `none` must never
    be byte-identical in effect — a typo here is a 4xx, not a silent no-op."""
    with pytest.raises(HTTPException) as exc:
        publish_scope.normalize_publish_layers("totalnonsense")
    assert exc.value.status_code == 422
    assert "totalnonsense" in str(exc.value.detail)


def test_normalize_corpus_expands_to_the_three_slices():
    assert publish_scope.normalize_publish_layers("corpus") == "corpus"
    lanes, slices = publish_scope._repo_scopes("corpus")
    assert slices == frozenset({"authored", "knowledge", "runs"})
    assert lanes == {"corpus"}


# ── connect-time consent ────────────────────────────────────────────


def test_connect_defaults_new_repo_to_off_when_publish_layers_omitted():
    """The ticket's own open product question, resolved: default-off for
    NEW connects. A client that omits the field entirely still gets the
    safe default, not the daemon-config "absent means everything" rule."""
    client = _client()
    _login(client)
    r = client.post("/v1/repos/connect", json={"repo_full_name": "Gurio/new"})
    assert r.status_code == 200 and r.json()["ok"] is True
    with client.app.state.SessionLocal() as db:
        repo = db.query(Repo).filter(Repo.repo_full_name == "Gurio/new").one()
        assert repo.publish_layers == "none"


def test_connect_stores_an_explicit_consent():
    client = _client()
    _login(client)
    r = client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/new", "publish_layers": "activity,quota"},
    )
    assert r.status_code == 200 and r.json()["ok"] is True
    with client.app.state.SessionLocal() as db:
        repo = db.query(Repo).filter(Repo.repo_full_name == "Gurio/new").one()
        assert repo.publish_layers == "activity,quota"


def test_connect_rejects_unknown_publish_layers_token():
    client = _client()
    _login(client)
    r = client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/new", "publish_layers": "totalnonsense"},
    )
    assert r.status_code == 422
    assert r.json()["ok"] is False
    with client.app.state.SessionLocal() as db:
        assert db.query(Repo).filter(Repo.repo_full_name == "Gurio/new").one_or_none() is None


def test_reconnect_does_not_silently_change_a_recorded_consent():
    """Consent is captured once, at creation — an idempotent reconnect (the
    GitHub auto-sync path re-POSTs the same repo) must not quietly narrow or
    widen a choice the user already made."""
    client = _client()
    _login(client)
    client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/new", "publish_layers": "activity"},
    )
    again = client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/new", "default_branch": "trunk"},
    )
    assert again.status_code == 200
    with client.app.state.SessionLocal() as db:
        repo = db.query(Repo).filter(Repo.repo_full_name == "Gurio/new").one()
        assert repo.publish_layers == "activity"
        assert repo.default_branch == "trunk"


def test_dashboard_repos_view_surfaces_publish_layers():
    client = _client()
    _login(client)
    client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/new", "publish_layers": "quota"},
    )
    body = client.get("/v1/dashboard/repos").json()
    assert body["connected_repos"][0]["publish_layers"] == "quota"


# ── settings surface: revisit later ─────────────────────────────────


def test_publish_layers_settings_endpoint_updates_consent():
    client = _client()
    _login(client)
    connect = client.post("/v1/repos/connect", json={"repo_full_name": "Gurio/new"}).json()
    with client.app.state.SessionLocal() as db:
        repo_id = db.query(Repo).filter(Repo.repo_full_name == "Gurio/new").one().id
    assert connect["ok"] is True

    r = client.post(
        f"/v1/repos/{repo_id}/publish-layers", json={"publish_layers": "authored,quota"}
    )
    assert r.status_code == 200 and r.json()["ok"] is True
    with client.app.state.SessionLocal() as db:
        # Canonical order is lanes-then-corpus-slices (`quota` is a lane,
        # `authored` a corpus slice), not input order — see
        # test_normalize_canonicalizes_order_and_dedupes.
        assert db.get(Repo, repo_id).publish_layers == "quota,authored"


def test_publish_layers_settings_endpoint_validates():
    client = _client()
    _login(client)
    client.post("/v1/repos/connect", json={"repo_full_name": "Gurio/new"})
    with client.app.state.SessionLocal() as db:
        repo_id = db.query(Repo).filter(Repo.repo_full_name == "Gurio/new").one().id

    r = client.post(f"/v1/repos/{repo_id}/publish-layers", json={"publish_layers": "nonsense"})
    assert r.status_code == 422
    with client.app.state.SessionLocal() as db:
        assert db.get(Repo, repo_id).publish_layers == "none"


def test_publish_layers_settings_endpoint_is_account_scoped():
    client = _client()
    _login(client, github_id="1", login="owner")
    client.post("/v1/repos/connect", json={"repo_full_name": "owner/repo"})
    with client.app.state.SessionLocal() as db:
        repo_id = db.query(Repo).filter(Repo.repo_full_name == "owner/repo").one().id

    _login(client, github_id="2", login="intruder")
    r = client.post(f"/v1/repos/{repo_id}/publish-layers", json={"publish_layers": "corpus"})
    assert r.status_code == 404


# ── server-side enforcement at the publish seam ─────────────────────


def test_activity_lane_drops_content_the_repo_did_not_consent_to():
    client = _client()
    _login(client)
    client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/new", "publish_layers": "quota"},  # not "activity"
    )
    with client.app.state.SessionLocal() as db:
        repo_id = db.query(Repo).filter(Repo.repo_full_name == "Gurio/new").one().id
    daemon_token = _pair_daemon(client, repo_id)
    headers = {"Authorization": f"Bearer {daemon_token}"}

    r = client.put(
        "/v1/daemons/activity",
        json={"records": [{
            "id": "rec-1", "kind": "task", "source": "cli", "conversation_key": "c1",
            "summary": "hello", "runner": {}, "status": "running", "phase": "",
            "branch": "", "pr_number": None, "started_at": None, "updated_at": None,
            "scheduled_for": None, "defer_until": None, "links": {},
        }]},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["activity"] == []


def test_activity_lane_permits_content_the_repo_consented_to():
    client = _client()
    _login(client)
    client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/new", "publish_layers": "activity"},
    )
    with client.app.state.SessionLocal() as db:
        repo_id = db.query(Repo).filter(Repo.repo_full_name == "Gurio/new").one().id
    daemon_token = _pair_daemon(client, repo_id)
    headers = {"Authorization": f"Bearer {daemon_token}"}

    r = client.put(
        "/v1/daemons/activity",
        json={"records": [{
            "id": "rec-1", "kind": "task", "source": "cli", "conversation_key": "c1",
            "summary": "hello", "runner": {}, "status": "running", "phase": "",
            "branch": "", "pr_number": None, "started_at": None, "updated_at": None,
            "scheduled_for": None, "defer_until": None, "links": {},
        }]},
        headers=headers,
    )
    assert r.status_code == 200
    assert len(r.json()["activity"]) == 1


def test_quota_lane_gated_by_the_connecting_repos_own_consent():
    client = _client()
    _login(client)
    client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/new", "publish_layers": "activity"},  # not "quota"
    )
    with client.app.state.SessionLocal() as db:
        repo_id = db.query(Repo).filter(Repo.repo_full_name == "Gurio/new").one().id
    daemon_token = _pair_daemon(client, repo_id)
    headers = {"Authorization": f"Bearer {daemon_token}"}
    client.post("/v1/daemons/register", json={"daemon_name": "laptop"}, headers=headers)

    r = client.put(
        "/v1/daemons/quota",
        json={"shells": [{"shell": "claude", "windows": []}], "gates": []},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["shells"] == []


def test_legacy_repo_with_no_recorded_consent_is_unenforced():
    """A repo connected before this column existed (`publish_layers is
    None`) keeps its current behaviour untouched — the whole point of the
    "existing accounts" half of the ticket."""
    client = _client()
    token = _login(client)

    # Created through the account-API-key surface, which this change
    # deliberately leaves un-migrated (see publish_scope / accounts.py).
    api_headers = {"Authorization": f"Bearer {token}"}
    repo_id = client.post(
        "/v1/accounts/repos",
        json={"repo_full_name": "Gurio/legacy"},
        headers=api_headers,
    ).json()["repo_id"]
    with client.app.state.SessionLocal() as db:
        assert db.get(Repo, repo_id).publish_layers is None

    daemon_token = _pair_daemon(client, repo_id)
    headers = {"Authorization": f"Bearer {daemon_token}"}
    client.post("/v1/daemons/register", json={"daemon_name": "laptop"}, headers=headers)
    r = client.put(
        "/v1/daemons/quota",
        json={"shells": [{"shell": "claude", "windows": []}], "gates": []},
        headers=headers,
    )
    assert r.status_code == 200
    assert len(r.json()["shells"]) == 1


def test_corpus_lane_requires_every_connected_repo_to_consent():
    """Corpus/knowledge is account-wide by construction — one repo saying
    yes cannot authorize shipping the whole account's home knowledge."""
    client = _client()
    _login(client)
    client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/a", "publish_layers": "corpus"},
    )
    client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/b", "publish_layers": "none"},
    )
    with client.app.state.SessionLocal() as db:
        repo_a = db.query(Repo).filter(Repo.repo_full_name == "Gurio/a").one().id
    daemon_token = _pair_daemon(client, repo_a)
    headers = {"Authorization": f"Bearer {daemon_token}"}

    r = client.put(
        "/v1/daemons/surface",
        json={"files": [{"path": "surface/plan.md", "markdown": "# plan", "layer": "authored", "truncated": False}]},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["files"] == []


def test_corpus_lane_ships_the_slices_every_repo_agreed_to():
    client = _client()
    _login(client)
    client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/a", "publish_layers": "authored"},
    )
    with client.app.state.SessionLocal() as db:
        repo_a = db.query(Repo).filter(Repo.repo_full_name == "Gurio/a").one().id
    daemon_token = _pair_daemon(client, repo_a)
    headers = {"Authorization": f"Bearer {daemon_token}"}

    r = client.put(
        "/v1/daemons/surface",
        json={"files": [
            {"path": "surface/plan.md", "markdown": "# plan", "layer": "authored", "truncated": False},
            {"path": "knowledge/index.md", "markdown": "# kb", "layer": "knowledge", "truncated": False},
        ]},
        headers=headers,
    )
    assert r.status_code == 200
    files = r.json()["files"]
    assert [f["path"] for f in files] == ["surface/plan.md"]


def test_a_future_layer_ships_dark_for_an_existing_consent(monkeypatch):
    """Pin for the ticket's own requirement: "a layer added to the product
    later ships dark for existing consents until opted in." Simulate the
    product growing an eighth lane after a repo already recorded a
    narrower consent — the new lane must not silently inherit reachability.
    """
    client = _client()
    _login(client)
    client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/new", "publish_layers": "quota"},
    )
    with client.app.state.SessionLocal() as db:
        repo_id = db.query(Repo).filter(Repo.repo_full_name == "Gurio/new").one().id

    monkeypatch.setattr(cloud, "_PUBLISH_TICK_ORDER", cloud._PUBLISH_TICK_ORDER + ("widget_lane",))

    with client.app.state.SessionLocal() as db:
        assert publish_scope.lane_permitted(db, repo_id=repo_id, lane="widget_lane") is False
        # The consent the repo actually made is unaffected by the addition.
        assert publish_scope.lane_permitted(db, repo_id=repo_id, lane="quota") is True

"""Explicit publish-scope consent at repo connect (legal pack item 2, #417
follow-on).

#417 built ``publish.layers`` as a daemon-local gate (`.brr/config`) over the
seven dashboard-mirror lanes. This is the second half: an explicit consent
captured at connect on brnrd.dev (`brnrd.publish_scope`, wired into
`_connect_repo_core` / `/v1/repos/connect` and enforced again at the
`PUT /v1/daemons/*` publish seam), not only hidden behind a UI control.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("multipart")

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from brnrd import create_app, publish_scope  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.models import Account, ActivityRecord, Daemon, Repo, Token  # noqa: E402
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


def test_narrowing_purges_only_removed_scope_and_keeps_the_other_repo():
    """#734: withdrawal is an eraser, and its key is the data's subject.

    Both repos are connected through the real consent caller.  The stored
    mirrors deliberately mix their rows inside each account-wide daemon
    snapshot: deleting a whole daemon snapshot would make the victim vanish
    but would also destroy the positive control this test protects.
    """
    client = _client()
    _login(client)
    everything = ",".join(cloud._PUBLISH_TICK_ORDER)
    for name in ("Gurio/victim", "Gurio/survivor"):
        result = client.post(
            "/v1/repos/connect",
            json={"repo_full_name": name, "publish_layers": everything},
        )
        assert result.status_code == 200, result.text

    with client.app.state.SessionLocal() as db:
        victim = db.query(Repo).filter(Repo.repo_full_name == "Gurio/victim").one()
        survivor = db.query(Repo).filter(Repo.repo_full_name == "Gurio/survivor").one()
        account = db.get(Account, victim.account_id)
        session_token = db.query(Token).filter(Token.kind == Token.KIND_SESSION).one()

        db.add_all(
            [
                ActivityRecord(
                    id="act-victim",
                    repo_id=victim.id,
                    token_id=session_token.id,
                    record_id="run:victim",
                ),
                ActivityRecord(
                    id="act-survivor",
                    repo_id=survivor.id,
                    token_id=session_token.id,
                    record_id="run:survivor",
                ),
            ]
        )
        victim_token = Token(
            id="tok-victim-daemon",
            account_id=victim.account_id,
            repo_id=victim.id,
            kind=Token.KIND_DAEMON,
            token_hash="hash-victim-daemon",
        )
        survivor_token = Token(
            id="tok-survivor-daemon",
            account_id=victim.account_id,
            repo_id=survivor.id,
            kind=Token.KIND_DAEMON,
            token_hash="hash-survivor-daemon",
        )
        db.add_all([victim_token, survivor_token])

        mixed = [
            {"id": "victim-row", "repo_label": "gurio/VICTIM"},
            {"id": "survivor-row", "repo_label": "Gurio/survivor"},
            # The stored format cannot attribute this row.  Withdrawal keeps
            # it rather than guessing and erasing a sibling's content.
            {"id": "unattributed-row"},
        ]
        victim_daemon = Daemon(
            id="daemon-victim",
            account_id=victim.account_id,
            repo_id=victim.id,
            token_id=victim_token.id,
            daemon_name="victim",
            quota_json='[{"shell":"victim"}]',
            gate_health_json='[{"gate":"victim"}]',
            live_runs_json=json.dumps(mixed),
            daemon_mood_json='{"name":"victim"}',
            pr_review_queue_json=json.dumps(mixed),
            run_ledger_json=json.dumps(mixed),
            runners_json='[{"name":"victim"}]',
            runners_default="victim",
            environment_default="worktree",
            environments_json='[{"name":"worktree"}]',
        )
        survivor_daemon = Daemon(
            id="daemon-survivor",
            account_id=victim.account_id,
            repo_id=survivor.id,
            token_id=survivor_token.id,
            daemon_name="survivor",
            quota_json='[{"shell":"survivor"}]',
            gate_health_json='[{"gate":"survivor"}]',
            live_runs_json=json.dumps(mixed),
            daemon_mood_json='{"name":"survivor"}',
            pr_review_queue_json=json.dumps(mixed),
            run_ledger_json=json.dumps(mixed),
            runners_json='[{"name":"survivor"}]',
            runners_default="survivor",
            environment_default="host",
            environments_json='[{"name":"host"}]',
        )
        db.add_all([victim_daemon, survivor_daemon])
        account.surface_json = json.dumps(
            [
                {"path": "surface/plan.md", "layer": "authored"},
                {"path": "knowledge/index.md", "layer": "knowledge"},
                {"path": "runs/run-1/body.md", "layer": "runs"},
            ]
        )
        account_id = victim.account_id
        victim_id = victim.id
        survivor_id = survivor.id
        db.commit()

    # Keep quota and one corpus slice; withdraw every other lane/slice.
    response = client.post(
        f"/v1/repos/{victim_id}/publish-layers",
        json={"publish_layers": "quota,knowledge"},
    )
    assert response.status_code == 200, response.text

    with client.app.state.SessionLocal() as db:
        assert db.get(Repo, victim_id).publish_layers == "quota,knowledge"
        assert {
            row.repo_id for row in db.query(ActivityRecord).order_by(ActivityRecord.id)
        } == {survivor_id}

        files = json.loads(db.get(Account, account_id).surface_json)
        assert [(item["path"], item["layer"]) for item in files] == [
            ("knowledge/index.md", "knowledge")
        ]

        victim_daemon = db.get(Daemon, "daemon-victim")
        survivor_daemon = db.get(Daemon, "daemon-survivor")
        # Quota was retained, so widening/unchanged scope is a no-op.
        assert json.loads(victim_daemon.quota_json) == [{"shell": "victim"}]
        assert json.loads(victim_daemon.gate_health_json) == [{"gate": "victim"}]
        # Repo-keyed content removed with the victim's lane.
        assert json.loads(victim_daemon.runners_json) == []
        assert victim_daemon.runners_default is None
        assert victim_daemon.environment_default is None
        assert json.loads(victim_daemon.environments_json) == []
        assert victim_daemon.daemon_mood_json is None
        # The untouched repo's repo-keyed snapshots survive.
        assert json.loads(survivor_daemon.runners_json) == [{"name": "survivor"}]
        assert survivor_daemon.runners_default == "survivor"
        assert survivor_daemon.daemon_mood_json == '{"name":"survivor"}'

        for daemon in (victim_daemon, survivor_daemon):
            for field in (
                "live_runs_json",
                "pr_review_queue_json",
                "run_ledger_json",
            ):
                assert [row["id"] for row in json.loads(getattr(daemon, field))] == [
                    "survivor-row",
                    "unattributed-row",
                ]


def test_publish_purge_coverage_is_derived_from_the_lane_vocabulary(monkeypatch):
    assert publish_scope.purge_storage_lanes() == frozenset(cloud._PUBLISH_TICK_ORDER)

    monkeypatch.setattr(
        publish_scope,
        "LANES",
        (*publish_scope.LANES, "future_lane"),
    )
    with pytest.raises(RuntimeError, match="future_lane"):
        publish_scope.purge_storage_lanes()


# ── withdrawal must not fail closed against the user ────────────────
#
# Both of these assert the *direction* of failure rather than any happy path.
# The purge exists because Art 7(3) wants withdrawal at least as easy as
# consent; a purge that refuses the withdrawal when something on our side is
# wrong has inverted the very right it implements. On this path the safe error
# is always to erase more — a mirror is a replaceable render cache, and
# over-erasing costs one republish while under-erasing is the violation.


def test_a_malformed_stored_mirror_does_not_block_the_withdrawal(monkeypatch):
    """Regression: an earlier shape answered unparseable stored JSON with a
    409 *"publish scope was not changed"*. Our own corrupt cache then made the
    user unable to turn publishing off."""
    client = _client()
    _login(client)
    everything = ",".join(cloud._PUBLISH_TICK_ORDER)
    result = client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/corrupt", "publish_layers": everything},
    )
    assert result.status_code == 200, result.text

    with client.app.state.SessionLocal() as db:
        repo = db.query(Repo).filter(Repo.repo_full_name == "Gurio/corrupt").one()
        session_token = db.query(Token).filter(Token.kind == Token.KIND_SESSION).one()
        token = Token(
            id="tok-corrupt-daemon",
            account_id=repo.account_id,
            repo_id=repo.id,
            kind=Token.KIND_DAEMON,
            token_hash="hash-corrupt-daemon",
        )
        db.add(token)
        db.add(
            Daemon(
                id="daemon-corrupt",
                account_id=repo.account_id,
                repo_id=repo.id,
                token_id=token.id,
                daemon_name="corrupt",
                # Not JSON at all, and a JSON scalar where a list belongs.
                live_runs_json="{not json at all",
                pr_review_queue_json='"a string, not a list"',
                run_ledger_json=json.dumps([{"id": "r", "repo_label": "Gurio/corrupt"}]),
            )
        )
        account = db.get(Account, repo.account_id)
        account.surface_json = "]["
        repo_id, account_id = repo.id, repo.account_id
        assert session_token is not None
        db.commit()

    response = client.post(
        f"/v1/repos/{repo_id}/publish-layers",
        json={"publish_layers": "none"},
    )
    assert response.status_code == 200, response.text

    with client.app.state.SessionLocal() as db:
        # The consent landed — that is the whole point.
        assert db.get(Repo, repo_id).publish_layers == "none"
        daemon = db.get(Daemon, "daemon-corrupt")
        # Unreadable fields were erased to their column default, not kept.
        assert daemon.live_runs_json == "[]"
        assert daemon.pr_review_queue_json == "[]"
        # A readable field is still filtered precisely, not blanket-erased.
        assert json.loads(daemon.run_ledger_json) == []
        assert db.get(Account, account_id).surface_json == "[]"


def test_a_lane_with_no_purge_target_does_not_block_the_withdrawal(monkeypatch):
    """The coverage assertion is a development-time guarantee, not a runtime
    gate. A lane someone forgot to mark is our bug; making the user unable to
    withdraw because of it would be a second, worse one."""
    client = _client()
    _login(client)
    everything = ",".join(cloud._PUBLISH_TICK_ORDER)
    result = client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/unmarked", "publish_layers": everything},
    )
    assert result.status_code == 200, result.text
    with client.app.state.SessionLocal() as db:
        repo_id = db.query(Repo).filter(Repo.repo_full_name == "Gurio/unmarked").one().id

    # A canonical lane that no model marks — exactly the state the coverage
    # test is there to catch before deploy.
    monkeypatch.setattr(publish_scope, "LANES", (*publish_scope.LANES, "unmarked_lane"))
    with pytest.raises(RuntimeError):
        publish_scope.purge_storage_lanes()

    response = client.post(
        f"/v1/repos/{repo_id}/publish-layers",
        json={"publish_layers": "none"},
    )
    assert response.status_code == 200, response.text
    with client.app.state.SessionLocal() as db:
        assert db.get(Repo, repo_id).publish_layers == "none"


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


def test_legacy_repo_with_no_recorded_consent_publishes_nothing():
    """A repo that never recorded a consent (`publish_layers IS NULL`) goes
    dark.

    Inverted deliberately: this used to pin the opposite — an unrecorded
    consent was unenforced, so a legacy repo kept publishing everything.
    Unrecorded is not permission, and it was the most permissive state in the
    system. The row is not backfilled; it simply publishes nothing until its
    owner records a scope, and the repos surface says so.
    """
    client = _client()
    token = _login(client)

    repo_id = _mint_legacy_repo(client, token, "Gurio/legacy")

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


def test_api_key_connect_without_publish_layers_records_the_opt_out_not_null():
    """The API-key surface can no longer mint an unrecorded consent.

    It used to leave the column NULL, which the gate then read as "publish
    everything" — an API-key client bypassed the consent gate entirely just by
    not mentioning it. Omitting the field now records the explicit opt-out.
    """
    client = _client()
    token = _login(client)
    repo_id = client.post(
        "/v1/accounts/repos",
        json={"repo_full_name": "Gurio/api-minted"},
        headers={"Authorization": f"Bearer {token}"},
    ).json()["repo_id"]
    with client.app.state.SessionLocal() as db:
        assert db.get(Repo, repo_id).publish_layers == publish_scope.OFF


def test_api_key_connect_records_an_explicit_consent_when_given_one():
    """…and a client that does name its scopes gets exactly those, through the
    same validator as the browser connect."""
    client = _client()
    token = _login(client)
    created = client.post(
        "/v1/accounts/repos",
        json={"repo_full_name": "Gurio/api-consented", "publish_layers": "quota,activity"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, created.text
    # Canonical order, and echoed back on the wire so the caller can see what
    # it consented to rather than having to infer it.
    assert created.json()["publish_layers"] == "activity,quota"
    with client.app.state.SessionLocal() as db:
        assert db.get(Repo, created.json()["repo_id"]).publish_layers == "activity,quota"


def test_api_key_connect_rejects_an_unknown_scope_token():
    """A typo is not a choice — same loud 422 the browser connect gives."""
    client = _client()
    token = _login(client)
    r = client.post(
        "/v1/accounts/repos",
        json={"repo_full_name": "Gurio/api-typo", "publish_layers": "totalnonsense"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422, r.text


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


# ── account-wide corpus: a legacy sibling narrows nothing, dissolves nothing ──


def _account_id(client: TestClient) -> str:
    with client.app.state.SessionLocal() as db:
        return db.query(Repo).first().account_id


def _mint_legacy_repo(client: TestClient, session_token: str, full_name: str) -> str:
    """A repo with `publish_layers IS NULL` — a row that predates the consent
    gate.

    Written to the column directly, because **no live endpoint can produce
    one any more**: the browser connect always recorded a value, and the
    account-API-key surface now records `publish_scope.DEFAULT_NEW_CONNECT`
    when the caller omits `publish_layers` instead of leaving the column
    unset. NULL is therefore purely a historical state — rows already in the
    database when the gate shipped, deliberately not backfilled, since writing
    a consent nobody gave would fabricate the evidence the gate exists to
    hold. This helper reproduces that state so the tests below can pin what
    such a row does now: nothing publishes.
    """
    repo_id = client.post(
        "/v1/accounts/repos",
        json={"repo_full_name": full_name},
        headers={"Authorization": f"Bearer {session_token}"},
    ).json()["repo_id"]
    with client.app.state.SessionLocal() as db:
        db.get(Repo, repo_id).publish_layers = None
        db.commit()
        assert db.get(Repo, repo_id).publish_layers is None
    return repo_id


def test_a_legacy_sibling_does_not_dissolve_a_recorded_none():
    """#715 B2, driven at the seam that ships. An account with one repo that
    recorded an explicit `none` and one legacy repo that recorded nothing must
    still ship no corpus. Unchanged by the fail-closed change, and now true for
    a stronger reason: the unrecorded sibling *vetoes* rather than abstaining,
    so the result is `frozenset()` whether or not the explicit `none` is there."""
    client = _client()
    token = _login(client)
    client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/opted-out", "publish_layers": "none"},
    )
    _mint_legacy_repo(client, token, "Gurio/legacy")

    with client.app.state.SessionLocal() as db:
        recorded = db.query(Repo).filter(Repo.repo_full_name == "Gurio/opted-out").one()
        repo_id, account_id = recorded.id, recorded.account_id

    daemon_token = _pair_daemon(client, repo_id)
    r = client.put(
        "/v1/daemons/surface",
        json={"files": [
            {"path": "surface/plan.md", "markdown": "# plan", "layer": "authored", "truncated": False},
            {"path": "knowledge/index.md", "markdown": "# kb", "layer": "knowledge", "truncated": False},
        ]},
        headers={"Authorization": f"Bearer {daemon_token}"},
    )
    assert r.status_code == 200
    assert r.json()["files"] == []
    with client.app.state.SessionLocal() as db:
        assert publish_scope.corpus_slices_permitted(db, account_id) == frozenset()


def test_legacy_only_account_publishes_no_corpus_at_all():
    """The former `SECURITY.md` carve-out, inverted: an account where *no* repo
    recorded a consent now ships **nothing**, where it used to ship everything.

    This is the change's blast radius stated plainly — a purely legacy account
    goes dark until its owner records a scope. That is the intended outcome,
    not collateral: those repos never answered the consent question, and the
    old behaviour answered it for them in the most permissive direction.
    """
    client = _client()
    token = _login(client)
    _mint_legacy_repo(client, token, "Gurio/legacy-a")
    repo_b = _mint_legacy_repo(client, token, "Gurio/legacy-b")

    with client.app.state.SessionLocal() as db:
        assert publish_scope.corpus_slices_permitted(db, _account_id(client)) == frozenset()

    daemon_token = _pair_daemon(client, repo_b)
    r = client.put(
        "/v1/daemons/surface",
        json={"files": [
            {"path": "surface/plan.md", "markdown": "# plan", "layer": "authored", "truncated": False},
            {"path": "knowledge/index.md", "markdown": "# kb", "layer": "knowledge", "truncated": False},
        ]},
        headers={"Authorization": f"Bearer {daemon_token}"},
    )
    assert r.status_code == 200
    assert r.json()["files"] == []


def test_recorded_consents_intersect_and_a_legacy_sibling_narrows_to_nothing():
    """The all-recorded path is unchanged (plain intersection). Adding a repo
    with no recorded consent now narrows the result to nothing rather than
    leaving it alone — the unrecorded row participates as `OFF`.

    This is #715's rule (*enforcement must not weaken when a repo is added*)
    reaching the one row that used to be exempt from it.
    """
    client = _client()
    token = _login(client)
    client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/a", "publish_layers": "authored,knowledge"},
    )
    client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/b", "publish_layers": "knowledge,runs"},
    )
    account_id = _account_id(client)
    with client.app.state.SessionLocal() as db:
        assert publish_scope.corpus_slices_permitted(db, account_id) == frozenset({"knowledge"})

    _mint_legacy_repo(client, token, "Gurio/legacy")
    with client.app.state.SessionLocal() as db:
        assert publish_scope.corpus_slices_permitted(db, account_id) == frozenset()


def test_account_with_no_repos_at_all_is_unenforced():
    """Unchanged: nothing connected means nothing to enforce against."""
    client = _client()
    _login(client)
    with client.app.state.SessionLocal() as db:
        assert publish_scope.corpus_slices_permitted(db, "acc-nonexistent") is None


# ── dashboard read-side consent explanation ────────────────────────


def _connected_repos(db, account_id: str) -> list[Repo]:
    """The list the dashboard endpoints already hold when they ask.

    `lanes_withheld` and `repos_without_publish_consent` take the repo list
    rather than a Session on purpose: they are asked once per lane per 2 s
    poll, and a signature that re-queried would spend a round trip per lane
    per tick to answer "not withheld".
    """
    return list(db.query(Repo).filter(Repo.account_id == account_id))



def test_no_repos_proves_no_lane_is_withheld():
    client = _client()
    _login(client)
    with client.app.state.SessionLocal() as db:
        account_id = db.query(Account.id).one()[0]
        assert publish_scope.lanes_withheld(_connected_repos(db, account_id)) == frozenset()


def test_one_unrecorded_repo_withholds_every_lane():
    client = _client()
    token = _login(client)
    _mint_legacy_repo(client, token, "Gurio/legacy")
    with client.app.state.SessionLocal() as db:
        assert publish_scope.lanes_withheld(_connected_repos(db, _account_id(client))) == frozenset(
            publish_scope.LANES
        )


def test_one_repo_permitting_activity_leaves_only_activity_ambiguous():
    client = _client()
    _login(client)
    client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/active", "publish_layers": "activity"},
    )
    with client.app.state.SessionLocal() as db:
        withheld = publish_scope.lanes_withheld(_connected_repos(db, _account_id(client)))
    assert "activity" not in withheld
    assert withheld == frozenset(publish_scope.LANES) - {"activity"}


def test_any_permitting_repo_keeps_a_lane_out_of_withheld():
    client = _client()
    _login(client)
    client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/active", "publish_layers": "activity"},
    )
    client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/off", "publish_layers": "none"},
    )
    with client.app.state.SessionLocal() as db:
        withheld = publish_scope.lanes_withheld(_connected_repos(db, _account_id(client)))
    assert "activity" not in withheld


def test_consent_absence_distinguishes_unrecorded_from_explicit_none():
    client = _client()
    token = _login(client)
    _mint_legacy_repo(client, token, "Gurio/legacy")
    client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/off", "publish_layers": "none"},
    )
    with client.app.state.SessionLocal() as db:
        absence = publish_scope.repos_without_publish_consent(_connected_repos(db, _account_id(client)))
    assert absence.unrecorded == ("Gurio/legacy",)
    assert absence.opted_out == ("Gurio/off",)


_WITHHELD_DASHBOARD_LANES = [
    ("activity", "/v1/dashboard/activity"),
    ("live_runs", "/v1/dashboard/live-runs"),
    ("run_ledger", "/v1/dashboard/run-ledger"),
    ("corpus", "/v1/dashboard/surface"),
    ("quota", "/v1/dashboard/quota"),
    ("runners", "/v1/dashboard/runners"),
    ("pr_review_queue", "/v1/dashboard/pr-review-queue"),
]


@pytest.mark.parametrize(
    "lane,path",
    _WITHHELD_DASHBOARD_LANES,
    ids=[lane for lane, _path in _WITHHELD_DASHBOARD_LANES],
)
def test_dashboard_empty_lane_marks_consent_without_repeating_account_state(lane, path):
    client = _client()
    token = _login(client)
    _mint_legacy_repo(client, token, "Gurio/legacy")
    client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/off", "publish_layers": "none"},
    )

    body = client.get(path).json()

    assert body["withheld"] == {
        "lane": lane,
        "unrecorded": ["Gurio/legacy"],
        "opted_out": ["Gurio/off"],
    }


def test_withheld_marker_omits_an_absence_category_that_is_empty():
    """Same rule as the marker itself: absent, never an empty list.

    An empty `unrecorded` and a missing one would render identically to a
    careless reader and differently to a careful one — and the careful reading
    ("we checked, nobody is unrecorded") is the true one only when we did.
    """
    client = _client()
    _login(client)
    client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/off", "publish_layers": "none"},
    )

    marker = client.get("/v1/dashboard/activity").json()["withheld"]

    assert marker == {"lane": "activity", "opted_out": ["Gurio/off"]}
    assert "unrecorded" not in marker


def test_dashboard_omits_withheld_when_any_repo_permits_the_lane():
    client = _client()
    _login(client)
    client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/active", "publish_layers": "activity"},
    )
    client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/off", "publish_layers": "none"},
    )

    assert "withheld" not in client.get("/v1/dashboard/activity").json()


def test_corpus_is_withheld_when_a_sibling_repo_narrows_the_intersection():
    """The mixed-consent case: one repo fully consented, one never asked.

    Found live 2026-07-27. An account had `hugimuni-labs/brnrd` consenting to
    every lane including `corpus`, and one forgotten repo with a daemon that
    had never woken and no recorded scope. The write seam intersects corpus
    slices across every connected repo, so all 2,863 files were dropped — and
    `lanes_withheld` answered with a *union*, said "not withheld", and the
    dashboard rendered a bare "No corpus mirrored yet." for a corpus that was
    being refused every three seconds.

    The two must agree: the marker is only worth anything in exactly the case
    where a permitting repo exists, because that is the case a reader cannot
    diagnose from their own consent screen.
    """
    client = _client()
    token = _login(client)
    client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/consented", "publish_layers": "corpus"},
    )
    _mint_legacy_repo(client, token, "Gurio/forgotten")

    with client.app.state.SessionLocal() as db:
        account_id = _account_id(client)
        repos = _connected_repos(db, account_id)
        # The enforcement side: nothing may ship.
        assert publish_scope.corpus_slices_permitted(db, account_id) == frozenset()
        # A repo *does* permit the lane — this is what the union saw.
        permitted_by_someone = frozenset().union(
            *(publish_scope._repo_scopes(repo.publish_layers)[0] for repo in repos)
        )
        assert "corpus" in permitted_by_someone
        # ...and the explanation must still agree with the gate that runs.
        assert "corpus" in publish_scope.lanes_withheld(repos)

    marker = client.get("/v1/dashboard/surface").json()["withheld"]
    assert marker["lane"] == "corpus"
    assert marker["unrecorded"] == ["Gurio/forgotten"], (
        "the panel has to name the repo whose consent is missing — the owner "
        "cannot act on a condition without the subject of the sentence"
    )


def test_corpus_is_not_withheld_when_every_repo_agrees_on_a_slice():
    """The inverse, so the fix cannot become "corpus is always withheld"."""
    client = _client()
    _login(client)
    client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/one", "publish_layers": "corpus"},
    )
    client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/two", "publish_layers": "knowledge"},
    )

    with client.app.state.SessionLocal() as db:
        account_id = _account_id(client)
        assert publish_scope.corpus_slices_permitted(db, account_id) == frozenset({"knowledge"})
        assert "corpus" not in publish_scope.lanes_withheld(_connected_repos(db, account_id))

    assert "withheld" not in client.get("/v1/dashboard/surface").json()


def test_sliced_lanes_matches_the_lanes_slice_enforcement_governs():
    """`SLICED_LANES` is a hand-named set; this is what stops it going stale.

    `lanes_withheld` reduces sliced lanes by intersection and every other lane
    by union, because that is how each is enforced. A second sliced lane added
    to the vocabulary without being added here would silently inherit the union
    and reintroduce the exact defect above — so pin the membership against the
    slice vocabulary itself rather than trusting the next reader to notice.
    """
    assert publish_scope.SLICED_LANES <= frozenset(publish_scope.LANES)
    assert publish_scope.SLICED_LANES == frozenset({"corpus"}), (
        "corpus is the only account-wide lane today. If that changed, "
        "`lanes_withheld` and `corpus_slices_permitted` both need the new lane."
    )
    # And the slice vocabulary is the corpus lane's, not a free-floating set.
    assert frozenset(cloud._PUBLISH_CORPUS_SLICES) == frozenset({"authored", "knowledge", "runs"})


def test_a_recorded_consent_is_an_enumeration_and_never_widens():
    """Replaces `test_a_future_layer_ships_dark_for_an_existing_consent`, which
    was vacuous — and not for the reason first filed.

    The original monkeypatched `cloud._PUBLISH_TICK_ORDER` and the note on the
    issue claimed the rebinding never reached the code because `publish_scope`
    bound `LANES` at import. That reasoning is wrong: the assertion runs through
    `lane_permitted` -> `_repo_scopes` -> `cloud._resolve_publish_scopes`, whose
    `__module__` is `brr.gates.cloud`, so it reads `_PUBLISH_TICK_ORDER` out of
    *cloud's* globals at call time (`cloud.py:465,478`) and the patch lands fine.

    It was vacuous for a plainer reason: the repo consented to `"quota"`, so
    `"widget_lane" in lanes` was `False` patched **and** unpatched. Apply the
    only question that catches this class — *would this assertion change if I
    deleted the patch?* — and the whole test evaporates.

    What is left worth pinning needs no patch at all: a recorded consent is an
    **enumeration**, so every lane it does not name is dark. A regression that
    made a recorded consent fail open (the `None`-vs-`"none"` confusion this
    module exists to keep straight) fails here on all five unnamed lanes. The
    *other* thing the original claimed to catch — a new handler shipping with
    no gate — is structural and lives in
    `test_every_daemon_put_lane_is_gated` below.
    """
    client = _client()
    _login(client)
    client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/new", "publish_layers": "quota"},
    )
    with client.app.state.SessionLocal() as db:
        repo_id = db.query(Repo).filter(Repo.repo_full_name == "Gurio/new").one().id

        assert publish_scope.lane_permitted(db, repo_id=repo_id, lane="quota") is True
        dark = {
            lane
            for lane in cloud._PUBLISH_TICK_ORDER
            if not publish_scope.lane_permitted(db, repo_id=repo_id, lane=lane)
        }
        assert dark == set(cloud._PUBLISH_TICK_ORDER) - {"quota"}, (
            "a recorded consent must name every lane it permits; anything it "
            f"does not name ships dark. Reachable but unnamed: {sorted(set(cloud._PUBLISH_TICK_ORDER) - {'quota'} - dark)}"
        )


# ── #714: the consent names the subject, not the publisher ──────────
#
# Every enforcement test above this line connects exactly one repo, which is
# precisely why the suite could not see #714: with one repo, the payload's
# subject and the token's owner are the same row, and the bug lives in the gap
# between them. A fixture picks a moment, a caller — and a **cardinality**.


def _two_repos_one_token(client: TestClient, *, opted_out_consent: str = "none") -> dict:
    """One account, two repos, one daemon token paired to the permissive one.

    `Gurio/secret` records `none` (an explicit, recorded opt-out — not the
    legacy `NULL` gap). `Gurio/public` consents to all three subject-bearing
    lanes and owns the token that does every PUT below.
    """
    _login(client)
    client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/secret", "publish_layers": opted_out_consent},
    )
    client.post(
        "/v1/repos/connect",
        json={
            "repo_full_name": "Gurio/public",
            "publish_layers": "live_runs,pr_review_queue,run_ledger",
        },
    )
    with client.app.state.SessionLocal() as db:
        public_id = db.query(Repo).filter(Repo.repo_full_name == "Gurio/public").one().id
    token = _pair_daemon(client, public_id)
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/v1/daemons/register", json={"daemon_name": "laptop"}, headers=headers)
    return headers


def _live_runs_payload(*labels: str) -> dict:
    return {
        "runs": [
            {
                "id": f"run-{i}",
                "repo_label": label,
                "card_text": f"card text for {label}",
            }
            for i, label in enumerate(labels)
        ]
    }


def _pr_queue_payload(*labels: str) -> dict:
    return {
        "prs": [
            {"number": i + 1, "title": f"PR title for {label}", "repo_label": label}
            for i, label in enumerate(labels)
        ]
    }


def _run_ledger_payload(*labels: str) -> dict:
    return {
        "rows": [
            {"run_id": f"led-{i}", "repo_label": label, "name": f"ledger name for {label}"}
            for i, label in enumerate(labels)
        ]
    }


# (lane label, PUT path, payload builder, stored key, dashboard path, dashboard key)
_SUBJECT_LANES = [
    ("live_runs", "/v1/daemons/live-runs", _live_runs_payload, "runs", "/v1/dashboard/live-runs", "runs"),
    ("pr_review_queue", "/v1/daemons/pr-review-queue", _pr_queue_payload, "prs", "/v1/dashboard/pr-review-queue", "prs"),
    ("run_ledger", "/v1/daemons/run-ledger", _run_ledger_payload, "rows", "/v1/dashboard/run-ledger", "rows"),
]


@pytest.mark.parametrize(
    "lane,put_path,payload_of,stored_key,dash_path,dash_key",
    _SUBJECT_LANES,
    ids=[row[0] for row in _SUBJECT_LANES],
)
def test_a_sibling_token_cannot_publish_a_repo_that_recorded_none(
    lane, put_path, payload_of, stored_key, dash_path, dash_key
):
    """#714, driven per lane. Two repos in one account; the permissive repo's
    token does the PUT and the payload names both repos.

    The opted-out repo's row must be absent from what is stored **and** from
    what the dashboard read returns — `dashboard.py:448` re-aggregates by
    account (`Daemon.repo_id.in_(repo_ids)`), so the stored row is the fact but
    the dashboard is where it becomes a disclosure. Asserting only on the PUT
    response would miss a fix that filtered the echo and not the storage.

    The permissive repo's row is the positive control in the same assertion:
    without it this test passes just as well against a handler that was never
    wired at all, which is the standard way this shape goes green over nothing.
    """
    client = _client()
    headers = _two_repos_one_token(client)

    r = client.put(put_path, json=payload_of("Gurio/secret", "Gurio/public"), headers=headers)
    assert r.status_code == 200, r.text

    stored_labels = [row["repo_label"] for row in r.json()[stored_key]]
    assert stored_labels == ["Gurio/public"], (
        f"{lane}: a token owned by Gurio/public published rows about Gurio/secret, "
        f"which recorded an explicit 'none' — stored {stored_labels}"
    )

    served = client.get(dash_path).json()[dash_key]
    served_labels = [row.get("repo_label") for row in served]
    assert "Gurio/secret" not in served_labels, (
        f"{lane}: the opted-out repo reached the dashboard read — {served_labels}"
    )
    # Positive control: the consenting repo's row must actually land, content
    # and all. A gate that drops everything is not a fix.
    assert "Gurio/public" in served_labels, (
        f"{lane}: the consenting repo's rows went dark too — {served_labels}"
    )


@pytest.mark.parametrize(
    "lane,put_path,payload_of,stored_key,dash_path,dash_key",
    _SUBJECT_LANES,
    ids=[row[0] for row in _SUBJECT_LANES],
)
def test_a_row_naming_a_repo_with_no_recorded_consent_goes_dark(
    lane, put_path, payload_of, stored_key, dash_path, dash_key
):
    """The former carve-out pin, inverted.

    `lane_permitted` used to fail **open** for a repo that never recorded a
    consent, and this test pinned that as disclosed behaviour (`SECURITY.md`,
    re-litigated in #715 / `64925c11`). The product decision changed: an
    unrecorded consent is not permission, so a row *about* such a repo is
    dropped while the consenting publisher's own rows still ship.

    The publisher's rows staying visible is the part that still matters — the
    change must narrow to the unconsented subject, not blank the whole payload.
    """
    client = _client()
    token = _login(client)
    client.post(
        "/v1/repos/connect",
        json={
            "repo_full_name": "Gurio/public",
            "publish_layers": "live_runs,pr_review_queue,run_ledger",
        },
    )
    _mint_legacy_repo(client, token, "Gurio/legacy")
    with client.app.state.SessionLocal() as db:
        public_id = db.query(Repo).filter(Repo.repo_full_name == "Gurio/public").one().id
    headers = {"Authorization": f"Bearer {_pair_daemon(client, public_id)}"}
    client.post("/v1/daemons/register", json={"daemon_name": "laptop"}, headers=headers)

    r = client.put(put_path, json=payload_of("Gurio/legacy", "Gurio/public"), headers=headers)
    assert r.status_code == 200, r.text
    assert [row["repo_label"] for row in r.json()[stored_key]] == [
        "Gurio/public",
    ], f"{lane}: expected only the consenting repo's rows to ship"

    served = [row.get("repo_label") for row in client.get(dash_path).json()[dash_key]]
    assert "Gurio/legacy" not in served, (
        f"{lane}: a repo with no recorded consent reached the dashboard — {served}"
    )
    assert "Gurio/public" in served, (
        f"{lane}: the consenting repo's rows went dark too — {served}"
    )


@pytest.mark.parametrize(
    "lane,put_path,payload_of,stored_key,dash_path,dash_key",
    _SUBJECT_LANES,
    ids=[row[0] for row in _SUBJECT_LANES],
)
def test_a_row_naming_an_unknown_label_falls_back_to_the_publishers_consent(
    lane, put_path, payload_of, stored_key, dash_path, dash_key
):
    """The other half of the carve-out, and the one place this diff deliberately
    does *not* fail open.

    `repo_label` arrives from the client. A label naming no repo in the token's
    account resolves to nothing, so there is no subject consent to read — and
    the question that was being asked before this diff existed is exactly the
    right fallback: the **publisher's** consent. With a permissive publisher
    the row ships, which is today's behaviour unchanged (delete the fix and
    this stays green).

    Failing *open* on an unresolvable label instead — the literal reading of
    `lane_permitted`'s "unknown repo => True" — would have been a widening, not
    a preservation: a daemon whose own repo recorded `none` could then ship any
    row it liked by misspelling the label. `test_an_unknown_label_cannot_outrun_
    the_publishers_own_opt_out` below drives that direction.
    """
    client = _client()
    headers = _two_repos_one_token(client)

    r = client.put(put_path, json=payload_of("Gurio/never-connected", "Gurio/public"), headers=headers)
    assert r.status_code == 200, r.text
    assert [row["repo_label"] for row in r.json()[stored_key]] == [
        "Gurio/never-connected",
        "Gurio/public",
    ], f"{lane}: an unknown label went dark under a permissive publisher"

    served = [row.get("repo_label") for row in client.get(dash_path).json()[dash_key]]
    assert "Gurio/never-connected" in served, f"{lane}: unknown-label row missing — {served}"


@pytest.mark.parametrize(
    "lane,put_path,payload_of,stored_key,dash_path,dash_key",
    _SUBJECT_LANES,
    ids=[row[0] for row in _SUBJECT_LANES],
)
def test_an_unknown_label_cannot_outrun_the_publishers_own_opt_out(
    lane, put_path, payload_of, stored_key, dash_path, dash_key
):
    """A label the server cannot resolve must not buy a *wider* audience than
    the token that carried it.

    The publisher itself recorded `none`, so nothing it sends ships today. A
    row naming an unresolvable label must not change that — otherwise the
    consent is bypassable by typo, and this diff would have widened the
    disclosure surface while claiming to narrow it.
    """
    client = _client()
    _login(client)
    client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/secret", "publish_layers": "none"},
    )
    with client.app.state.SessionLocal() as db:
        secret_id = db.query(Repo).filter(Repo.repo_full_name == "Gurio/secret").one().id
    headers = {"Authorization": f"Bearer {_pair_daemon(client, secret_id)}"}
    client.post("/v1/daemons/register", json={"daemon_name": "laptop"}, headers=headers)

    r = client.put(put_path, json=payload_of("Gurio/never-connected"), headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()[stored_key] == [], (
        f"{lane}: a publisher that recorded 'none' shipped a row by naming an "
        "unresolvable repo_label"
    )


@pytest.mark.parametrize(
    "lane,put_path,payload_of,stored_key,dash_path,dash_key",
    _SUBJECT_LANES,
    ids=[row[0] for row in _SUBJECT_LANES],
)
def test_a_label_from_another_account_does_not_borrow_that_accounts_consent(
    lane, put_path, payload_of, stored_key, dash_path, dash_key
):
    """The label->Repo lookup is scoped to `principal.account_id`.

    `repo_label` is client-supplied. An account-wide-unscoped lookup would let
    a token name a *stranger's* repo and read its consent — and, worse, publish
    under a label that account owns. Here the intruder's `Gurio/secret` is
    permissive, and the row still must not inherit it: the resolution finds
    nothing in this account, falls back to this publisher, which recorded
    `none`.
    """
    client = _client()
    _login(client, github_id="999", login="stranger")
    client.post(
        "/v1/repos/connect",
        json={
            "repo_full_name": "Gurio/secret",
            "publish_layers": "live_runs,pr_review_queue,run_ledger",
        },
    )

    _login(client, github_id="12345", login="Gurio")
    client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/mine", "publish_layers": "none"},
    )
    with client.app.state.SessionLocal() as db:
        mine_id = (
            db.query(Repo)
            .filter(Repo.repo_full_name == "Gurio/mine")
            .one()
            .id
        )
    headers = {"Authorization": f"Bearer {_pair_daemon(client, mine_id)}"}
    client.post("/v1/daemons/register", json={"daemon_name": "laptop"}, headers=headers)

    r = client.put(put_path, json=payload_of("Gurio/secret"), headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()[stored_key] == [], (
        f"{lane}: a row borrowed another account's repo consent by naming its label"
    )


@pytest.mark.parametrize(
    "lane,put_path,payload_of,stored_key,dash_path,dash_key",
    _SUBJECT_LANES,
    ids=[row[0] for row in _SUBJECT_LANES],
)
def test_a_case_mismatched_label_still_resolves_to_its_repo(
    lane, put_path, payload_of, stored_key, dash_path, dash_key
):
    """One character of case must not reopen #714.

    The two sides of this comparison have different provenance. The `Repo` row
    records what the connect payload said; the daemon derives `repo_label` by
    parsing the **git remote URL** (`cloud.py::_github_repo_label` ->
    `parse_origin_url`). GitHub serves repos case-insensitively, so a clone of
    `.../gurio/secret` produces a label that parses to `gurio/secret` while the
    connect record says `Gurio/secret`. Exact matching fails to resolve there,
    and an unresolved subject falls back to the publisher — which is #714's own
    defect wearing a different coat.

    Not theoretical, and the codebase had already decided this twice: this
    lane's own producer dedups its labels by `repo_label.casefold()`
    (`cloud.py::_pr_review_repo_labels`), and `webhooks.py::_find_repo` matches
    the same way. The producer folded and the consumer did not.
    """
    client = _client()
    headers = _two_repos_one_token(client)

    r = client.put(put_path, json=payload_of("gurio/secret", "Gurio/public"), headers=headers)
    assert r.status_code == 200, r.text
    assert [row["repo_label"] for row in r.json()[stored_key]] == ["Gurio/public"], (
        f"{lane}: 'gurio/secret' did not resolve to the connected 'Gurio/secret', so "
        "the opted-out repo's rows fell through to the publisher's consent"
    )

    served = [row.get("repo_label") for row in client.get(dash_path).json()[dash_key]]
    assert not any((s or "").casefold() == "gurio/secret" for s in served), (
        f"{lane}: the opted-out repo reached the dashboard under a case variant — {served}"
    )
    assert "Gurio/public" in served, f"{lane}: positive control lost — {served}"


@pytest.mark.parametrize(
    "lane,put_path,payload_of,stored_key,dash_path,dash_key",
    _SUBJECT_LANES,
    ids=[row[0] for row in _SUBJECT_LANES],
)
def test_case_variant_siblings_must_all_consent(
    lane, put_path, payload_of, stored_key, dash_path, dash_key
):
    """Folding the match creates an ambiguity, and it resolves the strict way.

    `Repo` is unique on `(account_id, repo_full_name)` (`models.py:46`) but that
    constraint is case-*sensitive*, and so is the dedup in
    `accounts.py::create_repo` — so one account can genuinely hold `Gurio/x` and
    `gurio/x` as two rows with two different recorded consents. A folded lookup
    therefore has to answer for a *set*, not a row.

    The rule is #715's, pointed at a new axis — **enforcement must not weaken
    when a repo is added**: the row publishes only if every case variant
    permits the lane. Resolving the ambiguity by giving up instead
    (`_find_repo`'s `len(matches) == 1` shape) would fall through to the
    publisher, and connecting a second repo would then *widen* what the first
    had shut.

    This also pins that the lookup cannot raise: an unfolded
    `scalar_one_or_none()` over a folded predicate would throw
    `MultipleResultsFound` on a live request, turning a consent check into a
    500.
    """
    client = _client()
    _login(client)
    client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/dup", "publish_layers": "none"},
    )
    client.post(
        "/v1/repos/connect",
        json={
            "repo_full_name": "gurio/dup",
            "publish_layers": "live_runs,pr_review_queue,run_ledger",
        },
    )
    client.post(
        "/v1/repos/connect",
        json={
            "repo_full_name": "Gurio/public",
            "publish_layers": "live_runs,pr_review_queue,run_ledger",
        },
    )
    with client.app.state.SessionLocal() as db:
        variants = db.query(Repo).filter(Repo.repo_full_name.in_(["Gurio/dup", "gurio/dup"])).all()
        assert len(variants) == 2, (
            "this account no longer holds two case variants — the premise of this "
            "test moved; check accounts.py::create_repo's dedup"
        )
        public_id = db.query(Repo).filter(Repo.repo_full_name == "Gurio/public").one().id
    headers = {"Authorization": f"Bearer {_pair_daemon(client, public_id)}"}
    client.post("/v1/daemons/register", json={"daemon_name": "laptop"}, headers=headers)

    # Either spelling names a set containing the `none` sibling ⇒ neither ships.
    for spelling in ("Gurio/dup", "gurio/dup"):
        r = client.put(put_path, json=payload_of(spelling, "Gurio/public"), headers=headers)
        assert r.status_code == 200, r.text
        assert [row["repo_label"] for row in r.json()[stored_key]] == ["Gurio/public"], (
            f"{lane}: {spelling!r} published although a case variant of it recorded 'none'"
        )

    served = [row.get("repo_label") for row in client.get(dash_path).json()[dash_key]]
    assert not any((s or "").casefold() == "gurio/dup" for s in served), (
        f"{lane}: a case-variant pair reached the dashboard — {served}"
    )
    assert "Gurio/public" in served, f"{lane}: positive control lost — {served}"


def test_mixed_payload_is_filtered_not_rejected():
    """Shape pin: the request still succeeds and keeps its permitted rows.

    A 4xx here would be a different contract — the daemon publishes on a tick
    and has no per-row retry path, so rejecting the whole payload would take
    the consenting repo's rows down with the opted-out one's.
    """
    client = _client()
    headers = _two_repos_one_token(client)
    r = client.put(
        "/v1/daemons/live-runs",
        json=_live_runs_payload("Gurio/secret", "Gurio/public", "Gurio/secret"),
        headers=headers,
    )
    assert r.status_code == 200
    assert [row["repo_label"] for row in r.json()["runs"]] == ["Gurio/public"]


def test_a_row_naming_no_repo_at_all_falls_back_to_the_publisher():
    """`repo_label` is optional on every one of these schemas (default `""` on
    `LiveRunIn`/`PRReviewItemIn`, `None` on `RunLedgerRowIn`). A row naming no
    subject is gated on the publisher — the token identifies who is speaking
    even when the row does not say who it is about.
    """
    client = _client()
    _login(client)
    client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/secret", "publish_layers": "none"},
    )
    with client.app.state.SessionLocal() as db:
        secret_id = db.query(Repo).filter(Repo.repo_full_name == "Gurio/secret").one().id
    headers = {"Authorization": f"Bearer {_pair_daemon(client, secret_id)}"}
    client.post("/v1/daemons/register", json={"daemon_name": "laptop"}, headers=headers)

    r = client.put("/v1/daemons/live-runs", json={"runs": [{"id": "r1"}]}, headers=headers)
    assert r.status_code == 200
    assert r.json()["runs"] == []


def test_daemon_scoped_lanes_stay_keyed_to_the_publishing_token():
    """The lanes #714 deliberately did **not** touch, pinned so a later
    "unify the predicate" pass has to argue with a test.

    `activity`, `quota` and `runners` carry daemon-scoped payloads whose rows
    name no repo — `ActivityRecordIn` has no `repo_label` and the row is
    stored under `principal.repo_id` by the handler itself. For those, the
    publishing token's repo *is* the subject, so token-keyed is the correct
    question. Reusing one predicate for uniformity is exactly how the subject
    and the publisher ended up on the same key.
    """
    client = _client()
    _login(client)
    client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/secret", "publish_layers": "none"},
    )
    client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/public", "publish_layers": "activity,quota,runners"},
    )
    with client.app.state.SessionLocal() as db:
        secret_id = db.query(Repo).filter(Repo.repo_full_name == "Gurio/secret").one().id
    # Token owned by the opted-out repo, with a permissive sibling in the same
    # account: the sibling must not lend its consent either.
    headers = {"Authorization": f"Bearer {_pair_daemon(client, secret_id)}"}
    client.post("/v1/daemons/register", json={"daemon_name": "laptop"}, headers=headers)

    r = client.put(
        "/v1/daemons/quota",
        json={"shells": [{"shell": "claude", "windows": []}], "gates": []},
        headers=headers,
    )
    assert r.status_code == 200 and r.json()["shells"] == []

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
    assert r.status_code == 200 and r.json()["activity"] == []


def test_daemon_mood_stays_all_or_nothing_on_the_publishing_token():
    """`daemon_mood` is the daemon's own face, not any repo's content — it has
    no `repo_label`, so there is no subject to key it to and the publishing
    token's consent is the only one that can speak for it. Documented here
    rather than left implicit, because it is the one field on the live-runs
    lane that #714 left token-keyed on purpose.
    """
    client = _client()
    _login(client)
    client.post(
        "/v1/repos/connect",
        json={"repo_full_name": "Gurio/secret", "publish_layers": "none"},
    )
    with client.app.state.SessionLocal() as db:
        secret_id = db.query(Repo).filter(Repo.repo_full_name == "Gurio/secret").one().id
    headers = {"Authorization": f"Bearer {_pair_daemon(client, secret_id)}"}
    client.post("/v1/daemons/register", json={"daemon_name": "laptop"}, headers=headers)

    client.put(
        "/v1/daemons/live-runs",
        json={
            "runs": [],
            "daemon_mood": {"state": "busy", "name": "grim", "glyph": "b_d", "pitch": 0.5},
        },
        headers=headers,
    )
    assert client.get("/v1/dashboard/live-runs").json()["daemon_mood"] is None


# ── the server-side registration<->gate binding ─────────────────────


def test_every_daemon_put_lane_is_gated():
    """Server analogue of `test_every_publisher_is_a_registered_gated_lane`
    (`tests/test_cloud_gate.py:2621`).

    The daemon side binds registration and gating into one thing: `@_publish_lane`
    is the only way into the tick's registry and the same decorator gates it, so
    an ungated lane is not expressible. The server has no such binding — six
    literal `lane="…"` strings and a router that will happily mount a seventh
    handler with no gate at all. That gap is the whole reason #714's three lanes
    could be miskeyed in the first place and nothing complained.

    The route set is derived from the **router object**, never hand-listed: a
    hand-written list meets the member nobody listed, and a seventh lane must
    fail this test with no edit to it. That property is the deliverable — see
    the report for this test driven red against a deliberately ungated lane.

    Gating is read off the handler's own bytecode (`co_names` carries every
    global and attribute name the function body touches) rather than its source
    text, so a comment mentioning `lane_permitted` cannot satisfy it.
    """
    from fastapi.routing import APIRoute

    from brnrd.routers import daemons as daemons_router

    # Named-with-a-reason exemptions only. Empty is the honest state today:
    # every PUT under /v1/daemons/* publishes repo-derived content and every
    # one of them gates. A future entry here owes a sentence saying why the
    # payload carries nothing a repo could have consented against.
    exempt: dict[str, str] = {}

    gate_names = {"lane_permitted", "permitted_rows", "corpus_slices_permitted"}
    put_routes = {
        route.path: route.endpoint
        for route in daemons_router.router.routes
        if isinstance(route, APIRoute) and "PUT" in route.methods
    }
    assert put_routes, "derived no PUT routes from the daemons router — the derivation broke"

    ungated = sorted(
        path
        for path, endpoint in put_routes.items()
        if path not in exempt and not (gate_names & set(endpoint.__code__.co_names))
    )
    assert not ungated, (
        "every PUT /v1/daemons/* handler must consult brnrd.publish_scope or be "
        f"on the exemption list with a stated reason; ungated: {ungated}"
    )

    stale = sorted(set(exempt) - set(put_routes))
    assert not stale, f"exemption list names routes that no longer exist: {stale}"


def _subject_bearing_put_lanes() -> dict[str, list[str]]:
    """Derive, from the router and the payload schemas, which PUT lanes carry
    rows that name their own repo.

    Both halves are derived, never listed: the routes come off `router.routes`
    and the "does this payload name a subject" question is answered by walking
    the endpoint's own request model for a list of items with a `repo_label`
    field. A seventh lane is classified correctly the moment it is mounted,
    with no edit here — which is the only version of this test that catches the
    lane nobody remembered to add to a list.
    """
    import typing

    from fastapi.routing import APIRoute
    from pydantic import BaseModel

    from brnrd.routers import daemons as daemons_router

    found: dict[str, list[str]] = {}
    for route in daemons_router.router.routes:
        if not (isinstance(route, APIRoute) and "PUT" in route.methods):
            continue
        hints = typing.get_type_hints(route.endpoint)
        model = next(
            (t for t in hints.values() if isinstance(t, type) and issubclass(t, BaseModel)),
            None,
        )
        if model is None:
            continue
        carriers = [
            item.__name__
            for field in model.model_fields.values()
            for item in typing.get_args(field.annotation)
            if isinstance(item, type)
            and issubclass(item, BaseModel)
            and "repo_label" in item.model_fields
        ]
        # A lane that isolates per row (#685) cannot type its list of rows —
        # a typed item annotation means FastAPI rejects the whole batch before
        # the handler runs, which is the outage that change exists to close.
        # Those lanes declare their row model as `ROW_MODEL` instead, so the
        # row shape stays readable *here* rather than only in a comment. Both
        # spellings are walked; neither is a special case for one path.
        row_model = getattr(model, "ROW_MODEL", None)
        if (
            isinstance(row_model, type)
            and issubclass(row_model, BaseModel)
            and "repo_label" in row_model.model_fields
        ):
            carriers.append(row_model.__name__)
        if carriers:
            found[route.path] = carriers
    return found


def test_a_row_isolating_report_declares_its_row_model():
    """The pin under the derivation above, and the reason it is not a special
    case for `/v1/daemons/live-runs`.

    Per-row isolation (#685) requires erasing the item type from the request
    model — and that erasure is precisely what hid the row shape from #714's
    per-row-consent derivation, which reads item types to answer "does this
    lane's payload name its own repo". The lane stayed correct; the test that
    proves it went blind. #722's shape exactly: a guard anchored one level off
    reads like no guard at all.

    So: a PUT report whose rows are untyped must say what they are. The next
    lane to adopt isolation fails here instead of silently un-testing itself.
    """
    import typing

    from fastapi.routing import APIRoute
    from pydantic import BaseModel

    from brnrd.routers import daemons as daemons_router

    untyped = []
    for route in daemons_router.router.routes:
        if not (isinstance(route, APIRoute) and "PUT" in route.methods):
            continue
        hints = typing.get_type_hints(route.endpoint)
        model = next(
            (t for t in hints.values() if isinstance(t, type) and issubclass(t, BaseModel)),
            None,
        )
        if model is None:
            continue
        for name, field in model.model_fields.items():
            if typing.get_origin(field.annotation) is not list:
                continue
            (item,) = typing.get_args(field.annotation) or (None,)
            if item is not typing.Any:
                continue
            row_model = getattr(model, "ROW_MODEL", None)
            if not (isinstance(row_model, type) and issubclass(row_model, BaseModel)):
                untyped.append(f"{model.__name__}.{name} ({route.path})")

    assert not untyped, (
        "these PUT payloads carry an untyped list of rows and declare no "
        "`ROW_MODEL`, so every derivation that reads row shape off the request "
        f"model is blind to them: {untyped}"
    )


def test_a_lane_whose_rows_name_a_repo_must_gate_per_row():
    """The narrower half of the binding above: knowing a lane is *gated* is not
    knowing it is gated on the **right key**. #714 was three gated lanes all
    asking the wrong question.

    The rule, stated once: a payload whose rows carry `repo_label` must use the
    per-row predicate, and a payload whose rows do not must not — for those the
    publishing token's repo genuinely *is* the subject, and reusing one
    predicate for uniformity is exactly how the subject and the publisher ended
    up on the same key. Both sides are asserted so a later "unify the
    predicates" pass has to argue with a test in either direction.
    """
    from fastapi.routing import APIRoute

    from brnrd.routers import daemons as daemons_router

    subject_bearing = _subject_bearing_put_lanes()
    assert subject_bearing, "derived no subject-bearing lanes — the derivation broke"

    per_row = {
        route.path
        for route in daemons_router.router.routes
        if isinstance(route, APIRoute)
        and "PUT" in route.methods
        and "permitted_rows" in route.endpoint.__code__.co_names
    }
    missing = sorted(set(subject_bearing) - per_row)
    assert not missing, (
        "these lanes carry rows naming their own repo but gate on the publishing "
        f"token's repo instead — #714's exact defect: "
        f"{ {path: subject_bearing[path] for path in missing} }"
    )
    spurious = sorted(per_row - set(subject_bearing))
    assert not spurious, (
        "these lanes gate per row but their payload rows name no repo, so there "
        f"is nothing to key on: {spurious}"
    )


def _two_repos(client, *, victim_label: str, victim_layers: str, publisher_layers: str):
    """A repo that opted OUT and a publisher that opted IN, same account."""
    from brnrd.models import Repo

    client.post("/v1/repos/connect",
                json={"repo_full_name": victim_label, "publish_layers": victim_layers})
    client.post("/v1/repos/connect",
                json={"repo_full_name": "Gurio/publisher", "publish_layers": publisher_layers})
    with client.app.state.SessionLocal() as db:
        victim = db.query(Repo).filter(Repo.repo_full_name == victim_label).one()
        publisher = db.query(Repo).filter(Repo.repo_full_name == "Gurio/publisher").one()
        return victim.account_id, victim.id, publisher.id


def test_truncating_repo_label_would_publish_an_opted_out_repo_under_the_publishers_consent():
    """The escalation `repo_label`'s classification used to open, pinned (#685).

    The first cut of #685 classified `repo_label` as display and truncated it,
    on the argument that a shortened label "fails closed" because the truncation
    mark cannot appear in a forge repo name, so it matches nothing.

    **Matching nothing is not failing closed.** `_subject_permits` resolves the
    no-match case to the *publisher's* consent — the #714 fallback, which is
    right for a spoofed label and catastrophic for one the server truncated
    itself. Truncation is what flips the outcome, and this test drives both
    sides of that flip against `permitted_rows` rather than asserting a string
    property. The string-property version this replaces passed for the entire
    life of the escalation, because it never called `permitted_rows`.
    """
    from brnrd import schemas

    client = _client()
    _login(client)
    cap = schemas.LiveRunIn.string_bounds()["repo_label"]
    victim_label = "Gurio/" + "r" * (cap + 50)
    account_id, _victim_id, publisher_id = _two_repos(
        client, victim_label=victim_label, victim_layers="none", publisher_layers="live_runs",
    )

    class _Row:
        def __init__(self, label):
            self.repo_label = label

    with client.app.state.SessionLocal() as db:
        def kept_for(label):
            return publish_scope.permitted_rows(
                db, [_Row(label)], account_id=account_id,
                publisher_repo_id=publisher_id, lane="live_runs",
            )

        # As the daemon sends it: resolves to the victim, whose `none` drops it.
        assert kept_for(victim_label) == []

        # The value truncation *would* have produced. It resolves to no repo, so
        # `_subject_permits` falls back to the publisher's `live_runs` and the
        # opted-out repo's row publishes under someone else's consent.
        mark = schemas.LIVE_RUN_TRUNCATION_MARK
        as_truncated = victim_label[: cap - len(mark)] + mark
        assert len(kept_for(as_truncated)) == 1, (
            "expected the truncated label to escalate — if this no longer holds, "
            "the fallback in _subject_permits changed and this test's premise is stale"
        )

    # Which is why the server must never produce that value: `repo_label` is in
    # the matched set, so an over-long one costs its row at validation instead.
    with pytest.raises(Exception):
        schemas.LiveRunIn.model_validate({"id": "run-v", "repo_label": victim_label})


def test_an_over_long_repo_label_costs_its_row_instead_of_changing_whose_consent_applies():
    """The shipped behaviour: reject, report, and leave the neighbours alone."""
    from brnrd import schemas

    cap = schemas.LiveRunIn.string_bounds()["repo_label"]
    over = "Gurio/" + "r" * (cap + 50)

    intake = schemas.isolate_live_runs([
        {"id": "keep-1", "repo_label": "Gurio/publisher"},
        {"id": "drop-me", "repo_label": over},
        {"id": "keep-2", "repo_label": "Gurio/publisher"},
    ])
    assert [row.id for row in intake.runs] == ["keep-1", "keep-2"]
    assert [(r.id, r.fields) for r in intake.rejected] == [("drop-me", ["repo_label"])]
    # It is *not* silently shortened into an unresolvable label.
    assert schemas.LIVE_RUN_TRUNCATION_MARK not in "".join(
        row.repo_label for row in intake.runs
    )


def test_a_repo_label_at_the_cap_still_resolves_to_its_own_repos_consent():
    """The other direction, and the one that proves the fix did not simply break
    the lane: a label at exactly the bound is unchanged and still resolves to
    the repo it names — whose recorded `none` is honoured over the publisher's
    `live_runs`."""
    from brnrd import schemas

    client = _client()
    _login(client)
    cap = schemas.LiveRunIn.string_bounds()["repo_label"]
    at_cap_label = "Gurio/" + "r" * (cap - len("Gurio/"))
    assert len(at_cap_label) == cap
    account_id, _victim_id, publisher_id = _two_repos(
        client, victim_label=at_cap_label, victim_layers="none", publisher_layers="live_runs",
    )

    row = schemas.LiveRunIn.model_validate({"id": "run-v", "repo_label": at_cap_label})
    assert row.repo_label == at_cap_label  # byte-identical, unmarked

    with client.app.state.SessionLocal() as db:
        kept = publish_scope.permitted_rows(
            db, [row], account_id=account_id,
            publisher_repo_id=publisher_id, lane="live_runs",
        )
    assert kept == [], "the victim's recorded `none` must still win over the publisher's consent"


def test_every_matched_field_rejects_rather_than_truncates():
    """The rule, stated as the predicate that actually decides: **is this value
    ever matched, or only shown?**

    "Display vs identity" is how this set was first drawn, and it is why
    `repo_label` — which reads like display and is matched — was truncated into
    a consent escalation. The polarity chosen in #685 makes truncation the
    default so a new *shown* field is safe automatically; the cost is that a new
    *matched* field defaults to the wrong side. This is the test that charges
    that cost: any bounded `LiveRunIn` field a consent or join lookup reads must
    be in `LIVE_RUN_IDENTITY_FIELDS`.
    """
    from brnrd import schemas

    # Fields `src/brnrd` resolves a decision through, not merely renders.
    # `repo_label`: publish_scope._subject_repos (consent). id/run_id/
    # parent_run_id: row and parent/child joins. `stream` is deliberately absent
    # — it looks like a key and nothing in src/brnrd matches on it.
    matched = {"id", "run_id", "parent_run_id", "repo_label"}
    assert matched <= schemas.LIVE_RUN_IDENTITY_FIELDS, (
        "a value something is matched against is truncating: "
        f"{sorted(matched - schemas.LIVE_RUN_IDENTITY_FIELDS)}"
    )
    bounds = schemas.LiveRunIn.string_bounds()
    for field in sorted(schemas.LIVE_RUN_IDENTITY_FIELDS):
        with pytest.raises(Exception):
            schemas.LiveRunIn.model_validate({"id": "run-a", field: "y" * (bounds[field] + 1)})

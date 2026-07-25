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


# ── account-wide corpus: a legacy sibling narrows nothing, dissolves nothing ──


def _account_id(client: TestClient) -> str:
    with client.app.state.SessionLocal() as db:
        return db.query(Repo).first().account_id


def _mint_legacy_repo(client: TestClient, session_token: str, full_name: str) -> str:
    """A repo with `publish_layers IS NULL` — the account-API-key surface this
    change deliberately leaves un-migrated, i.e. a repo connected before the
    consent step shipped."""
    repo_id = client.post(
        "/v1/accounts/repos",
        json={"repo_full_name": full_name},
        headers={"Authorization": f"Bearer {session_token}"},
    ).json()["repo_id"]
    with client.app.state.SessionLocal() as db:
        assert db.get(Repo, repo_id).publish_layers is None
    return repo_id


def test_a_legacy_sibling_does_not_dissolve_a_recorded_none():
    """#715 B2, driven at the seam that ships. An account with one repo that
    recorded an explicit `none` and one legacy repo that recorded nothing must
    still ship no corpus: a repo with no recorded value neither consents nor
    vetoes, so it cannot dissolve the sibling's opt-out. Before the fix the
    first `NULL` row returned `None` (unenforced) and the whole surface
    shipped."""
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


def test_legacy_only_account_is_still_entirely_unenforced():
    """The `SECURITY.md` carve-out, pinned: an account where *no* repo recorded
    a consent keeps today's exact behaviour — `None`, unenforced, every slice
    ships. Nothing that publishes today goes dark for the fix above."""
    client = _client()
    token = _login(client)
    _mint_legacy_repo(client, token, "Gurio/legacy-a")
    repo_b = _mint_legacy_repo(client, token, "Gurio/legacy-b")

    with client.app.state.SessionLocal() as db:
        assert publish_scope.corpus_slices_permitted(db, _account_id(client)) is None

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
    assert [f["path"] for f in r.json()["files"]] == ["surface/plan.md", "knowledge/index.md"]


def test_recorded_consents_still_intersect_and_a_legacy_sibling_does_not_widen():
    """The all-recorded path is unchanged (plain intersection), and adding a
    legacy repo to that account neither widens nor dissolves the result."""
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
        assert publish_scope.corpus_slices_permitted(db, account_id) == frozenset({"knowledge"})


def test_account_with_no_repos_at_all_is_unenforced():
    """Unchanged: nothing connected means nothing to enforce against."""
    client = _client()
    _login(client)
    with client.app.state.SessionLocal() as db:
        assert publish_scope.corpus_slices_permitted(db, "acc-nonexistent") is None


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
def test_a_row_naming_a_repo_with_no_recorded_consent_still_publishes(
    lane, put_path, payload_of, stored_key, dash_path, dash_key
):
    """Carve-out pin, not a fix-shaped assertion.

    `lane_permitted` fails **open** for a repo that never recorded a consent —
    a repo connected before this gate shipped. That is the disclosed behaviour
    in `SECURITY.md` and was re-litigated in #715 (`64925c11`); narrowing it
    here would be a regression, not a bonus. Delete the #714 fix entirely and
    this must stay green — that is what makes it a pin on the carve-out rather
    than a restatement of the change.
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
        "Gurio/legacy",
        "Gurio/public",
    ], f"{lane}: a legacy repo with no recorded consent went dark — carve-out broken"

    served = [row.get("repo_label") for row in client.get(dash_path).json()[dash_key]]
    assert "Gurio/legacy" in served, f"{lane}: legacy row missing from the dashboard — {served}"


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
        if carriers:
            found[route.path] = carriers
    return found


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

"""Tests for the discovered work-surface mirror and dashboard view."""

from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event  # noqa: E402

from brnrd import create_app  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.models import Event  # noqa: E402
from brnrd.oauth import GitHubIdentity  # noqa: E402
from brnrd.routers.accounts import account_for_github_identity, issue_session_token  # noqa: E402
from _helpers import PUBLISH_EVERYTHING, brnrd_account_headers  # noqa: E402


def _client() -> TestClient:
    app = create_app(Settings(database_url="sqlite:///:memory:", public_base_url="https://brnrd.example", github_oauth_client_id="gh-client", github_oauth_client_secret="gh-secret"))
    return TestClient(app, base_url="https://testserver")


def _repo_and_daemon(client: TestClient) -> tuple[dict[str, str], dict[str, str]]:
    account_headers = brnrd_account_headers(client.app, github_id="123", login="octocat", email="a@b.com")
    repo = client.post("/v1/accounts/repos", json={"repo_full_name": "Gurio/brr", "default_branch": "main", "publish_layers": PUBLISH_EVERYTHING}, headers=account_headers).json()
    pair = client.post("/v1/accounts/pair").json()
    client.post(f"/v1/accounts/pair/{pair['pair_code']}/approve", json={"repo_id": repo["repo_id"], "approve_secret": pair["approve_secret"]}, headers=account_headers)
    paired = client.get(f"/v1/accounts/pair/{pair['pair_code']}", params={"poll_secret": pair["poll_secret"]}).json()
    return account_headers, {"Authorization": f"Bearer {paired['daemon_token']}"}


def _login_cookie(client: TestClient) -> None:
    with client.app.state.SessionLocal() as db:
        account = account_for_github_identity(db, GitHubIdentity(github_id="123", login="octocat", email="a@b.com"))
        token = issue_session_token(db, account)
    client.cookies.set("brnrd_session", token)


def test_daemon_surface_snapshot_replaces_the_discovered_set():
    client = _client()
    _, daemon_headers = _repo_and_daemon(client)
    posted = client.put("/v1/daemons/surface", json={"files": [
        {"path": "index.md", "markdown": "# Work surface"},
        {"path": "plans/Gurio__brr/active.md", "markdown": "# Ranked moves"},
    ]}, headers=daemon_headers)
    assert posted.status_code == 200, posted.text
    assert [item["path"] for item in posted.json()["files"]] == ["index.md", "plans/Gurio__brr/active.md"]
    assert posted.json()["surface_updated_at"] is not None

    replaced = client.put("/v1/daemons/surface", json={"files": [{"path": "surface/index.md", "markdown": "revised"}]}, headers=daemon_headers)
    assert replaced.status_code == 200
    assert [item["path"] for item in replaced.json()["files"]] == ["surface/index.md"]


def test_daemon_surface_carries_corpus_layer_and_truncation():
    """The layered corpus: each file keeps its layer and truncation marker."""
    client = _client()
    _, daemon_headers = _repo_and_daemon(client)
    posted = client.put("/v1/daemons/surface", json={"files": [
        {"path": "surface/index.md", "markdown": "# Work surface", "layer": "authored"},
        {"path": "knowledge/repos/Gurio__brr/log.md", "markdown": "capped", "layer": "knowledge", "truncated": True},
        # `runs`, not the invented `replies`: the corpus has exactly three
        # layers (`brr.account.CORPUS_LAYERS`), and the daemon's surface
        # publisher cannot emit a fourth. A `replies` layer only round-tripped
        # here because the consent gate was unenforced for this fixture's repo
        # — no consent string could ever have permitted it, since it is not in
        # the slice vocabulary. With the gate closed the fiction is visible, so
        # the fixture now names a layer production actually produces, and the
        # case covers all three real layers instead of two plus a ghost.
        {"path": "runs/Gurio__brr/run-x.md", "markdown": "reply", "layer": "runs"},
    ]}, headers=daemon_headers)
    assert posted.status_code == 200, posted.text
    _login_cookie(client)
    files = client.get("/v1/dashboard/surface").json()["files"]
    by_path = {item["path"]: item for item in files}
    assert by_path["knowledge/repos/Gurio__brr/log.md"]["layer"] == "knowledge"
    assert by_path["knowledge/repos/Gurio__brr/log.md"]["truncated"] is True
    assert by_path["runs/Gurio__brr/run-x.md"]["layer"] == "runs"
    assert by_path["surface/index.md"]["truncated"] is False


def test_daemon_surface_bases_round_trip():
    """design-the-bases-the-dashboard-needs.md: the per-repo forge + kb
    bases ride the same publish call as `files`, keyed by repo label on
    the way back out."""
    client = _client()
    _, daemon_headers = _repo_and_daemon(client)
    posted = client.put(
        "/v1/daemons/surface",
        json={
            "files": [],
            "bases": {
                "Gurio/brr": {"forge": "https://github.com/Gurio/brr", "forge_kind": "github", "kb": "https://github.com/Gurio/brr-kb/blob/main/knowledge/"},
            },
        },
        headers=daemon_headers,
    )
    assert posted.status_code == 200, posted.text
    assert posted.json()["bases"] == {
        "Gurio/brr": {"forge": "https://github.com/Gurio/brr", "forge_kind": "github", "kb": "https://github.com/Gurio/brr-kb/blob/main/knowledge/"},
    }

    from brnrd.models import Account

    with client.app.state.SessionLocal() as db:
        account = db.query(Account).one()
        stored = json.loads(account.bases_json)
    assert stored == {
        "Gurio/brr": {"forge": "https://github.com/Gurio/brr", "forge_kind": "github", "kb": "https://github.com/Gurio/brr-kb/blob/main/knowledge/"},
    }


def test_daemon_surface_bases_gated_per_repo_not_by_the_publishing_tokens_repo():
    """A base names its own repo (#714) — an opted-out sibling's base must
    not ship just because the publishing token belongs to a consenting one."""
    from brnrd.models import Repo

    client = _client()
    account_headers, daemon_headers = _repo_and_daemon(client)  # Gurio/brr, everything
    client.post(
        "/v1/accounts/repos",
        json={"repo_full_name": "Gurio/silent", "publish_layers": "none"},
        headers=account_headers,
    )

    posted = client.put(
        "/v1/daemons/surface",
        json={
            "files": [],
            "bases": {
                "Gurio/brr": {"forge": "https://github.com/Gurio/brr", "forge_kind": "github"},
                "Gurio/silent": {"forge": "https://github.com/Gurio/silent", "forge_kind": "github"},
            },
        },
        headers=daemon_headers,
    )

    assert posted.status_code == 200, posted.text
    assert list(posted.json()["bases"]) == ["Gurio/brr"]
    from brnrd.models import Account

    with client.app.state.SessionLocal() as db:
        account = db.query(Account).one()
        assert list(json.loads(account.bases_json).keys()) == ["Gurio/brr"]


@pytest.mark.parametrize("path", ["../secret.md", "/absolute.md", ".hidden.md"])
def test_daemon_surface_refuses_paths_outside_the_declared_root(path: str):
    client = _client()
    _, daemon_headers = _repo_and_daemon(client)
    response = client.put("/v1/daemons/surface", json={"files": [{"path": path, "markdown": "no"}]}, headers=daemon_headers)
    assert response.status_code == 422


def test_dashboard_surface_returns_the_same_generic_file_set():
    client = _client()
    _, daemon_headers = _repo_and_daemon(client)
    files = [
        {"path": "index.md", "markdown": "[Plan](plans/Gurio__brr/active.md)"},
        {"path": "workflow.md", "markdown": "## Gating\nvisibility over approval"},
    ]
    client.put("/v1/daemons/surface", json={"files": files}, headers=daemon_headers)
    _login_cookie(client)

    response = client.get("/v1/dashboard/surface")

    assert response.status_code == 200
    got = [{"path": item["path"], "markdown": item["markdown"]} for item in response.json()["files"]]
    assert got == files
    assert response.json()["reported_at"] is not None


def test_dashboard_surface_requires_session():
    assert _client().get("/v1/dashboard/surface").status_code == 401


def test_dashboard_surface_first_load_carries_an_etag():
    """#946: the validator rides the first response so a caller has something
    to echo back on the next reload."""
    client = _client()
    _, daemon_headers = _repo_and_daemon(client)
    client.put("/v1/daemons/surface", json={"files": [{"path": "index.md", "markdown": "# Work"}]}, headers=daemon_headers)
    _login_cookie(client)

    response = client.get("/v1/dashboard/surface")

    assert response.status_code == 200
    assert response.headers.get("etag")
    assert response.json()["files"]


def test_dashboard_surface_conditional_reload_gets_an_empty_304():
    """The repeat-load case #946 targets: same corpus, no daemon publish in
    between — the second reload must not re-ship the 2,841-page body."""
    client = _client()
    _, daemon_headers = _repo_and_daemon(client)
    client.put("/v1/daemons/surface", json={"files": [{"path": "index.md", "markdown": "# Work"}]}, headers=daemon_headers)
    _login_cookie(client)
    first = client.get("/v1/dashboard/surface")
    etag = first.headers["etag"]

    second = client.get("/v1/dashboard/surface", headers={"If-None-Match": etag})

    assert second.status_code == 304
    assert second.content == b""
    assert second.headers.get("etag") == etag


def test_dashboard_surface_stale_conditional_gets_200_after_republish():
    """A conditional built against a stale ETag must not be honored once the
    daemon republishes the corpus — the validator has to move with the data."""
    client = _client()
    _, daemon_headers = _repo_and_daemon(client)
    client.put("/v1/daemons/surface", json={"files": [{"path": "index.md", "markdown": "# Work"}]}, headers=daemon_headers)
    _login_cookie(client)
    first = client.get("/v1/dashboard/surface")
    stale_etag = first.headers["etag"]

    client.put("/v1/daemons/surface", json={"files": [{"path": "index.md", "markdown": "# Work, revised"}]}, headers=daemon_headers)
    third = client.get("/v1/dashboard/surface", headers={"If-None-Match": stale_etag})

    assert third.status_code == 200
    assert third.headers["etag"] != stale_etag
    assert third.json()["files"][0]["markdown"] == "# Work, revised"


@contextmanager
def _captured_sql(client: TestClient):
    """Every statement the app's engine actually emits, in order.

    Asserting on the SQL is the only honest way to pin #956: the bug is a
    column being *read*, and a timing assertion would measure the test
    machine rather than the query. ``before_cursor_execute`` sees the final
    text handed to the DBAPI, after the ORM has chosen its column list.
    """
    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = client.app.state.engine
    event.listen(engine, "before_cursor_execute", _record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", _record)


_NON_SURFACE_DASHBOARD_ENDPOINTS = (
    "/v1/dashboard/config-requests",
    "/v1/dashboard/quota",
    "/v1/dashboard/live-runs",
    "/v1/dashboard/runners",
    "/v1/dashboard/activity",
)


@pytest.mark.parametrize("path", _NON_SURFACE_DASHBOARD_ENDPOINTS)
def test_dashboard_handlers_that_never_render_the_corpus_do_not_read_it(path: str):
    """#956: ``surface_json`` is deferred, so ``db.get(Account, id)`` stops
    dragging the corpus out of the database for handlers that never look at it.

    In production that column held 10 MB, and every authenticated dashboard
    handler opens with the same ``db.get`` — which is why a 65-byte
    ``/config-requests`` response cost 3.0 s against a 73 ms ``/healthz``
    floor. The account row must still be loaded (these handlers need it); it
    is the corpus column that must be absent from the SELECT.
    """
    client = _client()
    _, daemon_headers = _repo_and_daemon(client)
    client.put(
        "/v1/daemons/surface",
        json={"files": [{"path": "index.md", "markdown": "# Work"}]},
        headers=daemon_headers,
    )
    _login_cookie(client)

    with _captured_sql(client) as statements:
        response = client.get(path)

    assert response.status_code == 200, response.text
    # Without this the assertion below would pass vacuously if the handler
    # stopped loading the account at all.
    assert [s for s in statements if "FROM accounts" in s], f"{path} loaded no account row"
    leaked = [s for s in statements if "surface_json" in s]
    assert leaked == [], f"{path} read the corpus column:\n" + "\n".join(leaked)


def test_dashboard_surface_still_reads_the_whole_corpus():
    """The one reader keeps working: deferring is lazy, not lost."""
    client = _client()
    _, daemon_headers = _repo_and_daemon(client)
    files = [
        {"path": "index.md", "markdown": "# Work surface"},
        {"path": "knowledge/repos/Gurio__brr/log.md", "markdown": "x" * 4096, "layer": "knowledge"},
    ]
    client.put("/v1/daemons/surface", json={"files": files}, headers=daemon_headers)
    _login_cookie(client)

    with _captured_sql(client) as statements:
        response = client.get("/v1/dashboard/surface")

    assert response.status_code == 200, response.text
    got = [{"path": item["path"], "markdown": item["markdown"]} for item in response.json()["files"]]
    assert got == [{"path": item["path"], "markdown": item["markdown"]} for item in files]
    # The deferred load is emitted on access, as its own statement.
    assert [s for s in statements if "surface_json" in s], "the corpus was never loaded"


def test_dashboard_surface_304_does_not_read_the_corpus():
    """#951 stopped re-*shipping* the corpus; #956 stops re-*reading* it.

    The zero-byte 304 measured at 3.36 s in production is the proof the cost
    was never the payload — the validator is ``surface_updated_at``, and the
    conditional path must not touch the 10 MB column at all.
    """
    client = _client()
    _, daemon_headers = _repo_and_daemon(client)
    client.put(
        "/v1/daemons/surface",
        json={"files": [{"path": "index.md", "markdown": "# Work"}]},
        headers=daemon_headers,
    )
    _login_cookie(client)
    etag = client.get("/v1/dashboard/surface").headers["etag"]

    with _captured_sql(client) as statements:
        second = client.get("/v1/dashboard/surface", headers={"If-None-Match": etag})

    assert second.status_code == 304
    assert second.content == b""
    assert second.headers.get("etag") == etag
    assert [s for s in statements if "FROM accounts" in s], "the 304 loaded no account row"
    leaked = [s for s in statements if "surface_json" in s]
    assert leaked == [], "the 304 path read the corpus:\n" + "\n".join(leaked)


def test_deferring_the_corpus_keeps_it_in_the_publish_purge_inventory():
    """Privacy guard for the fix itself (#956).

    ``publish_scope`` discovers withdrawal targets by reflecting over mapper
    columns and reading their ``info`` markers. Deferring changes the loading
    strategy only — but a column that quietly stopped being enumerated by the
    erasure machinery would be a far worse bug than the latency it fixes, so
    the enumeration is asserted directly rather than assumed.
    """
    from brnrd import publish_scope
    from brnrd.models import Account

    targets = publish_scope._purge_targets()
    assert (
        "corpus",
        "slice",
        Account,
        "surface_json",
    ) in targets
    assert "corpus" in publish_scope.purge_storage_lanes()
    assert "surface_json" in Account.__table__.columns


def test_surface_rejects_a_traversal_path_even_in_an_unconsented_layer():
    """Shape is validated before consent is consulted.

    The filter used to run first, so a file whose layer the account had not
    consented to was `continue`d past the traversal guard and the request
    returned 200. A malformed payload must be refused on its own terms
    regardless of whether any of it would have shipped — otherwise closing the
    consent gate silently *widens* what a caller can send unchecked.
    """
    from brnrd.models import Repo

    client = _client()
    _account_headers, daemon_headers = _repo_and_daemon(client)
    # Narrow the consent so `knowledge` is definitely not permitted — asserted,
    # not hoped for, since a consent that failed to narrow would let the guard
    # fire for the ordinary reason and the test would prove nothing.
    with client.app.state.SessionLocal() as db:
        repo = db.query(Repo).filter(Repo.repo_full_name == "Gurio/brr").one()
        repo.publish_layers = "authored"
        db.commit()
    from brnrd import publish_scope

    with client.app.state.SessionLocal() as db:
        repo = db.query(Repo).filter(Repo.repo_full_name == "Gurio/brr").one()
        assert "knowledge" not in publish_scope.corpus_slices_permitted(db, repo.account_id)

    posted = client.put("/v1/daemons/surface", json={"files": [
        {"path": "../secret.md", "markdown": "x", "layer": "knowledge"},
    ]}, headers=daemon_headers)

    assert posted.status_code == 422, posted.text
    assert "invalid surface path" in posted.json()["detail"]


def test_dashboard_warp_asks_serves_lru_rows_stale_and_done_apart():
    client = _client()
    _, daemon_headers = _repo_and_daemon(client)
    files = [
        {"path": "surface/warp/w-1.md", "markdown": "# Older\n\ntype: action\nreturn: in git\ntouched: 2026-09-01T00:00:00Z\nsays: evt-a evt-b\n"},
        {"path": "surface/warp/w-2.md", "markdown": "# Newer\n\ntype: decision\ntouched: 2026-09-20T00:00:00Z\nattempts: run-260921-2330-3low\n"},
        {"path": "surface/warp/w-3.md", "markdown": "# Ancient\n\ntype: action\ntouched: 2025-01-01T00:00:00Z\n"},
        {"path": "surface/warp/w-4.md", "markdown": "# Shipped\n\ntype: action\ndone: 2026-09-10\n"},
        {"path": "surface/warp/g-1.md", "markdown": "# A goal\n\ntype: goal\n"},
    ]
    client.put("/v1/daemons/surface", json={"files": files}, headers=daemon_headers)
    _login_cookie(client)

    response = client.get("/v1/dashboard/warp/asks.json")

    assert response.status_code == 200
    body = response.json()
    assert [row["id"] for row in body["asks"]] == ["w-2", "w-1", "w-3"]  # LRU, stale last
    assert [row["stale"] for row in body["asks"]] == [False, False, True]
    assert body["asks"][1]["return"] == "in git" and len(body["asks"][1]["says"]) == 2
    assert body["asks"][0]["touched_at"].startswith("2026-09-21T23:30")  # newest evidence: the run id
    assert [row["id"] for row in body["done"]] == ["w-4"]
    assert [row["id"] for row in body["goals"]] == ["g-1"]
    assert body["stale_after_days"] == 60
    assert response.headers["cache-control"] == "private, max-age=30"


def test_dashboard_warp_asks_requires_session():
    assert _client().get("/v1/dashboard/warp/asks.json").status_code == 401


def test_dashboard_warp_asks_resolves_say_excerpts_from_the_message_store():
    """The 17:51Z steer on the-panel-third-pass: a say line with no
    excerpt of its own resolves one server-side, joined on the event id
    against the cloud's own message store (``Event.body``) — scoped to
    this account's own repos, and never fabricating a placeholder for an
    id that matches nothing."""
    client = _client()
    account_headers, daemon_headers = _repo_and_daemon(client)
    repo_id = client.post(
        "/v1/accounts/repos",
        json={"repo_full_name": "Gurio/brr", "default_branch": "main", "publish_layers": PUBLISH_EVERYTHING},
        headers=account_headers,
    ).json()["repo_id"]
    with client.app.state.SessionLocal() as db:
        db.add(Event(event_id="evt-with-body", repo_id=repo_id, source="dev", body="  slick ui   to inspect the done things  ", reply_to="{}"))
        db.add(Event(event_id="evt-long-body", repo_id=repo_id, source="dev", body="x" * 200, reply_to="{}"))
        db.commit()
    files = [
        {
            "path": "surface/warp/w-1.md",
            "markdown": (
                "# Has a say\n\n"
                "type: action\n"
                "touched: 2026-09-22T00:00:00Z\n"
                "says: evt-with-body evt-long-body evt-unknown\n"
            ),
        },
    ]
    client.put("/v1/daemons/surface", json={"files": files}, headers=daemon_headers)
    _login_cookie(client)

    body = client.get("/v1/dashboard/warp/asks.json").json()

    says = {say["event"]: say for say in body["asks"][0]["says"]}
    assert says["evt-with-body"]["excerpt"] == "slick ui to inspect the done things"
    assert says["evt-long-body"]["excerpt"] == "x" * 120 + "…"
    assert says["evt-unknown"]["excerpt"] is None  # matches nothing: stays blank, not a lie
    assert all(say["url"] is None for say in says.values())  # no message route exists yet


def test_dashboard_warp_asks_excerpt_scoped_to_the_account_own_repos():
    """A same-named event id on a repo the account does not own must not
    leak that other account's message body into this account's list."""
    client = _client()
    account_headers, daemon_headers = _repo_and_daemon(client)
    other_headers = brnrd_account_headers(client.app, github_id="999", login="other", email="c@d.com")
    other_repo_id = client.post(
        "/v1/accounts/repos",
        json={"repo_full_name": "Other/repo", "default_branch": "main", "publish_layers": PUBLISH_EVERYTHING},
        headers=other_headers,
    ).json()["repo_id"]
    with client.app.state.SessionLocal() as db:
        db.add(Event(event_id="evt-shared-id", repo_id=other_repo_id, source="dev", body="the other account's own words", reply_to="{}"))
        db.commit()
    files = [
        {
            "path": "surface/warp/w-1.md",
            "markdown": "# Has a say\n\ntype: action\ntouched: 2026-09-22T00:00:00Z\nsays: evt-shared-id\n",
        },
    ]
    client.put("/v1/daemons/surface", json={"files": files}, headers=daemon_headers)
    _login_cookie(client)

    body = client.get("/v1/dashboard/warp/asks.json").json()

    assert body["asks"][0]["says"][0]["excerpt"] is None


def test_dashboard_warp_asks_resolves_receipts_from_the_published_bases():
    """design-the-bases-the-dashboard-needs.md: `bases` rides the same
    payload, at the top level, and every row's `receipt:` tokens resolve
    against it (the-panel-third-pass.md's "not built" doubt, closed)."""
    client = _client()
    _, daemon_headers = _repo_and_daemon(client)
    files = [
        {
            "path": "surface/warp/w-1.md",
            "markdown": "# Shipped\n\ntype: action\ntouched: 2026-09-22T00:00:00Z\nreceipt: #42 design-the-ask.md w-9\n",
        },
    ]
    client.put(
        "/v1/daemons/surface",
        json={
            "files": files,
            "bases": {
                "Gurio/brr": {
                    "forge": "https://github.com/Gurio/brr",
                    "forge_kind": "github",
                    "kb": "https://github.com/Gurio/brr-kb/blob/main/knowledge/",
                },
            },
        },
        headers=daemon_headers,
    )
    _login_cookie(client)

    body = client.get("/v1/dashboard/warp/asks.json").json()

    assert body["bases"] == {
        "Gurio/brr": {"forge": "https://github.com/Gurio/brr", "forge_kind": "github", "kb": "https://github.com/Gurio/brr-kb/blob/main/knowledge/"},
    }
    receipts = {r["ref"]: r["url"] for r in body["asks"][0]["receipts"]}
    assert receipts == {
        "#42": "https://github.com/Gurio/brr/pull/42",
        "design-the-ask.md": "https://github.com/Gurio/brr-kb/blob/main/knowledge/design-the-ask.md",
        "w-9": None,  # no dashboard item route exists yet
    }


def test_dashboard_warp_asks_receipts_empty_with_no_published_bases():
    """No bases published yet ⇒ every receipt token still lists, url `None`
    throughout — never a guess, never a missing key."""
    client = _client()
    _, daemon_headers = _repo_and_daemon(client)
    files = [
        {
            "path": "surface/warp/w-1.md",
            "markdown": "# Shipped\n\ntype: action\ntouched: 2026-09-22T00:00:00Z\nreceipt: #42\n",
        },
    ]
    client.put("/v1/daemons/surface", json={"files": files}, headers=daemon_headers)
    _login_cookie(client)

    body = client.get("/v1/dashboard/warp/asks.json").json()

    assert body["bases"] == {}
    assert body["asks"][0]["receipts"] == [{"ref": "#42", "url": None}]

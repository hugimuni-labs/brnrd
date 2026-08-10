"""Tests for the discovered work-surface mirror and dashboard view."""

from __future__ import annotations

from contextlib import contextmanager

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event  # noqa: E402

from brnrd import create_app  # noqa: E402
from brnrd.config import Settings  # noqa: E402
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

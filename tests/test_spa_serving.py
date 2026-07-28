"""The app serves the SPA, and the backend/SPA boundary is derived, not listed.

#847: before this, the SvelteKit build was served entirely by Upsun's
``web.locations`` router, and the boundary between "backend route" and "SPA
deep link" lived in a hand-maintained regex in ``.upsun/config.yaml`` whose own
comment asked the next author to keep it in sync with ``app.py``. CI could
never catch a drift, because CI never ran the router.

These tests are the replacement for that regex. The first two are the ones
that make the duplicate impossible to reintroduce: they check the *property*
(backend namespaces and SPA routes are disjoint; the dev proxy covers every
backend namespace) rather than any copy of the list.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from brnrd.app import create_app
from brnrd.config import Settings
from brnrd.spa import backend_namespaces, resolve_frontend_dir

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROUTES = REPO_ROOT / "src" / "frontend" / "src" / "routes"
VITE_CONFIG = REPO_ROOT / "src" / "frontend" / "vite.config.ts"


def _spa_top_level_routes() -> set[str]:
    """Top-level SvelteKit route names, read off the route directory.

    ``src/routes/repos/+page.svelte`` → ``repos``. Parameterised segments
    (``[code]``) never appear at the top level in this app; if one ever does,
    it is excluded here because a dynamic root would claim everything and the
    disjointness check below would stop meaning anything.
    """
    names: set[str] = set()
    for child in FRONTEND_ROUTES.iterdir():
        if not child.is_dir() or child.name.startswith((".", "[")):
            continue
        names.add(child.name)
    return names


def _vite_proxy_pattern() -> str:
    """The single backend-proxy key from the dev server's vite config."""
    text = VITE_CONFIG.read_text(encoding="utf-8")
    match = re.search(r"proxy:\s*\{\s*'([^']+)'", text)
    assert match, "vite.config.ts no longer declares a single quoted proxy key"
    return match.group(1)


def _upsun_locations() -> dict:
    yaml = pytest.importorskip("yaml")
    config = yaml.safe_load((REPO_ROOT / ".upsun" / "config.yaml").read_text(encoding="utf-8"))
    return config["applications"]["brnrd"]["web"]["locations"]


def _app_namespaces() -> set[str]:
    """Namespaces brnrd itself declares — FastAPI's own /docs etc. excluded.

    The exclusion is derived from a bare ``FastAPI()`` rather than named, so a
    framework upgrade that adds a built-in route does not fail this suite.
    """
    app = create_app(Settings(database_url="sqlite://", telegram_auto_webhook=False))
    builtin = backend_namespaces(r.path for r in FastAPI().routes)
    return set(app.state.backend_namespaces) - set(builtin)


def test_backend_namespaces_are_disjoint_from_spa_routes():
    """A backend route inside an SPA namespace silently breaks that deep link.

    This is the check the passthru regex was standing in for. It fires on both
    directions of the mistake: a new router mounted at an SPA-owned prefix, and
    a new SvelteKit page added under a prefix the API already owns.
    """
    collisions = _app_namespaces() & _spa_top_level_routes()
    assert not collisions, (
        f"these paths are claimed by both the API and the SPA: {sorted(collisions)}. "
        "The backend wins (it is registered first), so the SPA page becomes "
        "unreachable. Move the backend route under /v1 or rename the page."
    )


def test_dev_proxy_reaches_every_backend_namespace():
    """`npm run dev` serves the SPA itself, so it must proxy the whole API.

    A namespace missing here is a route that works in production and 404s in
    development against the SvelteKit dev server — the drift that was already
    live before #847 (the vite list had neither `logout` nor `terms/accept`).
    """
    pattern = re.compile(_vite_proxy_pattern())
    missing = sorted(ns for ns in _app_namespaces() if not pattern.match(f"/{ns}/"))
    assert not missing, f"vite.config.ts proxy does not reach: {missing}"


def test_dev_proxy_does_not_capture_spa_routes():
    """The mirror failure: a proxy rule broad enough to steal the SPA's pages."""
    pattern = re.compile(_vite_proxy_pattern())
    stolen = sorted(name for name in _spa_top_level_routes() if pattern.match(f"/{name}/"))
    assert not stolen, f"vite.config.ts proxy swallows SPA routes: {stolen}"


@pytest.fixture
def spa_client(tmp_path):
    """An app serving a stand-in build tree.

    Built against a synthetic directory on purpose: the backend CI job never
    runs ``npm run build``, so a test that needed the real ``src/frontend/build``
    would be a test that silently skips in the one place it matters.
    """
    build = tmp_path / "build"
    (build / "_app").mkdir(parents=True)
    (build / "index.html").write_text("<!doctype html><title>brnrd</title>", encoding="utf-8")
    (build / "_app" / "app.js").write_text("export const x = 1;\n", encoding="utf-8")
    (build / "robots.txt").write_text("User-agent: *\n", encoding="utf-8")
    settings = Settings(
        database_url="sqlite://",
        telegram_auto_webhook=False,
        frontend_dir=str(build),
    )
    with TestClient(create_app(settings)) as client:
        yield client


def test_deep_spa_route_serves_the_shell(spa_client):
    """`/repos` and `/connect/<code>` are client-side routes with no file."""
    for path in ("/", "/repos", "/connect/BR-123", "/config-approve/req-1", "/login", "/terms"):
        r = spa_client.get(path)
        assert r.status_code == 200, path
        assert "<title>brnrd</title>" in r.text, path


def test_static_assets_come_from_the_build_tree(spa_client):
    r = spa_client.get("/_app/app.js")
    assert r.status_code == 200
    assert "export const x" in r.text


def test_backend_routes_still_win(spa_client):
    """Registration order is the priority order — declared routes go first."""
    r = spa_client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["service"] == "brnrd"


def test_unknown_api_path_is_a_json_404_not_the_shell(spa_client):
    """The one thing the passthru regex bought, kept.

    Without the namespace rule a mistyped API path would answer 200 text/html,
    which reads to a client as "this endpoint exists and returned nonsense".
    """
    for path in ("/v1/nope", "/api/nope", "/v1/accounts/nope"):
        r = spa_client.get(path)
        assert r.status_code == 404, path
        assert r.headers["content-type"].startswith("application/json"), path


def test_non_read_methods_never_reach_the_shell(spa_client):
    """A POST to an undeclared path is a 404, not an HTML page."""
    r = spa_client.post("/repos", json={})
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")


def test_traversal_out_of_the_build_tree_is_refused(spa_client):
    r = spa_client.get("/../../pyproject.toml")
    assert r.status_code in (200, 404)
    assert "[project]" not in r.text


def test_missing_build_directory_is_not_an_error(tmp_path):
    """A backend-only deployment is legal; `/` 404s and the API still works."""
    settings = Settings(
        database_url="sqlite://",
        telegram_auto_webhook=False,
        frontend_dir=str(tmp_path / "nothing-here"),
    )
    assert resolve_frontend_dir(settings.frontend_dir) is None
    with TestClient(create_app(settings)) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/repos").status_code == 404


def test_upsun_config_no_longer_duplicates_the_route_list():
    """The regex this suite replaces must not come back.

    Named explicitly rather than left to review: the duplicate survived three
    refactors because every reader saw a config file, not a copy of app.py.
    """
    locations = _upsun_locations()
    # Parsed, not grepped: the first draft of this test string-matched
    # "rules:" and failed on the comment explaining the removal.
    for path, block in locations.items():
        assert "rules" not in block, (
            f"`.upsun/config.yaml` location {path!r} declares rules again — the "
            "backend/SPA boundary belongs in src/brnrd/spa.py, where a test "
            "can reach it."
        )


def test_upsun_passes_fileless_paths_to_an_app_that_can_answer_them():
    """The deploy config and the runtime env are one decision, in two files.

    `passthru: true` hands every path with no file behind it — i.e. every SPA
    deep link — to uvicorn. That is only correct while the app can find the
    build, and it *cannot* find it by default in production: the build hook
    `pip install`s brnrd into site-packages, so the package-relative guess in
    `resolve_frontend_dir` resolves next to the installed package and misses.
    `.environment` therefore has to export `BRNRD_FRONTEND_DIR`, and nothing
    but this test ties the two halves together. Caught in review, not by a
    failing test — which is why the test exists now.
    """
    root = _upsun_locations()["/"]
    if root.get("passthru") is not True:
        pytest.skip("the root location no longer passes fileless paths to the app")
    environment = (REPO_ROOT / ".environment").read_text(encoding="utf-8")
    assert "BRNRD_FRONTEND_DIR" in environment, (
        "`.upsun/config.yaml` sends SPA deep links to the app, but "
        "`.environment` does not tell the app where the build is — every deep "
        "link would 404 in production."
    )

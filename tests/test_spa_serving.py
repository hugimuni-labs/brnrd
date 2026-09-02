"""The app serves the SPA, and the backend/SPA boundary is derived, not listed.

#847: before this, the SvelteKit build was served entirely by the PaaS router
the backend then ran behind, and the boundary between "backend route" and "SPA
deep link" lived in a hand-maintained regex in that platform's config whose own
comment asked the next author to keep it in sync with ``app.py``. CI could
never catch a drift, because CI never ran the router. The config file itself
went away with the host on 2026-07-31; the duplication it caused is the reason
these tests are shaped the way they are.

These tests are the replacement for that regex. The first two are the ones
that make the duplicate impossible to reintroduce: they check the *property*
(backend namespaces and SPA routes are disjoint; the dev proxy covers every
backend namespace) rather than any copy of the list — a property that belongs
to the app, so it outlived the platform whose config prompted it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from brnrd.app import create_app
from brnrd.config import Settings
from brnrd.spa import _safe_file, backend_namespaces, resolve_frontend_dir

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


def test_the_image_tells_the_app_where_the_built_spa_is():
    """The deploy surface serves deep links from the app, so it must point at
    the build — two halves of one decision, in one file.

    Every path with no file behind it (i.e. every SPA deep link) is answered by
    the app itself now, which is only correct while the app can *find* the
    build. It cannot find it by default in production: the image `pip install`s
    brnrd into site-packages and deletes `/app/src`, so the package-relative
    guess in `resolve_frontend_dir` resolves next to the installed package and
    misses. `BRNRD_FRONTEND_DIR` therefore has to name the directory the image
    actually copies the SvelteKit build into, and nothing but this test ties
    the two lines together.

    This assertion outlived its first subject: it used to pair the PaaS route
    config's `passthru: true` against the repo-root `.environment` adapter that
    exported the variable. Both files went away on 2026-07-31; the coupling
    they encoded did not.
    """
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    declared = re.search(r"BRNRD_FRONTEND_DIR=(\S+)", dockerfile)
    assert declared, "the image no longer declares BRNRD_FRONTEND_DIR"
    copied = re.search(r"COPY --from=frontend-builder \S+ (\S+)", dockerfile)
    assert copied, "the image no longer copies a built SPA out of the frontend stage"
    assert declared.group(1) == copied.group(1), (
        f"the image serves SPA deep links from the app but points "
        f"BRNRD_FRONTEND_DIR at {declared.group(1)} while copying the build to "
        f"{copied.group(1)} — every deep link would 404 in production."
    )


def test_missing_app_asset_is_a_404_not_the_shell(spa_client):
    """A missing `/_app/...` file must 404 — the shell there is a lie.

    Every `_app` URL is minted by the SvelteKit build, never typed by a
    person and never a client route. Serving `index.html` for one that
    does not exist turned a stale or intercepted chunk fetch into
    ``200 text/html``, which the browser's ``import()`` reports as the
    opaque "Importing a module script failed" and SvelteKit renders as a
    500 — a soft 404 wearing a server error's face (measured live,
    2026-08-14). The 404 is also what SvelteKit's stale-deploy recovery
    (reload on failed navigation import) is designed to meet.
    """
    response = spa_client.get("/_app/immutable/nodes/9999.DeadBeef.js")
    assert response.status_code == 404
    assert "text/html" not in response.headers.get("content-type", "")
    # An `_app` file that *does* exist still serves normally.
    assert spa_client.get("/_app/app.js").status_code == 200


@pytest.fixture
def prerendered_client(tmp_path):
    """A build tree that also carries prerendered pages, the way
    ``adapter-static`` actually writes them.

    Its ``trailingSlash`` default emits ``<route>.html``, not
    ``<route>/index.html`` — so the file a request for ``/pricing`` needs is
    not named ``pricing``. A lookup that only tries the literal path finds
    nothing, falls through to the shell, and the prerender is 100% correct in
    the build output and 0% effective on the wire.

    The escape file outside ``build/`` is not decoration: it is what the
    ``.html``-suffix branch would reach for ``/.`` without its guard.
    """
    build = tmp_path / "build"
    (build / "_app").mkdir(parents=True)
    (build / "index.html").write_text(
        "<!doctype html><title>brnrd</title>", encoding="utf-8")
    (build / "pricing.html").write_text(
        "<!doctype html><title>pricing · brnrd</title>"
        '<meta property="og:title" content="pricing · brnrd">', encoding="utf-8")
    (build / "learn").mkdir()
    (build / "learn" / "agent-orchestration.html").write_text(
        "<!doctype html><title>agent orchestration · brnrd</title>",
        encoding="utf-8")
    (build / "robots.txt").write_text("User-agent: *\n", encoding="utf-8")
    (tmp_path / "build.html").write_text("ESCAPED", encoding="utf-8")
    settings = Settings(
        database_url="sqlite://",
        telegram_auto_webhook=False,
        frontend_dir=str(build),
    )
    with TestClient(create_app(settings)) as client:
        yield client


def test_a_prerendered_route_is_served_not_the_shell(prerendered_client):
    """The whole point of prerendering: the crawler sees the page's own head.

    A crawler runs no JavaScript, so if this falls through to the shell the
    link unfurls as the bare word "brnrd" no matter how correct the build is.
    """
    r = prerendered_client.get("/pricing")
    assert r.status_code == 200
    assert "<title>pricing · brnrd</title>" in r.text
    assert 'property="og:title"' in r.text

    nested = prerendered_client.get("/learn/agent-orchestration")
    assert nested.status_code == 200
    assert "<title>agent orchestration · brnrd</title>" in nested.text


def test_a_route_with_no_prerender_still_gets_the_shell(prerendered_client):
    """The fallback is unchanged for genuinely client-only routes."""
    r = prerendered_client.get("/repos")
    assert r.status_code == 200
    assert "<title>brnrd</title>" in r.text


def test_every_served_html_document_must_revalidate(prerendered_client):
    """Not just the shell (#1732 one route over).

    A prerendered page points at one build's hashed chunks exactly as the
    shell does. Caching it across a deploy renders a previous build's UI and
    never asks whether it is current.
    """
    for path in ("/", "/index.html", "/pricing", "/learn/agent-orchestration", "/repos"):
        r = prerendered_client.get(path)
        assert r.status_code == 200, path
        assert r.headers.get("cache-control") == "no-cache", path


def test_the_html_suffix_branch_cannot_reach_a_sibling_of_the_build_root(tmp_path):
    """``/.`` resolves to the build root itself and passes the containment
    check — and ``root.with_name(root.name + ".html")`` is root's *sibling*,
    outside the tree. The ``candidate != root`` guard is what stops it.

    Pinned at ``_safe_file`` rather than through the client on purpose, and
    the distinction is the honest part: httpx (and any RFC-3986 conforming
    client, including curl) removes the dot segment before the request is
    sent, so a ``TestClient.get("/.")`` never delivers ``spa_path == "."`` and
    a test written at that layer passes with the guard deleted — measured.
    A raw ``GET /. HTTP/1.1`` on a socket is not normalised by anything
    between h11 and this function, so the vector is not disproven, only
    unreachable through this client. Guarding it costs one comparison; the
    test says which layer it actually covers.
    """
    build = tmp_path / "build"
    build.mkdir()
    (build / "index.html").write_text("shell", encoding="utf-8")
    (tmp_path / "build.html").write_text("ESCAPED", encoding="utf-8")

    root = build.resolve()
    assert _safe_file(root, ".") == root / "index.html"
    assert _safe_file(root, "..") is None


def _client_for(build: Path):
    settings = Settings(
        database_url="sqlite://",
        telegram_auto_webhook=False,
        frontend_dir=str(build),
    )
    return TestClient(create_app(settings))


def test_the_shell_and_the_prerendered_landing_are_different_documents(tmp_path):
    """``/`` is baked; every client route gets the fallback.

    Until the fallback was renamed these were one filename, and SvelteKit
    writes the fallback *after* prerendering — it warns ("Overwriting
    build/index.html with fallback page") and then does it, so `/`'s real
    content was silently replaced by the generic shell every build.
    """
    build = tmp_path / "build"
    (build / "_app").mkdir(parents=True)
    (build / "200.html").write_text(
        "<!doctype html><title>brnrd</title>", encoding="utf-8")
    (build / "index.html").write_text(
        "<!doctype html><title>brnrd — a resident, not a chatbot</title>",
        encoding="utf-8")

    with _client_for(build) as client:
        landing = client.get("/")
        assert "a resident, not a chatbot" in landing.text
        for path in ("/repos", "/login", "/connect/BR-123"):
            r = client.get(path)
            assert r.status_code == 200, path
            assert "<title>brnrd</title>" in r.text, path
            assert "a resident" not in r.text, path


def test_a_build_without_the_renamed_fallback_still_serves_client_routes(tmp_path):
    """A backend deployed ahead of a frontend that still writes the old shape.

    It must degrade to exactly the previous behaviour — every client route on
    ``index.html`` — never to a 404 on every deep link.
    """
    build = tmp_path / "build"
    (build / "_app").mkdir(parents=True)
    (build / "index.html").write_text(
        "<!doctype html><title>brnrd</title>", encoding="utf-8")

    with _client_for(build) as client:
        for path in ("/", "/repos", "/login"):
            r = client.get(path)
            assert r.status_code == 200, path
            assert "<title>brnrd</title>" in r.text, path


def test_the_backend_prefers_the_fallback_name_the_build_actually_writes():
    """One fact, two files — so it is checked, not trusted.

    ``vite.config.ts`` decides the fallback's filename and ``spa.py`` decides
    which filename it serves as the shell. A rename on one side alone blanks
    every client route on the site, which is the failure this pins.
    """
    fallback = re.search(r"fallback:\s*'([^']+)'", VITE_CONFIG.read_text(encoding="utf-8"))
    assert fallback, "vite.config.ts no longer declares an adapter fallback"
    spa_source = (REPO_ROOT / "src" / "brnrd" / "spa.py").read_text(encoding="utf-8")
    assert f'root / "{fallback.group(1)}"' in spa_source, (
        f"vite writes the fallback as {fallback.group(1)!r}; spa.py does not prefer it"
    )

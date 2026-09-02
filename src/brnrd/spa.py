"""Serving the SvelteKit single-page app from the FastAPI process itself.

Until #847 the SPA was served entirely by the router of the PaaS this backend
then ran on (Upsun, left behind on 2026-07-31): the platform held the static
root and the ``index.html`` fallback, and its route config carried a
hand-maintained regex naming every backend prefix so those requests reached
FastAPI instead of the shell. That regex was
a second copy of ``app.py``'s ``include_router`` list — its own comment said
"keep this list in sync" — and it had grown a third copy: a set of 308
redirects in the routers, shimming the SPA-owned paths for bare ``uvicorn``,
which encoded the *complement* of the same list. Nothing checked any of them
against each other, and CI could not, because CI never ran the router.

The rule here replaces all three with one derived property:

    a request that matched no declared route belongs to the SPA,
    unless its first path segment is one the backend declares.

The claimed set is read off ``app.routes`` at mount time, so adding a router
extends it with no edit here, and ``tests/test_spa_serving.py`` asserts it
stays disjoint from the SPA's own route directory — the check that makes the
duplicate impossible to reintroduce.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse, JSONResponse
from starlette.requests import Request
from starlette.responses import Response

# Only these read a file; anything else that reached the fallback is a request
# for a route nobody declared, and gets the same 404 the API would have given.
_READ_METHODS = frozenset({"GET", "HEAD"})

# The shell contains the content-hashed chunk URLs for exactly one frontend
# build. Letting a browser heuristically cache it across a container rollout
# can therefore pin the whole UI to the previous build even while the backend
# and image are current. ``no-cache`` keeps normal browser caching but requires
# revalidation before reuse; the hashed ``/_app/immutable`` files remain free
# to use their own long-lived cache semantics.
_SHELL_CACHE_CONTROL = "no-cache"


def resolve_frontend_dir(configured: str = "") -> Path | None:
    """The directory holding the built SPA, or ``None`` when there is none.

    ``configured`` (``BRNRD_FRONTEND_DIR``) wins so a container image can put
    the build wherever it likes — the deployed image does exactly that, since
    an installed package cannot discover a source-checkout-relative path. The
    fallback is that source-checkout layout, which covers editable installs and
    the test suite. A missing directory is not an error: a backend-only
    deployment (or a checkout where ``npm run build`` has not run) serves the
    API and lets ``/`` 404, which is honest.
    """
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_dir() else None
    default = Path(__file__).resolve().parents[1] / "frontend" / "build"
    return default if default.is_dir() else None


def declared_paths(app: FastAPI, routers: Iterable[APIRouter]) -> list[str]:
    """Every URL path this app declares, routers included.

    ``app.routes`` alone is not enough and fails *quietly*: FastAPI 0.139
    stores an included router as an ``_IncludedRouter`` object with no
    ``path`` attribute at all, so reading paths off the app yields only the
    handful declared on it directly (``/healthz``, ``/docs``, the static
    mount) and silently drops every router. Caught by
    ``test_unknown_api_path_is_a_json_404_not_the_shell`` on its first run —
    the failure mode was a mistyped ``/v1/...`` answering 200 text/html.

    So the routers are passed in explicitly, and ``create_app`` iterates the
    same list it includes them from: one list, two uses, nothing to keep in
    sync. ``APIRouter.routes[i].path`` already carries the router prefix.
    """
    paths = [p for p in (getattr(r, "path", None) for r in app.routes) if isinstance(p, str)]
    for router in routers:
        paths.extend(p for p in (getattr(r, "path", None) for r in router.routes) if isinstance(p, str))
    return paths


def backend_namespaces(paths: Iterable[str]) -> frozenset[str]:
    """First path segment of each path — the route list, one word per prefix.

    ``/v1/accounts/repos`` and ``/v1/daemons/inbox`` both contribute ``v1``. A
    request whose head is in this set and that matched no route is a backend
    404, not an SPA deep link — exactly the distinction the passthru regex
    used to encode by hand.
    """
    heads: set[str] = set()
    for path in paths:
        if not path.startswith("/"):
            continue
        head = path.split("/", 2)[1]
        if head and not head.startswith("{"):
            heads.add(head)
    return frozenset(heads)


def _safe_file(root: Path, url_path: str) -> Path | None:
    """The file ``url_path`` names inside ``root``, or ``None``.

    Resolves before comparing, so ``..`` segments and symlinks out of the
    build tree cannot escape it.
    """
    candidate = (root / url_path.lstrip("/")).resolve()
    if not candidate.is_relative_to(root):
        return None
    if candidate.is_file():
        return candidate
    # adapter-static writes a prerendered page as ``<route>.html`` (its
    # ``trailingSlash`` default), so a request for ``/pricing`` names a file
    # that does not exist under that spelling and would fall through to the
    # shell — a prerender 100% correct in the build output and 0% effective
    # on the wire. Both siblings stay inside ``root`` by construction: the
    # traversal check above already ran on the resolved candidate, and
    # neither branch adds a ``..`` segment.
    # ``candidate != root`` is load-bearing, not tidiness: ``/.`` resolves
    # to ``root`` itself and passes the containment check above, and
    # ``root.with_name(root.name + ".html")`` is root's *sibling* — outside
    # the build tree. The equality guard is what keeps the suffix branch
    # from reaching it.
    if url_path and candidate != root:
        html_sibling = candidate.with_name(candidate.name + ".html")
        if html_sibling.is_file():
            return html_sibling
    index_child = candidate / "index.html"
    return index_child if index_child.is_file() else None


def mount_frontend(app: FastAPI, directory: Path, claimed: frozenset[str]) -> None:
    """Register the SPA fallback. Must be called after every ``include_router``.

    Starlette matches routes in registration order, so declared backend routes
    keep priority and only unmatched requests reach this one.
    """
    root = directory.resolve()
    # The client-route shell and the prerendered ``/`` page are two different
    # documents, and until this build they were the same filename: SvelteKit
    # writes the ``fallback`` *after* prerendering and says so out loud
    # ("Overwriting build/index.html with fallback page"), silently replacing
    # the baked landing with the generic shell. The fallback is ``200.html``
    # now, so ``index.html`` is free to be ``/``'s real prerendered content.
    #
    # ``index.html`` stays a legal shell for a build that has no ``200.html``:
    # a backend deployed ahead of a frontend that still writes the old shape
    # then degrades to exactly today's behaviour instead of 404ing every
    # client route. Resolved per request rather than at mount, because the
    # build directory can be replaced under a running process.
    def _shell() -> Path:
        candidate = root / "200.html"
        return candidate if candidate.is_file() else root / "index.html"

    @app.api_route(
        "/{spa_path:path}",
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
        name="spa_fallback",
    )
    def spa_fallback(request: Request, spa_path: str) -> Response:
        head = spa_path.split("/", 1)[0]
        if head in claimed or request.method not in _READ_METHODS:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        asset = _safe_file(root, spa_path)
        if asset is not None:
            response = FileResponse(asset)
            # Every HTML document this build ships points at one build's
            # hashed chunks, so all of them must revalidate — not just the
            # shell. ``/index.html`` is another spelling of ``/``; a
            # prerendered ``pricing.html`` is a different document with the
            # same lifetime. Caching either across a deploy is #1732 one
            # route over: a page that renders a previous build's UI and
            # never asks whether it is current.
            if asset.suffix == ".html":
                response.headers["Cache-Control"] = _SHELL_CACHE_CONTROL
            return response
        # `_app/` is SvelteKit's own build namespace — every URL under it is
        # minted by the build (hashed chunks, version.json), never a client
        # route, so the shell is always the wrong answer there. Serving it
        # anyway turned every missing-chunk fetch into `200 text/html`, which
        # a browser's `import()` reports as the opaque "Importing a module
        # script failed" and SvelteKit renders as a 500 — a soft 404 wearing
        # a server error's face (measured live, 2026-08-14). A real 404 is
        # also what SvelteKit's own stale-deploy recovery expects to see.
        if spa_path == "_app" or spa_path.startswith("_app/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        shell = _shell()
        if not shell.is_file():
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        # 200, not 404: the client router owns the path from here, including
        # its own not-found page. This is the behaviour the platform router
        # gave us and the behaviour a deep link needs. The shell points at one
        # build's hashed chunks, so every reuse must first revalidate it.
        response = FileResponse(shell, media_type="text/html")
        response.headers["Cache-Control"] = _SHELL_CACHE_CONTROL
        return response

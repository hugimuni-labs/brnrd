"""Transport-security headers, owned by the app rather than by a host.

HSTS used to be three lines of a PaaS route config (`.upsun/config.yaml`,
deleted with that host on 2026-07-31) — a route-level
``tls.strict_transport_security.enabled: true``, added 2026-07-27 after a probe
found brnrd.dev serving a security page that promises TLS while sending no
``Strict-Transport-Security`` header, leaving first-visit downgrade open. The
2026-07-30 move to Scaleway carried the container and left that line behind
with the route table it lived in, and production served no HSTS header at all
until this module (verified absent 2026-07-31 09:27Z).

That is the lesson worth keeping, not the outage: **a security guarantee that
lives in one provider's config ends at the next migration, silently, because
nothing in the repository knows it was ever there.** This one lives in the
application, so it travels with the image to any host.

Deliberately narrow, and the same shape the route config chose:

- **``max-age`` only.** ``includeSubDomains`` is an inventory question about
  every hostname under the domain, and ``preload`` is semi-irreversible
  (removal from the browser list is slow). Both are the operator's call;
  neither is a safe default for someone who did not ask for it.
- **Only on a request that arrived over TLS.** A header instructing a browser
  never to speak HTTP to this host again is a self-inflicted outage when a
  plain-HTTP dev server on localhost emits it. ``X-Forwarded-Proto`` is
  honoured because every deployment of this app sits behind a TLS-terminating
  proxy; trusting it is safe in the one direction that matters, since RFC 6797
  §8.1 requires a browser to ignore the header on an insecure response anyway.
- **Opt-out, not opt-in** (``BRNRD_HSTS_MAX_AGE=0``): a self-hoster whose own
  edge already sets the header can turn this off, but nobody has to know the
  setting exists in order to be protected by it.
"""

from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Iterable
from pathlib import Path

from starlette.types import ASGIApp, Message, Receive, Scope, Send

HEADER = b"strict-transport-security"


def request_is_https(scope: Scope) -> bool:
    """Did this request reach us over TLS, directly or through a proxy?"""

    for name, value in scope.get("headers") or ():
        if name == b"x-forwarded-proto":
            # A proxy may forward a comma-separated chain; the client-facing
            # hop is the first entry, and it is the only one a browser saw.
            first = value.decode("latin-1").split(",")[0].strip().lower()
            return first == "https"
    return str(scope.get("scheme") or "").lower() == "https"


class HSTSMiddleware:
    """Add ``Strict-Transport-Security`` to every HTTPS response.

    Written against the raw ASGI interface rather than
    ``BaseHTTPMiddleware`` on purpose: this app streams a 25 s inbox
    long-poll, and ``BaseHTTPMiddleware`` buffers through an anyio task
    pair that has a long history of interacting badly with long-lived
    responses. A header rewrite has no reason to touch the body at all.
    """

    def __init__(self, app: ASGIApp, *, max_age: int) -> None:
        self.app = app
        self.max_age = max_age
        self.value = f"max-age={max_age}".encode("latin-1")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self.max_age <= 0 or not request_is_https(scope):
            await self.app(scope, receive, send)
            return

        async def send_with_hsts(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                # An edge that already sets the header wins: overwriting it
                # would silently downgrade an operator's stronger policy
                # (theirs may carry includeSubDomains or a longer max-age).
                if not any(name.lower() == HEADER for name, _ in headers):
                    headers.append((HEADER, self.value))
            await send(message)

        await self.app(scope, receive, send_with_hsts)


# --- Content-Security-Policy (Report-Only) -----------------------------
#
# A naive `script-src 'self'` at the Cloudflare edge white-screened
# brnrd.dev: SvelteKit's static build (`@sveltejs/adapter-static`, no
# server process — see `spa.py`) bakes one inline `<script>` bootstrap
# into `index.html` per build, and `'self'` blocks any inline script
# outright.
#
# SvelteKit's own `kit.csp` cannot fix this here. Under `adapter-static`
# it has exactly one delivery mode — a `<meta http-equiv=...>` tag baked
# into the static HTML at build time, since there is no per-request
# server to emit a header — and a `<meta>`-delivered policy cannot be
# `Content-Security-Policy-Report-Only`; the CSP spec only allows the
# report-only variant over an HTTP header. A meta-tag CSP would also be
# *enforcing* the moment it shipped, which is the one thing this change
# is explicitly not supposed to do yet. So the hash has to be computed
# and served from here instead — same seam as HSTS, and per the same
# `spa.py` architecture where the backend, not SvelteKit, is the only
# thing that ever emits a response header for the built SPA.
#
# The inline script's content is not a fixed string: SvelteKit mints a
# fresh `__sveltekit_<id>` global and fresh content-hashed chunk
# filenames into it on every build. Hashing a value written down once
# would silently drift the first time the frontend rebuilds — so this
# reads the *actual* shipped `index.html` and hashes whatever inline
# script is in it right now.

_INLINE_SCRIPT_RE = re.compile(
    rb"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)

CSP_HEADER = b"content-security-policy-report-only"

# Widen only for a real, observed violation — this is deliberately the
# narrowest policy that still lets the current app run.
_BASE_DIRECTIVES: tuple[tuple[str, str], ...] = (
    ("default-src", "'self'"),
    ("style-src", "'self' 'unsafe-inline'"),
    ("img-src", "'self' data:"),
    ("connect-src", "'self'"),
    ("font-src", "'self'"),
    ("object-src", "'none'"),
    ("base-uri", "'self'"),
    ("frame-ancestors", "'none'"),
)


def inline_script_hashes(html: bytes) -> list[str]:
    """CSP ``'sha256-...'`` sources, one per inline ``<script>`` in *html*.

    A CSP hash source matches the exact bytes a browser hashes: the text
    between a script tag's ``>`` and its ``</script>``, verbatim — no
    trimming, no re-serialising. ``<script src=...>`` tags are excluded
    (nothing inline to hash; ``'self'`` already covers same-origin files).
    """
    return [
        "'sha256-" + base64.b64encode(hashlib.sha256(match.group(1)).digest()).decode("ascii") + "'"
        for match in _INLINE_SCRIPT_RE.finditer(html)
    ]


def build_csp_report_only(script_hashes: Iterable[str]) -> str:
    """Assemble the ``Content-Security-Policy-Report-Only`` header value."""
    script_src = " ".join(("'self'", *script_hashes))
    directives = [("script-src", script_src), *_BASE_DIRECTIVES]
    return "; ".join(f"{name} {value}" for name, value in directives)


def csp_report_only_value(frontend_dir: Path | None) -> str | None:
    """The Report-Only CSP header value for the build in *frontend_dir*.

    ``None`` when there is nothing to hash against — no frontend directory
    (backend-only deployment) or no ``index.html`` in it (checkout where
    ``npm run build`` has not run) — mirroring the "missing build is not an
    error" stance ``resolve_frontend_dir`` already takes: shipping no header
    beats shipping one with a hash that matches nothing.
    """
    if frontend_dir is None:
        return None
    index = frontend_dir / "index.html"
    if not index.is_file():
        return None
    return build_csp_report_only(inline_script_hashes(index.read_bytes()))


class CSPReportOnlyMiddleware:
    """Add ``Content-Security-Policy-Report-Only`` to every response.

    Report-Only, never enforcing: the maintainer flips to the enforcing
    header once the violation stream from this one is clean (see module
    docstring above for why SvelteKit's own ``kit.csp`` cannot ship the
    report-only variant under this app's static-adapter architecture).
    Same raw-ASGI shape as ``HSTSMiddleware`` and for the same reason — a
    header rewrite has no business touching a streamed body.
    """

    def __init__(self, app: ASGIApp, *, value: str) -> None:
        self.app = app
        self.value = value.encode("latin-1")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_csp(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                if not any(name.lower() == CSP_HEADER for name, _ in headers):
                    headers.append((CSP_HEADER, self.value))
            await send(message)

        await self.app(scope, receive, send_with_csp)

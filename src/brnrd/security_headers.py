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

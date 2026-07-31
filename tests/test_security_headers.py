"""HSTS is the app's promise now, not a host's route-table setting.

The regression this suite exists to make impossible: `.upsun/config.yaml`
carried `tls.strict_transport_security.enabled: true`, added 2026-07-27 after a
probe found brnrd.dev promising TLS on its security page and sending no
`Strict-Transport-Security` header. The 2026-07-30 move to Scaleway carried the
container and left the route table behind, and production served no HSTS at all
until 2026-07-31 — nothing in the repository knew the guarantee had ever
existed, so nothing could notice it was gone.

Every assertion below is against a response the app actually produced. A
header this suite only string-matched in `security_headers.py` would be
green for a middleware nobody wired in.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from brnrd.app import create_app
from brnrd.config import Settings

HEADER = "strict-transport-security"


def _client(**overrides) -> TestClient:
    settings = Settings(database_url="sqlite://", **overrides)
    # `base_url` decides the ASGI scope's scheme, which is what the middleware
    # reads when no proxy header is present.
    return TestClient(create_app(settings), base_url="https://testserver")


def test_https_response_carries_a_one_year_max_age():
    with _client() as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.headers[HEADER] == "max-age=31536000"


def test_plain_http_gets_no_hsts():
    """A dev server on localhost must not tell a browser to never speak HTTP.

    RFC 6797 §8.1 says a browser ignores the header on an insecure response,
    so this is belt-and-braces — but the belt is what stops a self-hoster
    running `python -m brnrd` on a LAN address from locking themselves out
    of their own host the first time they hit it.
    """
    with TestClient(
        create_app(Settings(database_url="sqlite://")), base_url="http://testserver"
    ) as client:
        response = client.get("/healthz")
    assert HEADER not in response.headers


def test_forwarded_proto_is_what_the_browser_saw():
    """Behind a TLS-terminating proxy the ASGI scheme is `http`; the header is
    the only evidence the browser's hop was HTTPS — which is the *entire*
    production topology, so getting this wrong ships an app that never sets
    HSTS anywhere that matters."""
    with TestClient(
        create_app(Settings(database_url="sqlite://")), base_url="http://testserver"
    ) as client:
        response = client.get("/healthz", headers={"x-forwarded-proto": "https"})
    assert response.headers[HEADER] == "max-age=31536000"


def test_forwarded_proto_chain_reads_the_client_facing_hop():
    """`X-Forwarded-Proto: https, http` means the browser used TLS and an
    internal hop did not. The first entry is the one a browser can act on."""
    with TestClient(
        create_app(Settings(database_url="sqlite://")), base_url="http://testserver"
    ) as client:
        secure = client.get("/healthz", headers={"x-forwarded-proto": "https, http"})
        insecure = client.get("/healthz", headers={"x-forwarded-proto": "http, https"})
    assert secure.headers[HEADER] == "max-age=31536000"
    assert HEADER not in insecure.headers


def test_zero_max_age_disables_the_header_entirely():
    """The opt-out is for an operator whose own edge already sets HSTS. A
    `max-age=0` header is not "off" — it actively *clears* a browser's stored
    policy — so the setting must suppress the header, not emit a zero."""
    with _client(hsts_max_age=0) as client:
        response = client.get("/healthz")
    assert HEADER not in response.headers


def test_an_operator_set_header_is_never_overwritten():
    """An edge policy may be stronger than ours (includeSubDomains, a longer
    max-age). Appending ours beside it, or replacing it, silently downgrades
    a deliberate choice."""
    from brnrd.security_headers import HSTSMiddleware

    async def app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/plain"),
                    (b"Strict-Transport-Security", b"max-age=63072000; includeSubDomains"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b"ok"})

    # No `with`: these bare-ASGI stubs answer HTTP only, and entering the
    # client's context would drive a lifespan they do not implement.
    response = TestClient(
        HSTSMiddleware(app, max_age=31536000), base_url="https://testserver"
    ).get("/")

    values = response.headers.get_list(HEADER)
    assert values == ["max-age=63072000; includeSubDomains"]


@pytest.mark.parametrize("path", ["/healthz", "/v1/stats/version"])
def test_the_header_is_not_route_specific(path):
    """A security header attached to one router is a header that stops
    existing the next time a route moves."""
    with _client() as client:
        assert client.get(path).headers[HEADER] == "max-age=31536000"


def test_body_messages_pass_through_one_for_one_unbuffered():
    """Written against raw ASGI rather than `BaseHTTPMiddleware` on purpose:
    this app holds a 25 s inbox long-poll open, and the buffering variant has
    a long history of breaking long-lived responses.

    Asserted at the ASGI seam rather than through `TestClient`, because the
    test transport coalesces chunks — a client-level assertion reads identical
    whether the middleware buffered or not, which is exactly the kind of check
    that is green for the wrong reason. Here the middleware's own `send` calls
    are recorded: each `http.response.body` must arrive as its own message,
    byte-identical, with `more_body` intact.
    """
    import asyncio

    from brnrd.security_headers import HSTSMiddleware

    emitted = [
        {"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]},
        {"type": "http.response.body", "body": b"one", "more_body": True},
        {"type": "http.response.body", "body": b"two", "more_body": False},
    ]

    async def app(scope, receive, send):
        for message in emitted:
            await send(dict(message))

    seen: list[dict] = []

    async def record(message):
        seen.append(message)

    scope = {"type": "http", "scheme": "https", "headers": [], "method": "GET", "path": "/"}

    async def receive():  # pragma: no cover - never called on this path
        return {"type": "http.disconnect"}

    asyncio.run(HSTSMiddleware(app, max_age=31536000)(scope, receive, record))

    assert [m["type"] for m in seen] == [
        "http.response.start",
        "http.response.body",
        "http.response.body",
    ]
    assert seen[1:] == emitted[1:], "the middleware rewrote or merged a body message"
    assert (HEADER.encode(), b"max-age=31536000") in seen[0]["headers"]

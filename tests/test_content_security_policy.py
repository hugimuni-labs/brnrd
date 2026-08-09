"""App-side Report-Only CSP, hashed against the actual built SPA (A-2).

Why this can't be SvelteKit's own `kit.csp`: this app's frontend build uses
`@sveltejs/adapter-static` (see `src/frontend/vite.config.ts`) with no
SvelteKit server process at runtime (`spa.py` serves the build as flat
files). `kit.csp` under that adapter has exactly one delivery mode — a
`<meta http-equiv=...>` tag baked into `index.html` at build time — and a
`<meta>`-delivered CSP cannot be `Content-Security-Policy-Report-Only` (the
CSP spec only allows the report-only variant over an HTTP header). So the
hash is computed here, from whatever `index.html` is actually being served,
and shipped as a response header alongside HSTS.

A hard-coded hash would be a lie the first time the frontend rebuilds:
SvelteKit mints a fresh `__sveltekit_<id>` global and fresh content-hashed
chunk filenames into the inline bootstrap script on every build. Every
assertion below computes its expected hash from the same bytes the app
itself hashes, never a value copied out once and left to drift.
"""

from __future__ import annotations

import base64
import hashlib

from fastapi.testclient import TestClient

from brnrd.config import Settings
from brnrd.security_headers import (
    build_csp_report_only,
    csp_report_only_value,
    inline_script_hashes,
)

HEADER = "content-security-policy-report-only"
ENFORCING_HEADER = "content-security-policy"

_INLINE_SCRIPT = '{\n\t__sveltekit_abc123 = { base: "" };\n\tconsole.log("hydrate");\n}'


def _sha256_source(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return "'sha256-" + base64.b64encode(digest).decode("ascii") + "'"


def _build_dir(tmp_path, *, script: str | None = _INLINE_SCRIPT):
    build = tmp_path / "build"
    build.mkdir(parents=True)
    if script is None:
        html = "<!doctype html><html><head></head><body>no script here</body></html>"
    else:
        html = f"<!doctype html><html><head></head><body><script>{script}</script></body></html>"
    (build / "index.html").write_text(html, encoding="utf-8")
    return build


def _app_client(build_dir) -> TestClient:
    from brnrd.app import create_app

    settings = Settings(
        database_url="sqlite://",
        telegram_auto_webhook=False,
        frontend_dir=str(build_dir),
    )
    return TestClient(create_app(settings))


def test_inline_script_hashes_matches_the_exact_bytes_a_browser_would_hash():
    html = b"<script>const x = 1;</script>"
    assert inline_script_hashes(html) == [_sha256_source("const x = 1;")]


def test_external_script_src_contributes_no_hash():
    html = b'<script src="/app.js"></script>'
    assert inline_script_hashes(html) == []


def test_multiple_inline_scripts_each_get_their_own_hash():
    html = b"<script>one();</script><script>two();</script>"
    assert inline_script_hashes(html) == [_sha256_source("one();"), _sha256_source("two();")]


def test_build_csp_report_only_keeps_the_agreed_baseline_and_folds_in_hashes():
    value = build_csp_report_only(["'sha256-AAAA'"])
    assert value == (
        "script-src 'self' 'sha256-AAAA'; "
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "font-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'"
    )


def test_csp_report_only_value_is_none_without_a_build(tmp_path):
    assert csp_report_only_value(None) is None
    assert csp_report_only_value(tmp_path / "nothing-here") is None


def test_served_response_carries_the_hash_of_the_real_shipped_script(tmp_path):
    build = _build_dir(tmp_path)
    with _app_client(build) as client:
        response = client.get("/")
    assert response.status_code == 200
    header = response.headers[HEADER]
    assert "script-src 'self' " + _sha256_source(_INLINE_SCRIPT) in header
    assert "'unsafe-inline'" not in header.split("script-src", 1)[1].split(";", 1)[0]


def test_no_enforcing_csp_header_is_ever_set(tmp_path):
    """The whole point of this change is Report-Only; the maintainer flips
    the switch by hand once the violation stream is clean."""
    build = _build_dir(tmp_path)
    with _app_client(build) as client:
        response = client.get("/")
    assert ENFORCING_HEADER not in response.headers


def test_the_header_is_not_route_specific(tmp_path):
    build = _build_dir(tmp_path)
    with _app_client(build) as client:
        assert HEADER in client.get("/healthz").headers
        assert HEADER in client.get("/repos").headers


def test_a_build_with_no_inline_script_still_forbids_unsafe_inline(tmp_path):
    build = _build_dir(tmp_path, script=None)
    with _app_client(build) as client:
        header = client.get("/").headers[HEADER]
    assert header.startswith("script-src 'self';") or header.split(";", 1)[0] == "script-src 'self'"


def test_missing_build_directory_emits_no_csp_header(tmp_path):
    from brnrd.app import create_app

    settings = Settings(
        database_url="sqlite://",
        telegram_auto_webhook=False,
        frontend_dir=str(tmp_path / "nothing-here"),
    )
    with TestClient(create_app(settings)) as client:
        assert HEADER not in client.get("/healthz").headers


def test_a_fresh_build_with_a_different_inline_script_changes_the_hash(tmp_path):
    """Guards against a hash frozen at import time / caching the wrong bytes."""
    build_a = _build_dir(tmp_path / "a", script="console.log('a');")
    build_b = _build_dir(tmp_path / "b", script="console.log('b');")
    with _app_client(build_a) as client:
        header_a = client.get("/").headers[HEADER]
    with _app_client(build_b) as client:
        header_b = client.get("/").headers[HEADER]
    assert _sha256_source("console.log('a');") in header_a
    assert _sha256_source("console.log('b');") in header_b
    assert header_a != header_b

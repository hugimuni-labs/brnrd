"""HTTP transport for the GitHub REST API.

Sync, ``requests``-based — the OSS daemon stays sync. The managed
brnrd backend plugs its own async ``httpx`` client against the same
``paths`` and ``parse`` modules without touching this file. Keeping
the transport isolated to one module is what makes that swap clean.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import requests

from .constants import _API_ROOT, _API_VERSION, _HTTP_TIMEOUT, _USER_AGENT

# One Session for the gate's single loop thread: keep-alive reuses the
# TCP/TLS connection across polls (and conditional ETag requests) instead
# of dialing api.github.com fresh each call. The managed brnrd backend
# plugs its own async httpx client and never touches this module. See
# ``kb/subject-daemon.md`` → gate responsiveness.
_SESSION = requests.Session()


class GitHubAPIError(RuntimeError):
    """Raised on any non-2xx GitHub API response."""

    def __init__(self, status: int, message: str, *, headers: dict | None = None):
        super().__init__(f"github {status}: {message}")
        self.status = status
        self.message = message
        self.headers = headers or {}


def _etag_cache_key(method: str, path: str) -> str:
    """Cache key for a conditional request.

    Only ``(method, path)`` is included; the ``since`` cursor in
    polling params changes on every cursor advance, but we still want
    to send the most recent ETag for that path so GitHub can answer
    304 when its underlying response is unchanged. GitHub's ETag is a
    function of the response body, so a stale-keyed conditional just
    means one wasted 200 before the new ETag is cached — self-healing.
    """
    return f"{method} {path}"


def _request(
    token: str,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    etag_store: dict[str, str] | None = None,
) -> tuple[Any, dict[str, str]]:
    """Issue a GitHub API call. Returns ``(parsed_body, response_headers)``.

    Raises ``GitHubAPIError`` on non-2xx responses; the ``Retry-After`` /
    ``X-RateLimit-*`` headers are surfaced on the exception so the caller
    can sleep until reset.

    When ``etag_store`` is provided, an ``If-None-Match`` header is sent
    using any previously-cached ETag for ``(method, path)``. On HTTP 304
    the helper returns ``(None, headers)`` without raising. On a 2xx
    response with an ``ETag`` header the store is updated in place so
    the next call against the same endpoint can be conditional.
    Conditional GETs are free against GitHub's REST rate limit when they
    return 304, which on quiet repos is the steady state.
    """
    url = _API_ROOT + path
    clean_params = None
    if params:
        # Filter out None / empty values so callers can pass optional
        # cursors without crafting URL strings by hand.
        clean = {k: v for k, v in params.items() if v not in (None, "")}
        if clean:
            clean_params = clean
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": _USER_AGENT,
        "X-GitHub-Api-Version": _API_VERSION,
    }
    cache_key: str | None = None
    if etag_store is not None:
        cache_key = _etag_cache_key(method, path)
        cached_etag = etag_store.get(cache_key)
        if cached_etag:
            headers["If-None-Match"] = cached_etag
    response = _SESSION.request(
        method,
        url,
        params=clean_params,
        json=body if body is not None else None,
        headers=headers,
        timeout=_HTTP_TIMEOUT,
    )
    response_headers = {k: v for k, v in response.headers.items()}
    if response.status_code == 304:
        # Resource unchanged since the cached ETag was issued. GitHub
        # doesn't count conditional 304s against the REST rate limit;
        # this is the whole point of threading the ETag store.
        return None, response_headers
    if not 200 <= response.status_code < 300:
        raise GitHubAPIError(
            response.status_code,
            _github_error_message(response),
            headers=response_headers,
        )
    if cache_key is not None:
        new_etag = response.headers.get("ETag")
        if new_etag:
            etag_store[cache_key] = new_etag
    if not response.content:
        return None, response_headers
    return response.json(), response_headers


def _github_error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        message = payload.get("message")
        if message:
            return str(message)[:500]
    if response.text:
        return response.text[:500]
    return response.reason or ""


def _download_url(
    token: str, url: str, dest: Path, *, timeout: float = _HTTP_TIMEOUT,
) -> bool:
    """GET an arbitrary URL and write its body to *dest*. Returns success.

    For inline image attachments (``user-attachments/assets/...`` links
    embedded in issue/PR/comment bodies) — these live outside the
    ``_API_ROOT``-scoped REST surface ``_request`` targets, so they need
    a plain GET rather than the JSON API helpers above. ``Authorization``
    is only honoured by ``requests`` on the first hop: it's stripped
    automatically on any cross-host redirect (github.com's attachment
    URL typically 302s to a signed, time-limited S3 object), so this
    never leaks the token to whatever host actually serves the bytes.
    Any non-2xx or transport error returns ``False`` rather than raising —
    a failed attachment download shouldn't drop the whole event.
    """
    try:
        response = _SESSION.get(
            url,
            headers={"Authorization": f"Bearer {token}", "User-Agent": _USER_AGENT},
            timeout=timeout,
            stream=True,
        )
    except requests.RequestException:
        return False
    if not 200 <= response.status_code < 300:
        return False
    try:
        with open(dest, "wb") as fh:
            for chunk in response.iter_content(65536):
                fh.write(chunk)
    except OSError:
        return False
    return True


def _api_get(
    token: str,
    path: str,
    params: dict[str, Any] | None = None,
    *,
    etag_store: dict[str, str] | None = None,
) -> Any:
    payload, _ = _request(
        token, "GET", path, params=params, etag_store=etag_store,
    )
    return payload


def _api_post(token: str, path: str, body: dict[str, Any]) -> Any:
    payload, _ = _request(token, "POST", path, body=body)
    return payload


def _api_patch(token: str, path: str, body: dict[str, Any]) -> Any:
    payload, _ = _request(token, "PATCH", path, body=body)
    return payload

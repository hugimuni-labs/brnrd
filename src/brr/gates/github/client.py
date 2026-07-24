"""HTTP transport for the GitHub REST API.

Sync, ``requests``-based — the OSS daemon stays sync. The managed
brnrd backend plugs its own async ``httpx`` client against the same
``paths`` and ``parse`` modules without touching this file. Keeping
the transport isolated to one module is what makes that swap clean.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from . import paths
from .constants import _API_ROOT, _API_VERSION, _HTTP_TIMEOUT, _USER_AGENT

# Hosts allowed to receive the operator's GitHub token on an attachment
# fetch. Attachment URLs are extracted from comment bodies — attacker-chosen
# input — so this is an allowlist, never a blocklist. Subdomains of these
# are included; unrelated hosts get an unauthenticated request.
_TOKEN_BEARING_HOSTS = ("github.com", "githubusercontent.com")

# Matches the telegram gate's cap (``gates/telegram.py``). An attachment
# fetch with no ceiling is an unbounded write into the daemon's temp dir,
# reachable by anyone who can post a comment.
_MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024

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


def _is_github_host(url: str) -> bool:
    """Is *url* served by GitHub itself, so the token may ride the request?

    Exact host match or a subdomain of one — never a suffix match, which
    ``attacker-github.com`` and ``github.com.evil.test`` both satisfy.
    """
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    if not host:
        return False
    return any(
        host == allowed or host.endswith("." + allowed)
        for allowed in _TOKEN_BEARING_HOSTS
    )


def _download_url(
    token: str, url: str, dest: Path, *, timeout: float = _HTTP_TIMEOUT,
) -> bool:
    """GET an attachment URL and write its body to *dest*. Returns success.

    For inline image attachments (``user-attachments/assets/...`` links
    embedded in issue/PR/comment bodies) — these live outside the
    ``_API_ROOT``-scoped REST surface ``_request`` targets, so they need
    a plain GET rather than the JSON API helpers above.

    **The token rides only to GitHub's own hosts.** The URL comes out of a
    comment body via a regex that matches *any* ``https?://`` link
    (:func:`brr.gates.github.attachments.extract_image_urls`), so it is
    attacker-chosen input: without this check, an image embed pointed at
    any host in the world was served the operator's GitHub token on hop 1.
    An earlier docstring here reasoned — correctly — that ``requests``
    strips ``Authorization`` on a *cross-host redirect*, and github.com's
    attachment URLs do 302 to signed S3 objects. That defence is real and
    guards the wrong case: a directly-named foreign URL never redirects,
    so hop 1 *is* the exfiltration. Non-GitHub URLs are still fetched, but
    unauthenticated — a public image stays readable, a credential does not
    leave.

    Responses are capped at :data:`_MAX_ATTACHMENT_BYTES`, matching the
    telegram and cloud gates: the same primitive is otherwise an
    unbounded write into the daemon's temp dir.

    Any non-2xx or transport error returns ``False`` rather than raising —
    a failed attachment download shouldn't drop the whole event.
    """
    headers = {"User-Agent": _USER_AGENT}
    if _is_github_host(url):
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = _SESSION.get(url, headers=headers, timeout=timeout, stream=True)
    except requests.RequestException:
        return False
    if not 200 <= response.status_code < 300:
        return False
    written = 0
    try:
        with open(dest, "wb") as fh:
            for chunk in response.iter_content(65536):
                written += len(chunk)
                if written > _MAX_ATTACHMENT_BYTES:
                    # Refuse the whole file rather than keep a truncated
                    # image: a half-written attachment is worse than none.
                    fh.close()
                    dest.unlink(missing_ok=True)
                    return False
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


def get_collaborator_permission(repo: str, username: str, token: str) -> str | None:
    """Return *username*'s permission level on *repo*, or ``None``.

    One of ``admin``/``write``/``maintain``/``read``/``none`` on success.
    ``None`` covers both "not a collaborator" (404) and any other
    transport/API failure — the authorization gate (#408) treats both
    the same way: deny unless the author is separately allowlisted.
    Never raises.
    """
    try:
        payload = _api_get(token, paths.collaborator_permission(repo, username))
    except GitHubAPIError:
        return None
    if not isinstance(payload, dict):
        return None
    permission = payload.get("permission")
    return str(permission) if isinstance(permission, str) and permission else None

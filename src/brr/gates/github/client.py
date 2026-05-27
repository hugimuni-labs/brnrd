"""HTTP transport for the GitHub REST API.

Sync, ``requests``-based — the OSS daemon stays sync. The managed
brnrd backend plugs its own async ``httpx`` client against the same
``paths`` and ``parse`` modules without touching this file. Keeping
the transport isolated to one module is what makes that swap clean.
"""

from __future__ import annotations

from typing import Any

import requests

from .constants import _API_ROOT, _API_VERSION, _HTTP_TIMEOUT, _USER_AGENT


class GitHubAPIError(RuntimeError):
    """Raised on any non-2xx GitHub API response."""

    def __init__(self, status: int, message: str, *, headers: dict | None = None):
        super().__init__(f"github {status}: {message}")
        self.status = status
        self.message = message
        self.headers = headers or {}


def _request(
    token: str,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, str]]:
    """Issue a GitHub API call. Returns ``(parsed_body, response_headers)``.

    Raises ``GitHubAPIError`` on non-2xx responses; the ``Retry-After`` /
    ``X-RateLimit-*`` headers are surfaced on the exception so the caller
    can sleep until reset.
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
    response = requests.request(
        method,
        url,
        params=clean_params,
        json=body if body is not None else None,
        headers=headers,
        timeout=_HTTP_TIMEOUT,
    )
    response_headers = {k: v for k, v in response.headers.items()}
    if not 200 <= response.status_code < 300:
        raise GitHubAPIError(
            response.status_code,
            _github_error_message(response),
            headers=response_headers,
        )
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


def _api_get(token: str, path: str, params: dict[str, Any] | None = None) -> Any:
    payload, _ = _request(token, "GET", path, params=params)
    return payload


def _api_post(token: str, path: str, body: dict[str, Any]) -> Any:
    payload, _ = _request(token, "POST", path, body=body)
    return payload


def _api_patch(token: str, path: str, body: dict[str, Any]) -> Any:
    payload, _ = _request(token, "PATCH", path, body=body)
    return payload

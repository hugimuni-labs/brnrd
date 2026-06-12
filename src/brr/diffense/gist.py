"""Publish diffense packs as user-owned GitHub gists.

The gist path is the durable rich-review route: the daemon writes the
pack JSON to the user's own GitHub account, then links brnrd's static
renderer shell with ``?pack=<raw gist url>``. brnrd serves code only; the
browser fetches the pack directly from GitHub.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

DEFAULT_FILENAME = "diffense-pack.json"
DEFAULT_RENDER_BASE_URL = "https://brnrd.dev/r"
_DEFAULT_DESCRIPTION = "brr diffense review pack"
_GH_TIMEOUT_S = 30
_RENDER_PROBE_TIMEOUT_S = 5
_RENDER_PROBE_PACK_URL = "https://example.invalid/diffense-pack-probe.json"
_RENDER_SHELL_MARKER = 'URLSearchParams(location.search).get("pack")'
_REVIEW_PAGE_MARKER = 'id="diffense-pack"'


@dataclass(frozen=True)
class GistPack:
    """The user-owned durable copy of a diffense pack."""

    html_url: str
    raw_url: str
    filename: str = DEFAULT_FILENAME


def pack_json(pack: dict) -> str:
    """Stable pretty JSON for the gist file."""
    return json.dumps(pack, ensure_ascii=False, indent=2) + "\n"


def render_url(raw_url: str, *, base_url: str = DEFAULT_RENDER_BASE_URL) -> str:
    """Build a brnrd renderer-shell URL for a raw pack URL."""
    sep = "&" if "?" in base_url else "?"
    return f"{base_url.rstrip('/')}{sep}{urlencode({'pack': raw_url})}"


def renderer_shell_available(
    base_url: str = DEFAULT_RENDER_BASE_URL,
    *,
    timeout_s: int = _RENDER_PROBE_TIMEOUT_S,
    fetch: Callable | None = None,
) -> bool:
    """Return whether *base_url* serves the gist-backed renderer shell.

    The gist path only works after brnrd has deployed ``GET /r``. During a
    rollout, older deployments still serve ``/r/{token}`` but return 404 for
    ``/r?pack=...``; probing first keeps PR bodies from publishing dead
    links and lets the caller fall back to the transient relay.
    """
    fetch = fetch or urlopen
    request = Request(
        render_url(_RENDER_PROBE_PACK_URL, base_url=base_url),
        headers={"User-Agent": "brr-diffense/0.1"},
    )
    try:
        response = fetch(request, timeout=timeout_s)
        try:
            status = getattr(response, "status", None)
            if status is None and hasattr(response, "getcode"):
                status = response.getcode()
            if status is not None and not 200 <= int(status) < 300:
                return False
            body = response.read(256_000)
        finally:
            close = getattr(response, "close", None)
            if close is not None:
                close()
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return False
    text = body.decode("utf-8", errors="ignore")
    return _RENDER_SHELL_MARKER in text


def review_url_available(
    url: str,
    *,
    timeout_s: int = _RENDER_PROBE_TIMEOUT_S,
    fetch: Callable | None = None,
) -> bool:
    """Return whether an already-built rich review URL renders."""
    fetch = fetch or urlopen
    request = Request(url, headers={"User-Agent": "brr-diffense/0.1"})
    try:
        response = fetch(request, timeout=timeout_s)
        try:
            status = getattr(response, "status", None)
            if status is None and hasattr(response, "getcode"):
                status = response.getcode()
            if status is not None and not 200 <= int(status) < 300:
                return False
            body = response.read(256_000)
        finally:
            close = getattr(response, "close", None)
            if close is not None:
                close()
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return False
    text = body.decode("utf-8", errors="ignore")
    return _REVIEW_PAGE_MARKER in text


def create_pack_gist(
    pack: dict,
    *,
    repo: str | None = None,
    filename: str = DEFAULT_FILENAME,
    description: str = _DEFAULT_DESCRIPTION,
    timeout_s: int = _GH_TIMEOUT_S,
    repo_visibility_fn: Callable[[str], str | None] | None = None,
) -> GistPack | None:
    """Create a secret gist containing *pack* and return its URLs.

    Secret gists are unlisted but public to anyone with the URL. When the
    pack names a GitHub repo that is private or internal, this function
    declines to create a durable public-capability artifact and lets the
    caller fall back to the transient relay.
    """
    repo = repo or _pack_repo(pack)
    if repo:
        visibility = (
            repo_visibility_fn(repo)
            if repo_visibility_fn is not None
            else _repo_visibility(repo, timeout_s=timeout_s)
        )
        if visibility is None or visibility.strip().lower() != "public":
            return None

    try:
        created = subprocess.run(
            ["gh", "gist", "create", "-f", filename, "-", "-d", description],
            input=pack_json(pack),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if created.returncode != 0:
        return None
    html_url = _last_url(created.stdout)
    if not html_url:
        return None
    raw_url = _fetch_raw_url(html_url, filename, timeout_s=timeout_s)
    if not raw_url:
        raw_url = _fallback_raw_url(html_url, filename)
    if not raw_url:
        return None
    return GistPack(html_url=html_url, raw_url=raw_url, filename=filename)


def _pack_repo(pack: dict) -> str | None:
    meta = pack.get("metadata")
    if not isinstance(meta, dict):
        return None
    pr = meta.get("pr")
    if not isinstance(pr, dict):
        return None
    repo = pr.get("repo")
    return repo.strip() if isinstance(repo, str) and repo.strip() else None


def _repo_visibility(repo: str, *, timeout_s: int = _GH_TIMEOUT_S) -> str | None:
    try:
        result = subprocess.run(
            ["gh", "repo", "view", repo, "--json", "visibility", "--jq", ".visibility"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    visibility = result.stdout.strip()
    return visibility or None


def _fetch_raw_url(html_url: str, filename: str, *, timeout_s: int) -> str | None:
    gist_id = _gist_id(html_url)
    if not gist_id:
        return None
    jq = f'.files["{filename}"].raw_url'
    try:
        result = subprocess.run(
            ["gh", "api", f"/gists/{gist_id}", "--jq", jq],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return _last_url(result.stdout)


def _fallback_raw_url(html_url: str, filename: str) -> str | None:
    parsed = urlparse(html_url)
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        return None
    owner, gist_id = parts[-2], parts[-1]
    return (
        f"https://gist.githubusercontent.com/{quote(owner)}/{quote(gist_id)}"
        f"/raw/{quote(filename)}"
    )


def _gist_id(html_url: str) -> str | None:
    parsed = urlparse(html_url)
    parts = [p for p in parsed.path.split("/") if p]
    return parts[-1] if parts else None


def _last_url(text: str) -> str | None:
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("http://") or line.startswith("https://"):
            return line
    return None

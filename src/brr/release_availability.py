"""Daemon-owned, fail-open observations of the latest brnrd release."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen

from packaging.version import InvalidVersion, Version

from . import __version__, account

PACKAGE_NAME = "brnrd"
PYPI_URL = f"https://pypi.org/pypi/{PACKAGE_NAME}/json"
# npm is the other headline install channel (`npm install -g brnrd`,
# packaging/npm/package.json) and, unlike PyPI, was never polled at all —
# an install from npm never learned it was behind. The registry's own
# metadata endpoint, no auth, same shape of identity check as PyPI below.
NPM_PACKAGE_NAME = "brnrd"
NPM_REGISTRY_URL = f"https://registry.npmjs.org/{NPM_PACKAGE_NAME}"
REPOSITORY_URL = "https://github.com/hugimuni-labs/brnrd"
# A published release's metadata is immutable, so the identity check matches
# every URL this project has ever published under, not only its current one.
# ``Gurio/brr`` is the pre-transfer name that 0.1.0 (2026-07-12) carries; PyPI
# cannot be edited in place, only superseded by a later release. Retire the
# alias once no supported release still names it.
REPOSITORY_URLS = (REPOSITORY_URL, "https://github.com/Gurio/brr")
CACHE_NAME = "release-availability.json"
SCHEMA = 1
DEFAULT_TTL_SECONDS = 24 * 60 * 60
REQUEST_TIMEOUT_SECONDS = 5.0
MAX_RESPONSE_BYTES = 64 * 1024

_refresh_lock = threading.Lock()
_refreshing = False


@dataclass(frozen=True)
class Availability:
    installed: str
    latest: str

    @property
    def available(self) -> bool:
        try:
            return Version(self.latest) > Version(self.installed)
        except InvalidVersion:
            return False

    def render(self) -> str | None:
        if self.available:
            return f"update available: {self.installed} → {self.latest}"
        return None


def cache_path(repo_root: Path) -> Path:
    """The machine-scoped daemon cache, outside every project mount."""
    del repo_root  # one installed brnrd version is shared across all projects
    return account._xdg_state_home() / account.DEFAULT_STATE_NAMESPACE / CACHE_NAME


def load(repo_root: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(cache_path(repo_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def observation(repo_root: Path, *, installed: str = __version__) -> Availability | None:
    payload = load(repo_root)
    latest = payload.get("latest") if payload else None
    if not isinstance(latest, str) or not latest.strip():
        return None
    return Availability(installed=installed, latest=latest.strip())


def npm_observation(repo_root: Path, *, installed: str = __version__) -> Availability | None:
    """The npm channel's own last-known-good, independent of PyPI's.

    Which channel actually installed this copy is not knowable from here —
    see ``news_lane.release_producer`` — so both channels are tracked
    side by side rather than one standing in for the other.
    """
    payload = load(repo_root)
    latest = payload.get("npm_latest") if payload else None
    if not isinstance(latest, str) or not latest.strip():
        return None
    return Availability(installed=installed, latest=latest.strip())


def _checked_at(payload: dict[str, Any] | None) -> float | None:
    value = payload.get("attempted_at", payload.get("checked_at")) if payload else None
    return float(value) if isinstance(value, (int, float)) else None


def _fresh(payload: dict[str, Any] | None, *, now: float, ttl: float) -> bool:
    checked_at = _checked_at(payload)
    return checked_at is not None and now - checked_at < ttl


def _write(repo_root: Path, payload: dict[str, Any]) -> None:
    path = cache_path(repo_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        pass


def _repository_url_matches(url: object) -> bool:
    """True when *url* names one of :data:`REPOSITORY_URLS`.

    npm's ``package.json`` convention prefixes the git remote with
    ``git+`` and suffixes it with ``.git`` (``git+https://…/brnrd.git``);
    PyPI's ``project_urls`` values never carry either. Strip both before
    comparing so one identity check serves both registries.
    """
    if not isinstance(url, str):
        return False
    normalized = url.removeprefix("git+").rstrip("/").removesuffix(".git")
    return normalized in {
        repo.rstrip("/").removesuffix(".git") for repo in REPOSITORY_URLS
    }


def _fetch_latest(*, timeout: float = REQUEST_TIMEOUT_SECONDS) -> str | None:
    request = Request(PYPI_URL, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed PyPI endpoint
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except (OSError, TimeoutError, ValueError):
        return None
    if len(raw) > MAX_RESPONSE_BYTES:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
        info = data["info"]
        if not isinstance(info, dict):
            return None
        project_urls = info.get("project_urls")
        urls = project_urls.values() if isinstance(project_urls, dict) else ()
        if info.get("name") != PACKAGE_NAME or not any(
            _repository_url_matches(url) for url in urls
        ):
            return None
        latest = info["version"]
        if not isinstance(latest, str):
            return None
        Version(latest)
    except (KeyError, TypeError, UnicodeDecodeError, ValueError, InvalidVersion):
        return None
    return str(latest)


def _fetch_latest_npm(*, timeout: float = REQUEST_TIMEOUT_SECONDS) -> str | None:
    """Same shape as :func:`_fetch_latest`, against npm's registry instead.

    npm's package document nests the identity check under ``repository``
    (a string or ``{"url": ...}``) rather than PyPI's ``project_urls``
    mapping — the one structural difference; the fail-open posture
    (any parse hiccup -> ``None``, never raise) is identical.
    """
    request = Request(NPM_REGISTRY_URL, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed npm endpoint
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except (OSError, TimeoutError, ValueError):
        return None
    if len(raw) > MAX_RESPONSE_BYTES:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict) or data.get("name") != NPM_PACKAGE_NAME:
            return None
        repository = data.get("repository")
        url = repository.get("url") if isinstance(repository, dict) else repository
        if not _repository_url_matches(url):
            return None
        dist_tags = data.get("dist-tags")
        latest = dist_tags.get("latest") if isinstance(dist_tags, dict) else None
        if not isinstance(latest, str):
            return None
        Version(latest)
    except (KeyError, TypeError, UnicodeDecodeError, ValueError, InvalidVersion):
        return None
    return str(latest)


def refresh_if_stale(
    repo_root: Path,
    *,
    now: float | None = None,
    ttl: float = DEFAULT_TTL_SECONDS,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> Availability | None:
    """Refresh at most daily; failed fetches leave the last good observation intact.

    One freshness clock governs both channels (PyPI's ``attempted_at`` also
    gates the npm fetch) — two independent daily timers would double the
    request rate for no benefit, since both checks are cheap and a daemon
    tick that is due for one is due for the other.
    """
    current_time = time.time() if now is None else now
    cached = load(repo_root)
    if not _fresh(cached, now=current_time, ttl=ttl):
        latest = _fetch_latest(timeout=timeout)
        npm_latest = _fetch_latest_npm(timeout=timeout)
        updated = dict(cached or {})
        updated.update({"schema": SCHEMA, "attempted_at": current_time})
        if latest is not None:
            updated.update({"checked_at": current_time, "latest": latest})
        if npm_latest is not None:
            updated.update({"npm_checked_at": current_time, "npm_latest": npm_latest})
        # A failed endpoint is still an attempt. Recording it prevents the
        # daemon's fast main loop from turning one outage into a request storm,
        # while retaining any last known-good ``latest`` observation.
        _write(repo_root, updated)
    return observation(repo_root)


def refresh_if_stale_async(
    repo_root: Path,
    *,
    on_complete: Callable[[Availability | None], None] | None = None,
) -> bool:
    """Refresh off the daemon loop; errors remain silent and retain prior state."""
    global _refreshing
    with _refresh_lock:
        if _refreshing:
            return False
        if _fresh(load(repo_root), now=time.time(), ttl=DEFAULT_TTL_SECONDS):
            if on_complete is not None:
                on_complete(observation(repo_root))
            return False
        _refreshing = True

    def work() -> None:
        global _refreshing
        try:
            result = refresh_if_stale(repo_root)
            if on_complete is not None:
                on_complete(result)
        finally:
            with _refresh_lock:
                _refreshing = False

    threading.Thread(target=work, name="brnrd-release-check", daemon=True).start()
    return True

"""Daemon-owned, fail-open observations of the latest brnrd release."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from packaging.version import InvalidVersion, Version

from . import __version__, account

REPOSITORY_URL = "https://github.com/hugimuni-labs/brnrd"
#: A release is a ``vX.Y.Z`` tag on the repository: what ``update = "stable"`` follows, read the same way
#: (``git ls-remote``, no account, no rate limit). PyPI was the source until the packaged install went.
RELEASES_URL = f"{REPOSITORY_URL}/releases"
CACHE_NAME = "release-availability.json"
SCHEMA = 1
DEFAULT_TTL_SECONDS = 24 * 60 * 60
REQUEST_TIMEOUT_SECONDS = 5.0

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


def _fetch_latest(*, timeout: float = REQUEST_TIMEOUT_SECONDS) -> str | None:
    """The newest release tag's version (``0.7.1``), or None on any failure: no git, offline, a timeout."""
    from .loom.runtime.update import newest_release
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--tags", REPOSITORY_URL, "refs/tags/v*"],
            capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode:
        return None
    found = newest_release(result.stdout)
    if found is None:
        return None
    latest = found[0].removeprefix("v")
    try:
        Version(latest)
    except InvalidVersion:
        return None
    return latest


def refresh_if_stale(
    repo_root: Path,
    *,
    now: float | None = None,
    ttl: float = DEFAULT_TTL_SECONDS,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> Availability | None:
    """Refresh at most daily; failed fetches leave the last good observation intact."""
    current_time = time.time() if now is None else now
    cached = load(repo_root)
    if not _fresh(cached, now=current_time, ttl=ttl):
        latest = _fetch_latest(timeout=timeout)
        updated = dict(cached or {})
        updated.update({"schema": SCHEMA, "attempted_at": current_time})
        if latest is not None:
            updated.update({"checked_at": current_time, "latest": latest})
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

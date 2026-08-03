"""Local open-issue-number cache — the network path behind the stale-open facet.

:mod:`brr.forge_state`'s "possible stale-opens" line (issue #1021) needs to know
which issue numbers are currently open before it can ask git whether any commit
reachable from ``main`` mentions one. That set does not exist locally anywhere
else in this codebase — the only sibling cache, :mod:`brr.forge_pr_cache`,
answers ``gh pr list``, and a pull request and an issue are different GitHub
objects sharing one number space. #1021's own "Shape" section assumed the set
was already in reach inside :func:`brr.forge_state.build_forge_state`; reading
that function whole showed it wasn't, so this module exists to hold exactly the
same architectural rule :mod:`brr.forge_pr_cache` already enforces: the ``gh``
call belongs to the daemon tick, and :mod:`brr.forge_state` may only ever
*read* the cache this module writes.

Truthfulness contract, identical in shape to :mod:`brr.forge_pr_cache` (the
``absent ≠ unknown ≠ none`` rule — see ``kb/log.md`` 2026-07-13):

- no cache file yet       → ``status="absent"``, ``numbers=None``  (unknown)
- last refresh failed     → ``status="error"``,  ``numbers=None``  (unknown,
                             with the last good rows kept if we had any)
- refresh succeeded       → ``numbers=[...]``                       (a real set,
                             possibly empty)

so a reader can never mistake "we have not looked" for "there are no open
issues" — the exact confusion #1000 and #770 filed against the PR-state cache's
own predecessor design, applied here before it could repeat.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import gitops

CACHE_NAME = "forge-issue-state.json"
SCHEMA = 1

# Issues move slower than PRs (no review/CI churn), but the same 5-minute
# window keeps this cache honest without paying a `gh` round-trip on every
# wake — consistent with :data:`brr.forge_pr_cache.DEFAULT_TTL_SECONDS`.
DEFAULT_TTL_SECONDS = 300.0
STALE_AFTER_SECONDS = DEFAULT_TTL_SECONDS

# This repo carried 190 open issues on 2026-08-03; headroom over today's count
# without paying for a second `gh` page.
FETCH_LIMIT = 500

_GH_TIMEOUT_SECONDS = 15.0

# One in-flight refresh at a time, process-wide — same reasoning as
# :mod:`brr.forge_pr_cache`.
_refresh_lock = threading.Lock()
_refreshing = False


def cache_path(repo_root: Path) -> Path:
    """The shared-runtime location of the cache for *repo_root*."""
    return gitops.shared_brr_dir(repo_root) / CACHE_NAME


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(raw: Any) -> float | None:
    """Epoch seconds for an ISO-8601 stamp, or ``None`` when unreadable."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def load(repo_root: Path) -> dict[str, Any] | None:
    """The cache as written, however old — or ``None`` when there is none."""
    try:
        data = json.loads(cache_path(repo_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read_state(
    repo_root: Path,
    *,
    now: float | None = None,
    stale_after: float = STALE_AFTER_SECONDS,
) -> dict[str, Any]:
    """Network-free read: the cache plus its own freshness verdict.

    Returns ``{"status", "fetched_at", "age_seconds", "numbers", "error"}``
    where ``status`` is one of ``absent`` | ``error`` | ``stale`` | ``fresh``
    and ``numbers`` is ``None`` whenever the state is unknown.
    """
    cached = load(repo_root)
    if cached is None:
        return {
            "status": "absent",
            "fetched_at": None,
            "age_seconds": None,
            "numbers": None,
            "error": None,
        }

    fetched_at = cached.get("fetched_at")
    fetched_epoch = parse_iso(fetched_at)
    age: float | None = None
    if fetched_epoch is not None:
        age = max(0.0, (time.time() if now is None else now) - fetched_epoch)

    error = cached.get("error")
    error = str(error).strip() if error else None
    rows = cached.get("numbers")
    numbers = (
        [n for n in rows if isinstance(n, int)] if isinstance(rows, list) else None
    )

    if numbers is None:
        status = "error" if error else "absent"
    elif error:
        # Rows kept from an earlier good fetch, behind a refresh that failed —
        # see the identical branch in forge_pr_cache.read_state for why "fresh"
        # is the one answer that is definitely wrong here.
        status = "error"
    elif age is None or age >= stale_after:
        status = "stale"
    else:
        status = "fresh"

    return {
        "status": status,
        "fetched_at": fetched_at if isinstance(fetched_at, str) else None,
        "age_seconds": age,
        "numbers": numbers,
        "error": error,
    }


def _repo_label(repo_root: Path) -> str | None:
    """``owner/repo`` from the git remote, when it can be read."""
    from . import forges

    try:
        remote = gitops.default_remote(repo_root) or "origin"
        url = gitops.remote_url(repo_root, remote)
    except Exception:  # noqa: BLE001 - a missing remote is not an error here
        return None
    if not url:
        return None
    parsed = forges.parse_remote(url)
    if not parsed:
        return None
    _host, owner, repo = parsed
    if owner and repo:
        return f"{owner}/{repo}"
    return None


def _write(repo_root: Path, payload: dict[str, Any]) -> Path | None:
    path = cache_path(repo_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        return None
    return path


def refresh(repo_root: Path, *, timeout: float = _GH_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Ask ``gh`` for this repo's open issue numbers and write the cache.

    Daemon-side only. Never raises: a failure writes an ``error`` cache
    (``numbers: null``) so readers keep saying *unknown* rather than silently
    reporting "no open issues". A failure also preserves the last good rows,
    kept with their true age attached.
    """
    label = _repo_label(repo_root)
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "fetched_at": _utc_now_iso(),
        "repo": label,
        "numbers": None,
        "error": None,
    }
    cmd = [
        "gh", "issue", "list",
        "--state", "open",
        "--limit", str(FETCH_LIMIT),
        "--json", "number",
    ]
    if label:
        cmd += ["--repo", label]
    try:
        result = subprocess.run(
            cmd,
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        payload["error"] = f"gh issue list timed out after {int(timeout)}s"
    except OSError as exc:
        payload["error"] = str(exc)
    else:
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            payload["error"] = detail.splitlines()[0] if detail else "gh issue list failed"
        else:
            try:
                rows = json.loads(result.stdout or "[]")
            except ValueError as exc:
                payload["error"] = f"invalid gh issue list output: {exc}"
            else:
                if not isinstance(rows, list):
                    payload["error"] = "invalid gh issue list payload"
                else:
                    numbers: list[int] = []
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        try:
                            numbers.append(int(row.get("number")))
                        except (TypeError, ValueError):
                            continue
                    payload["numbers"] = numbers

    if payload["numbers"] is None:
        # Keep the last good rows visible instead of dropping to nothing on one
        # bad refresh — but *aged honestly*, same rationale as forge_pr_cache.
        previous = load(repo_root)
        if isinstance(previous, dict) and isinstance(previous.get("numbers"), list):
            payload["numbers"] = previous["numbers"]
            payload["fetched_at"] = previous.get("fetched_at")
        payload["last_attempt_at"] = _utc_now_iso()
    _write(repo_root, payload)
    return payload


def refresh_if_stale(
    repo_root: Path,
    *,
    ttl: float = DEFAULT_TTL_SECONDS,
    now: float | None = None,
) -> bool:
    """Refresh when the cache is older than *ttl*. Returns whether it ran."""
    state = read_state(repo_root, now=now, stale_after=ttl)
    if state["status"] in ("fresh",):
        return False
    refresh(repo_root)
    return True


def refresh_if_stale_async(repo_root: Path, *, ttl: float = DEFAULT_TTL_SECONDS) -> bool:
    """Fire :func:`refresh_if_stale` on a daemon thread; never blocks the loop.

    Same reasoning as :func:`brr.forge_pr_cache.refresh_if_stale_async`: the
    daemon's scan tick is ~3s and a ``gh`` round-trip is ~1s worst case, so
    doing this inline would stall dispatch for an event queue waiting on
    nothing else. Returns whether a thread was started.
    """
    global _refreshing

    with _refresh_lock:
        if _refreshing:
            return False
        state = read_state(repo_root, stale_after=ttl)
        if state["status"] == "fresh":
            return False
        _refreshing = True

    def _work() -> None:
        global _refreshing
        try:
            refresh(repo_root)
        except Exception as exc:  # noqa: BLE001 - a cache refresh never kills the daemon
            print(f"[brnrd] forge issue-state refresh failed: {exc}")
        finally:
            with _refresh_lock:
                _refreshing = False

    threading.Thread(target=_work, name="forge-issue-cache", daemon=True).start()
    return True

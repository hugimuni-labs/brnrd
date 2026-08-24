"""Local workflow-run cache — deploy-lane health without touching the network at render time.

The wake's ``Forge state (local, network-free)`` block renders a prod line that
can say "N commits behind origin/main" for two entirely different reasons:

- **normal lag** — work merged, rollout not yet due.  Nothing to act on.
- **broken pipeline** — every deploy is failing; the gap widens with each
  merge while looking identical to the healthy case.

One predicate, two causes, and the reader watching the number rise cannot tell
them apart.  This module breaks the ambiguity by caching recent GitHub Actions
workflow-run results — warmed on the daemon's tick, read network-free on the
prompt-assembly path — so :mod:`brr.forge_state` can append a deploy-lane
clause to the prod line rather than silently inheriting the lie.

The design mirrors :mod:`brr.forge_pr_cache` exactly:

- the ``gh`` call lives here, and **only the daemon calls it** via
  :func:`refresh_if_stale_async` — never the prompt path;
- :func:`read_state` is the only function :mod:`brr.forge_state` ever calls —
  purely local, never a subprocess;
- the truthfulness contract is the same ``absent ≠ unknown ≠ none`` rule:

  - no cache file yet         → ``status="absent"``,   ``runs=None`` (unknown)
  - last refresh failed       → ``status="error"``,    ``runs=None`` (unknown,
                                last good rows kept if we had any)
  - refresh succeeded, empty  → ``runs=[]``                         (a real none)

so a reader can never mistake "we have not looked" for "the pipeline is fine".

Daemon wiring (not in this file — owned by daemon.py):
After the :func:`brr.forge_pr_cache.refresh_if_stale_async` call near
``daemon.py:15605``, add::

    forge_workflow_cache.refresh_if_stale_async(repo_root)

Optionally pass ``workflow_file=cfg.get("deploy.workflow_file")`` to scope the
cache to one workflow (e.g. ``"publish-container.yml"``).  Without it, the
first ``FETCH_LIMIT`` runs across all workflows are cached — still useful for
health classification, but noisier when a repo runs many unrelated workflows.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config as conf
from . import gitops

CACHE_NAME = "forge-workflow-state.json"

# Schema version — bump when cache shape changes incompatibly.
SCHEMA = 1

# Rides the daemon scan tick; deploy state moves on deploy timescales (minutes
# for a container rollout, hours for a normal working day), so 5 min is honest.
DEFAULT_TTL_SECONDS = 300.0
STALE_AFTER_SECONDS = DEFAULT_TTL_SECONDS

# How many recent runs to fetch.  15 gives a window deep enough to see a run
# of 12 consecutive failures (the 2026-08-23 incident) without burning tokens.
FETCH_LIMIT = 15

_GH_TIMEOUT_SECONDS = 20.0

# One in-flight refresh at a time — same guard as forge_pr_cache.
_refresh_lock = threading.Lock()
_refreshing = False


# ── file paths ──────────────────────────────────────────────────────────


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


# ── network-free read ────────────────────────────────────────────────────


def load(repo_root: Path) -> dict[str, Any] | None:
    """The cache as written, however old — or ``None`` when there is none.

    ``None`` means *absent* (nothing has ever refreshed it here), which
    callers must render as unknown, never as "no failures".
    """
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

    Returns ``{"status", "fetched_at", "age_seconds", "runs", "error"}``
    where ``status`` is one of ``absent`` | ``error`` | ``stale`` | ``fresh``
    and ``runs`` is ``None`` whenever the state is unknown.

    The caller (:func:`brr.forge_state._deploy_lane_facet`) must treat
    ``runs=None`` as *unknown* — not as "no failures" — so a missing or
    failed cache never silently reads as a healthy lane.
    """
    cached = load(repo_root)
    if cached is None:
        return {
            "status": "absent",
            "fetched_at": None,
            "age_seconds": None,
            "runs": None,
            "error": None,
        }

    fetched_at = cached.get("fetched_at")
    fetched_epoch = parse_iso(fetched_at)
    age: float | None = None
    if fetched_epoch is not None:
        age = max(0.0, (time.time() if now is None else now) - fetched_epoch)

    error = cached.get("error")
    error = str(error).strip() if error else None
    rows = cached.get("runs")
    runs = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else None

    if runs is None:
        status = "error" if error else "absent"
    elif error:
        # Rows kept from an earlier good fetch behind a refresh that failed:
        # may be perfectly current or hours out of date — "fresh" is wrong.
        status = "error"
    elif age is None or age >= stale_after:
        status = "stale"
    else:
        status = "fresh"

    return {
        "status": status,
        "fetched_at": fetched_at if isinstance(fetched_at, str) else None,
        "age_seconds": age,
        "runs": runs,
        "error": error,
    }


# ── gh-backed refresh (daemon-only) ─────────────────────────────────────


def _repo_label(repo_root: Path) -> str | None:
    """``owner/repo`` from the git remote, or ``None`` on any failure."""
    from . import forges

    try:
        remote = gitops.default_remote(repo_root) or "origin"
        url = gitops.remote_url(repo_root, remote)
    except Exception:  # noqa: BLE001
        return None
    if not url:
        return None
    parsed = forges.parse_remote(url)
    if not parsed:
        return None
    _host, owner, repo = parsed
    return f"{owner}/{repo}" if owner and repo else None


def _shape(row: dict[str, Any]) -> dict[str, Any] | None:
    """Reduce one ``gh run list`` row to the fields health classification needs."""
    status = str(row.get("status") or "").strip()
    if not status:
        return None
    run_id = row.get("databaseId")
    try:
        run_id = int(run_id)
    except (TypeError, ValueError):
        run_id = None
    return {
        "id": run_id,
        "status": status,
        "conclusion": str(row.get("conclusion") or "").strip() or None,
        "created_at": str(row.get("createdAt") or "").strip() or None,
        "head_branch": str(row.get("headBranch") or "").strip() or None,
        "name": str(row.get("name") or "").strip(),
    }


def _write(repo_root: Path, payload: dict[str, Any]) -> Path | None:
    path = cache_path(repo_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.rename(path)
    except OSError:
        return None
    return path


def refresh(
    repo_root: Path,
    *,
    workflow_file: str | None = None,
    limit: int = FETCH_LIMIT,
    timeout: float = _GH_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Refresh the workflow-run cache via ``gh``.

    ``workflow_file`` scopes the fetch to one workflow (e.g.
    ``"publish-container.yml"``); omit to fetch runs across all workflows.
    Only ``github`` remotes reach ``gh``; any other forge writes an error
    entry so the next :func:`read_state` call names the gap rather than
    silently returning absent.

    **Never call from the prompt path.** This is the only function in this
    module that touches the network.
    """
    from . import forges

    label = _repo_label(repo_root)
    if not label:
        return _error(repo_root, "could not resolve owner/repo from git remote")

    try:
        remote = gitops.default_remote(repo_root) or "origin"
        url = gitops.remote_url(repo_root, remote)
    except Exception:  # noqa: BLE001
        url = None

    if url:
        try:
            cfg = conf.load_config(repo_root)
        except Exception:  # noqa: BLE001
            cfg = {}
        match = forges.detect_forge(
            url,
            override_kind=cfg.get("forge.kind") or None,
            override_url_base=cfg.get("forge.url_base") or None,
        )
        if match and match.kind != "github":
            return _error(
                repo_root,
                f"workflow-run cache requires GitHub; detected forge is {match.kind!r}",
            )

    cmd = [
        "gh", "run", "list",
        "--repo", label,
        "--limit", str(limit),
        "--json", "databaseId,status,conclusion,createdAt,headBranch,name",
    ]
    if workflow_file:
        cmd += ["--workflow", workflow_file]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return _error(repo_root, f"gh run list timed out after {timeout:.0f}s")
    except FileNotFoundError:
        return _error(repo_root, "gh not found — GitHub CLI must be installed")

    if result.returncode != 0:
        msg = (result.stderr or "").strip() or f"exit code {result.returncode}"
        return _error(repo_root, msg)

    try:
        raw = json.loads(result.stdout)
    except ValueError as exc:
        return _error(repo_root, f"could not parse gh output: {exc}")

    if not isinstance(raw, list):
        return _error(repo_root, "unexpected gh output shape (expected JSON array)")

    runs = [shaped for row in raw if isinstance(row, dict) if (shaped := _shape(row))]
    payload = {
        "schema": SCHEMA,
        "fetched_at": _utc_now_iso(),
        "repo": label,
        "workflow_file": workflow_file,
        "runs": runs,
        "error": None,
    }
    _write(repo_root, payload)
    return {"ok": True, "count": len(runs)}


def _error(repo_root: Path, msg: str) -> dict[str, Any]:
    """Record a failed refresh attempt and return a summary."""
    cached = load(repo_root)
    prior_runs = cached.get("runs") if isinstance(cached, dict) else None
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "fetched_at": _utc_now_iso(),
        "repo": _repo_label(repo_root),
        "workflow_file": cached.get("workflow_file") if isinstance(cached, dict) else None,
        "runs": prior_runs,  # keep last good rows if available
        "error": msg,
    }
    _write(repo_root, payload)
    return {"ok": False, "error": msg}


def refresh_if_stale(
    repo_root: Path,
    *,
    ttl: float = DEFAULT_TTL_SECONDS,
    workflow_file: str | None = None,
) -> bool:
    """Refresh only when the cache is absent or older than *ttl* seconds.

    Returns ``True`` when a refresh was attempted (whether or not it succeeded).
    Safe to call from any synchronous context that already runs off the main
    thread (the daemon's tick handler).
    """
    state = read_state(repo_root, stale_after=ttl)
    if state["status"] in ("fresh",):
        return False
    refresh(repo_root, workflow_file=workflow_file)
    return True


def refresh_if_stale_async(
    repo_root: Path,
    *,
    ttl: float = DEFAULT_TTL_SECONDS,
    workflow_file: str | None = None,
) -> None:
    """Non-blocking version of :func:`refresh_if_stale` for the daemon tick.

    Mirrors :func:`brr.forge_pr_cache.refresh_if_stale_async` exactly: one
    in-flight refresh at a time, process-wide, on a daemon-owned thread.
    """
    global _refreshing  # noqa: PLW0603

    state = read_state(repo_root, stale_after=ttl)
    if state["status"] == "fresh":
        return

    def _do() -> None:
        global _refreshing  # noqa: PLW0603
        try:
            refresh(repo_root, workflow_file=workflow_file)
        finally:
            _refreshing = False

    with _refresh_lock:
        if _refreshing:
            return
        _refreshing = True

    t = threading.Thread(target=_do, daemon=True, name="forge-workflow-refresh")
    t.start()

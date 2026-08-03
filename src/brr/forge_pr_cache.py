"""Local PR-state cache — the *only* network path behind the Forge block.

The wake's ``Forge state (local, network-free)`` block listed branches and
nothing about their PRs, so nothing in a wake's boot context could contradict a
resident's remembered claim ("#373 still awaits the maintainer") — twice on
2026-07-13 that memory was wrong, once 3.5h after the PR had merged. The fix is
perception, not a warning: render PR state beside the branches so a stale claim
dies on contact at read time.

Which forces one hard constraint, and this module exists to hold it: the block's
name promises network-free assembly, so :mod:`brr.forge_state` and the prompt
renderers may only ever *read* a cache. The ``gh`` call lives here, and only the
**daemon** calls it, on its own scan cadence — never the prompt path.

Truthfulness contract (the ``absent ≠ unknown ≠ none`` rule this repo has been
bitten by repeatedly — see ``kb/log.md`` 2026-07-13, the credits panel):

- no cache file yet            → ``status="absent"``,      ``prs=None`` (unknown)
- last refresh failed          → ``status="error"``,       ``prs=None`` (unknown,
                                 with the last good rows kept if we had any)
- refresh succeeded, no PRs    → ``prs=[]``                             (a real none)
- remote is not GitHub         → ``status="unsupported"``, ``prs=None`` (known —
                                 and *no rows carried forward*: unlike ``error``,
                                 which keeps the last good read of the *same*
                                 repo, ``unsupported`` means the label now
                                 names a different forge than whatever the
                                 rows described, so nothing from before is
                                 still about this repo — #852)

so a reader can never mistake "we have not looked" for "there is nothing",
nor "we looked and it failed" for "there is nothing here to look at, ever".

The fourth case exists because ``gh pr list --repo OWNER/REPO`` resolves
``--repo`` against **github.com only** — it has no idea a parsed remote came
from GitLab/Bitbucket/Gitea. Without the gate, a non-GitHub remote either
burns a doomed subprocess every tick forever, or — worse — silently renders a
same-named GitHub repo's PRs as this repo's own state. ``refresh()`` decides
the forge *kind* before it ever shells out; only ``github`` reaches ``gh``.
``unsupported`` is not a permanent verdict, though — see
:func:`refresh_if_stale` — because its inputs (the git remote, a
``.brr/config`` override) are exactly what an operator edits to fix it.
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

CACHE_NAME = "forge-pr-state.json"
# Bumped 1 -> 2 for the ``unsupported`` / ``forge_kind`` fields (#852). Purely
# documentary: nothing in this module reads or validates ``schema`` on load,
# so an old schema-1 cache on disk (no ``unsupported`` key) still reads
# correctly under the new code — it just never matches the new branch in
# :func:`read_state` and falls through to the original absent/error/stale/
# fresh derivation, exactly as it did before this change.
SCHEMA = 2

# The cache rides the daemon's scan tick; PR state moves on human timescales
# (a merge, a review), so a 5-minute TTL keeps the block honest without
# spending a ``gh`` round-trip every few seconds.
DEFAULT_TTL_SECONDS = 300.0

# Beyond this, the rendered block calls the cache stale and labels its age.
STALE_AFTER_SECONDS = DEFAULT_TTL_SECONDS

# ``gh pr list --state all`` is number-descending; the newest slice covers every
# open PR plus the recently-resolved ones a live conversation might still claim.
FETCH_LIMIT = 60

_GH_TIMEOUT_SECONDS = 15.0

# One in-flight refresh at a time, process-wide: the daemon ticks every ~3s and
# a ``gh`` call takes ~1s, so without this a slow forge would stack threads.
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
    """The cache as written, however old — or ``None`` when there is none.

    ``None`` means *absent* (nothing has ever refreshed it here), which callers
    must render as unknown, never as "no PRs".
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

    Returns ``{"status", "fetched_at", "age_seconds", "prs", "error"}`` where
    ``status`` is one of ``absent`` | ``error`` | ``stale`` | ``fresh`` |
    ``unsupported``, and ``prs`` is ``None`` whenever the state is unknown.

    ``unsupported`` carries one extra key, ``forge_kind`` (the detected kind,
    or ``None`` when the host matched no known forge at all) — see
    :func:`refresh`. It never *renders* as ``stale``: a reader should never
    be told "a refresh is due" about a forge kind, which doesn't shift on
    its own. But its ``age_seconds`` is not purely informational — a caller
    deciding whether to re-derive (:func:`refresh_if_stale`) still reads it,
    because the two facts a kind is computed from (the git remote, a
    ``.brr/config`` override) are themselves editable, and this state must
    not be a silent permanent trap on an operator's fix.
    """
    cached = load(repo_root)
    if cached is None:
        return {
            "status": "absent",
            "fetched_at": None,
            "age_seconds": None,
            "prs": None,
            "error": None,
        }

    fetched_at = cached.get("fetched_at")
    fetched_epoch = parse_iso(fetched_at)
    age: float | None = None
    if fetched_epoch is not None:
        age = max(0.0, (time.time() if now is None else now) - fetched_epoch)

    if cached.get("unsupported"):
        return {
            "status": "unsupported",
            "fetched_at": fetched_at if isinstance(fetched_at, str) else None,
            "age_seconds": age,
            "prs": None,
            "error": None,
            "forge_kind": cached.get("forge_kind"),
        }

    error = cached.get("error")
    error = str(error).strip() if error else None
    rows = cached.get("prs")
    prs = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else None

    if prs is None:
        status = "error" if error else "absent"
    elif error:
        # Rows we kept from an earlier good fetch, behind a refresh that failed.
        # They may be perfectly current or hours out of date — we cannot know,
        # and "fresh" is the one answer that is definitely wrong.  This is the
        # rule this whole module exists to enforce, turned on itself.
        status = "error"
    elif age is None or age >= stale_after:
        status = "stale"
    else:
        status = "fresh"

    return {
        "status": status,
        "fetched_at": fetched_at if isinstance(fetched_at, str) else None,
        "age_seconds": age,
        "prs": prs,
        "error": error,
    }


def _forge_kind_and_label(repo_root: Path) -> tuple[str | None, str | None]:
    """``(kind, "owner/repo")`` from the git remote, honoring ``[forge]`` overrides.

    One function owns both reads (not a label helper plus a separate kind
    helper) so a missing/unreadable remote is a single early return instead
    of two independent git subprocess calls agreeing to fail.

    ``label`` is ``None`` only when the remote itself can't be read or
    doesn't parse into an ``owner/repo`` shape at all — genuinely nothing to
    go on, same as before this change, and left to :func:`refresh`'s old
    cwd-inference fallback rather than folded into ``unsupported``.

    ``kind`` is ``None`` in two shapes callers must **not** tell apart:
    forge-unresolvable host with a valid label (a self-hosted forge with no
    ``.brr/config`` override) reads exactly like an explicitly non-GitHub
    kind (``gitlab`` / ``bitbucket`` / ``gitea``) to :func:`refresh` — both
    are a labeled remote ``gh --repo OWNER/REPO`` would silently resolve
    against github.com instead, which is the hazard #852 gates on. Only
    ``kind == "github"`` is safe to query.
    """
    from . import forges

    try:
        remote = gitops.default_remote(repo_root) or "origin"
        url = gitops.remote_url(repo_root, remote)
    except Exception:  # noqa: BLE001 - a missing remote is not an error here
        return None, None
    if not url:
        return None, None
    parsed = forges.parse_remote(url)
    if not parsed:
        return None, None
    _host, owner, repo = parsed
    label = f"{owner}/{repo}" if owner and repo else None
    if label is None:
        return None, None

    try:
        cfg = conf.load_config(repo_root)
    except Exception:  # noqa: BLE001 - an unreadable config is not fatal here
        cfg = {}
    match = forges.detect_forge(
        url,
        override_kind=cfg.get("forge.kind") or None,
        override_url_base=cfg.get("forge.url_base") or None,
    )
    return (match.kind if match else None), label


def _shape(row: dict[str, Any]) -> dict[str, Any] | None:
    """Reduce one ``gh`` row to the fields the Forge block renders."""
    try:
        number = int(row.get("number"))
    except (TypeError, ValueError):
        return None
    branch = str(row.get("headRefName") or "").strip()
    if not branch:
        return None
    return {
        "number": number,
        "title": str(row.get("title") or "").strip(),
        "state": str(row.get("state") or "").strip().upper() or "UNKNOWN",
        "branch": branch,
        "url": str(row.get("url") or "").strip(),
        "draft": bool(row.get("isDraft")),
        "merged_at": str(row.get("mergedAt") or "").strip() or None,
        "closed_at": str(row.get("closedAt") or "").strip() or None,
    }


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
    """Ask ``gh`` for this repo's PRs and write the cache. Daemon-side only.

    Never raises: a failure writes an ``error`` cache (``prs: null``) so readers
    keep saying *unknown* rather than silently reporting "no PRs". A failure
    also preserves the last good rows, which are still worth seeing with their
    true age attached.

    Gated on the forge *kind* before any subprocess runs (#852): ``gh --repo
    OWNER/REPO`` resolves against github.com only, so a labeled remote whose
    kind isn't ``github`` never reaches ``gh`` at all — it writes an
    ``unsupported`` cache and returns. Without the gate this either burns a
    doomed subprocess every tick forever, or — worse, when
    ``github.com/OWNER/REPO`` happens to exist — silently renders a foreign
    repo's PRs as this repo's own state.

    This branch itself is cheap (one local git remote read, one config read,
    zero subprocesses), so :func:`refresh_if_stale` calls it again on the
    normal TTL cadence even while ``unsupported`` — re-deriving the kind
    each time rather than trusting a cached verdict forever, because the
    verdict's own inputs are user-editable (a ``git remote set-url``, a
    ``.brr/config`` ``forge.kind`` fix).
    """
    kind, label = _forge_kind_and_label(repo_root)
    if label is not None and kind != "github":
        payload: dict[str, Any] = {
            "schema": SCHEMA,
            "fetched_at": _utc_now_iso(),
            "repo": label,
            "prs": None,
            "error": None,
            "unsupported": True,
            "forge_kind": kind,
        }
        _write(repo_root, payload)
        return payload

    payload = {
        "schema": SCHEMA,
        "fetched_at": _utc_now_iso(),
        "repo": label,
        "prs": None,
        "error": None,
    }
    cmd = [
        "gh", "pr", "list",
        "--state", "all",
        "--limit", str(FETCH_LIMIT),
        "--json", "number,title,state,headRefName,mergedAt,closedAt,url,isDraft",
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
        payload["error"] = f"gh pr list timed out after {int(timeout)}s"
    except OSError as exc:
        payload["error"] = str(exc)
    else:
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            payload["error"] = detail.splitlines()[0] if detail else "gh pr list failed"
        else:
            try:
                rows = json.loads(result.stdout or "[]")
            except ValueError as exc:
                payload["error"] = f"invalid gh pr list output: {exc}"
            else:
                if not isinstance(rows, list):
                    payload["error"] = "invalid gh pr list payload"
                else:
                    shaped = [_shape(row) for row in rows if isinstance(row, dict)]
                    payload["prs"] = [row for row in shaped if row]

    if payload["prs"] is None:
        # Keep the last good rows visible instead of dropping to nothing on one
        # bad refresh — but *aged honestly*.  `fetched_at` describes the DATA,
        # not the attempt: carrying the rows forward under a fresh timestamp is
        # how an offline `gh` makes hour-old PR state read as current, which is
        # precisely the failure-indistinguishable-from-success this cache was
        # built to end.  The attempt gets its own field.
        previous = load(repo_root)
        if isinstance(previous, dict) and isinstance(previous.get("prs"), list):
            payload["prs"] = previous["prs"]
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
    """Refresh when the cache is older than *ttl*. Returns whether it ran.

    ``unsupported`` never *renders* as stale (see :func:`read_state`), but it
    is not a permanent skip either: its two inputs — the git remote and any
    ``.brr/config`` ``[forge]`` override — are exactly what an operator edits
    to *fix* an unrecognised host, and ``forge.kind = github`` is the
    documented remedy for that case. Caching the verdict forever would mean
    the correct fix silently does nothing until someone finds and deletes a
    cache file whose existence nothing advertises. So an ``unsupported``
    cache is re-derived on the same TTL cadence as any other status —
    cheaply: :func:`refresh` never reaches ``gh`` for a non-``github`` kind,
    it only re-reads local git + config, so re-checking costs nothing beyond
    what a normal stale refresh already pays (#852).
    """
    state = read_state(repo_root, now=now, stale_after=ttl)
    if state["status"] == "fresh":
        return False
    age = state["age_seconds"]
    if state["status"] == "unsupported" and age is not None and age < ttl:
        return False
    refresh(repo_root)
    return True


def refresh_if_stale_async(repo_root: Path, *, ttl: float = DEFAULT_TTL_SECONDS) -> bool:
    """Fire :func:`refresh_if_stale` on a daemon thread; never blocks the loop.

    The daemon's scan tick is ~3s and a ``gh`` round-trip is ~1s (worst case the
    full timeout); doing it inline would stall dispatch for an event queue that
    is waiting on nothing else. Returns whether a thread was started.
    """
    global _refreshing

    with _refresh_lock:
        if _refreshing:
            return False
        state = read_state(repo_root, stale_after=ttl)
        if state["status"] == "fresh":
            return False
        age = state["age_seconds"]
        if state["status"] == "unsupported" and age is not None and age < ttl:
            return False
        _refreshing = True

    def _work() -> None:
        global _refreshing
        try:
            refresh(repo_root)
        except Exception as exc:  # noqa: BLE001 - a cache refresh never kills the daemon
            print(f"[brnrd] forge PR-state refresh failed: {exc}")
        finally:
            with _refresh_lock:
                _refreshing = False

    threading.Thread(target=_work, name="forge-pr-cache", daemon=True).start()
    return True

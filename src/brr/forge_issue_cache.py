"""Local closed-issue cache — the network-free half of ``pitfall-cites-closed-issue``.

:mod:`brr.notes_preflight`'s ``check_pitfall_issue_refs`` wants to know, at
wake time and without a network call, whether a ``#NNNN`` a pitfall entry
cites is a *closed* issue — because a pitfall is a claim with a date on it,
and the ticket number it cites is the cheapest available falsifier (#1298:
an entry stayed in the present tense for weeks after the issue it named had
shipped a fix, and got quoted to the maintainer as a live constraint).

The design mirrors :mod:`brr.forge_pr_cache` / :mod:`brr.forge_workflow_cache`:
the ``gh`` call lives here, only the **daemon** calls it (:func:`refresh_if_stale_async`,
off the loop thread, TTL-guarded), and every reader — the preflight check —
only ever calls :func:`read_state`, which is a pure JSON load plus a
freshness verdict. Same truthfulness contract as its siblings:

- no cache file yet             → ``status="absent"``,  ``issues=None`` (unknown)
- last refresh failed           → ``status="error"``,   ``issues=None`` (unknown,
                                    last good rows kept if we had any)
- refresh ran, nothing to check → ``issues={}``                        (a real none)

so the preflight can never mistake "we have not looked" for "there is
nothing here" — the one guard that actually matters for this feature: an
**unknown** issue state must never render as a **closed** one.

**Why targeted, not bulk.** :mod:`brr.forge_pr_cache` fetches the newest
``FETCH_LIMIT`` PRs by number and lets an old one age out of the window —
fine there, because a resident mostly cares whether a *recent* branch's PR
merged. Pitfall citations skew the opposite way: the store's oldest entries
(this repo's earliest, #128-#175 vintage) are exactly the ones most likely
to have drifted, and a "most recent N issues" fetch would never see them.
So this cache fetches **exactly the numbers currently cited** across the
repo's resident pitfall stores, one ``gh issue view`` per number, and keeps
each number's last-known state until that number is re-checked — no
recency window to fall out of. The cost is one subprocess per cited number
instead of one bulk list call; :data:`MAX_NUMBERS_PER_REFRESH` bounds the
worst case, and a store citing more distinct numbers than that silently
undercounts (recorded in the payload's ``truncated`` field, not hidden)
rather than turning a background refresh into an unbounded stall. A batched
``gh api graphql`` query could fetch many numbers in one round trip and
would remove that cap entirely — left for a future pass if the per-number
cost ever actually bites; nothing here forecloses it, see the cache shape.

A cited number that ``gh issue view`` cannot resolve (most commonly: it
names a **pull request**, not an issue — GitHub shares one numbering space
between the two) is simply left out of the result. The caller checks
:mod:`brr.forge_pr_cache` for that shape; a number that is neither is left
unknown, never assumed closed.
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

CACHE_NAME = "forge-issue-state.json"

#: Schema version — bump when the cache shape changes incompatibly.
SCHEMA = 1

# Issue triage moves on human timescales (a comment, a close), far slower
# than PR review or a deploy — no reason to poll `gh` every few minutes.
DEFAULT_TTL_SECONDS = 1800.0
STALE_AFTER_SECONDS = DEFAULT_TTL_SECONDS

# One `gh issue view` per cited number (see module docstring). A pathological
# store citing hundreds of tickets would otherwise turn a background refresh
# into a multi-minute stall — this bounds it. Numbers beyond the cap (lowest
# first, so long-standing citations win over anything newly added) are
# skipped for the round and recorded in `truncated`, not silently dropped.
MAX_NUMBERS_PER_REFRESH = 100

_GH_TIMEOUT_SECONDS = 10.0

# One in-flight refresh at a time, process-wide — same guard as forge_pr_cache.
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
    callers must render as unknown, never as "no closed issues".
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

    Returns ``{"status", "fetched_at", "age_seconds", "issues", "error"}``
    where ``status`` is one of ``absent`` | ``error`` | ``stale`` | ``fresh``
    and ``issues`` — a ``{"<number>": {...}}`` map, keyed by the issue number
    as a string (JSON object keys are always strings) — is ``None`` whenever
    the state is unknown. A caller must treat a **missing key** the same way
    it treats ``issues is None``: not-yet-checked is not "open".
    """
    cached = load(repo_root)
    if cached is None:
        return {
            "status": "absent",
            "fetched_at": None,
            "age_seconds": None,
            "issues": None,
            "error": None,
        }

    fetched_at = cached.get("fetched_at")
    fetched_epoch = parse_iso(fetched_at)
    age: float | None = None
    if fetched_epoch is not None:
        age = max(0.0, (time.time() if now is None else now) - fetched_epoch)

    error = cached.get("error")
    error = str(error).strip() if error else None
    rows = cached.get("issues")
    issues = rows if isinstance(rows, dict) else None

    if issues is None:
        status = "error" if error else "absent"
    elif error:
        # Rows kept from an earlier good fetch behind a refresh that failed:
        # may be current or hours out of date — "fresh" is the one answer
        # that is definitely wrong.
        status = "error"
    elif age is None or age >= stale_after:
        status = "stale"
    else:
        status = "fresh"

    return {
        "status": status,
        "fetched_at": fetched_at if isinstance(fetched_at, str) else None,
        "age_seconds": age,
        "issues": issues,
        "error": error,
    }


# ── which numbers need checking ──────────────────────────────────────────


def cited_numbers(repo_root: Path, cfg: dict[str, Any] | None = None) -> set[int]:
    """Every issue-or-ambiguous ref cited across this repo's resident pitfall stores.

    Reads :func:`brr.notes_preflight.cited_issue_numbers` over every
    :func:`brr.dominion.resident_dominion_candidates` — the same resolution
    :func:`brr.notes_preflight.scan_scoped` walks — so this cache always
    answers about the same stores the wake-time check reads. Best-effort:
    an unresolvable dominion or config contributes nothing rather than
    failing the whole refresh.
    """
    from . import dominion as dominion_mod
    from . import notes_preflight

    if cfg is None:
        try:
            cfg = conf.load_config(repo_root)
        except Exception:  # noqa: BLE001 - an unreadable config is not fatal here
            cfg = {}

    numbers: set[int] = set()
    try:
        for candidate in dominion_mod.resident_dominion_candidates(repo_root, cfg):
            if not candidate.path.is_dir():
                continue
            numbers |= notes_preflight.cited_issue_numbers(candidate.path)
    except Exception:  # noqa: BLE001 - a cache refresh never kills the daemon
        pass
    return numbers


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


def _fetch_one(label: str, number: int, timeout: float) -> dict[str, Any] | None:
    """One issue's shaped state, or ``None`` (not an issue, or the call failed).

    Deliberately does not distinguish "definitely not an issue" (most often:
    the number names a PR) from a transient failure (auth, network, rate
    limit) — both leave the number's entry untouched in the merged cache
    rather than guessing. The next refresh tries again; a wrongly-cited or
    since-renumbered ticket just never gets an entry, which the preflight
    already treats as unknown.
    """
    try:
        result = subprocess.run(
            [
                "gh", "issue", "view", str(number),
                "--repo", label,
                "--json", "number,title,state,closedAt,url",
            ],
            capture_output=True, text=True, check=False, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    try:
        row = json.loads(result.stdout or "{}")
    except ValueError:
        return None
    if not isinstance(row, dict):
        return None
    try:
        num = int(row.get("number"))
    except (TypeError, ValueError):
        return None
    return {
        "number": num,
        "title": str(row.get("title") or "").strip(),
        "state": str(row.get("state") or "").strip().upper() or "UNKNOWN",
        "closed_at": str(row.get("closedAt") or "").strip() or None,
        "url": str(row.get("url") or "").strip(),
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


def refresh(
    repo_root: Path,
    numbers: "set[int] | frozenset[int]",
    *,
    timeout: float = _GH_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Look up *numbers* via ``gh issue view`` and write the merged cache.

    Daemon-side only — the one network path this module owns. Previously
    cached numbers not in *numbers* this round are kept as-is: a number a
    store stopped citing doesn't need to be forgotten, and only re-checking
    what's currently cited keeps a refresh's cost proportional to the live
    store, not its whole history.

    An empty *numbers* is legal and cheap — it writes a fresh, empty
    ``issues: {}`` stamp (no ``gh`` reached at all) so :func:`read_state`
    reports a real, current "nothing to check" instead of leaving the cache
    permanently ``absent``, which would otherwise make
    :func:`refresh_if_stale_async` re-attempt every daemon tick forever.
    """
    from . import forges

    cached = load(repo_root) or {}
    prior = cached.get("issues")
    issues: dict[str, Any] = dict(prior) if isinstance(prior, dict) else {}

    wanted = sorted({int(n) for n in numbers if int(n) > 0})
    if not wanted:
        payload = {
            "schema": SCHEMA,
            "fetched_at": _utc_now_iso(),
            "repo": _repo_label(repo_root),
            "issues": issues,
            "error": None,
            "truncated": 0,
        }
        _write(repo_root, payload)
        return payload

    label = _repo_label(repo_root)
    if not label:
        payload = {
            "schema": SCHEMA,
            "fetched_at": _utc_now_iso(),
            "repo": None,
            "issues": issues,
            "error": "could not resolve owner/repo from git remote",
            "truncated": 0,
        }
        _write(repo_root, payload)
        return payload

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
            payload = {
                "schema": SCHEMA,
                "fetched_at": _utc_now_iso(),
                "repo": label,
                "issues": issues,
                "error": (
                    f"issue-state cache requires GitHub; detected forge is {match.kind!r}"
                ),
                "truncated": 0,
            }
            _write(repo_root, payload)
            return payload

    truncated = max(0, len(wanted) - MAX_NUMBERS_PER_REFRESH)
    wanted = wanted[:MAX_NUMBERS_PER_REFRESH]

    for number in wanted:
        row = _fetch_one(label, number, timeout)
        if row is not None:
            issues[str(number)] = row

    payload = {
        "schema": SCHEMA,
        "fetched_at": _utc_now_iso(),
        "repo": label,
        "issues": issues,
        "error": None,
        "truncated": truncated,
    }
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
    if state["status"] == "fresh":
        return False
    refresh(repo_root, cited_numbers(repo_root))
    return True


def refresh_if_stale_async(repo_root: Path, *, ttl: float = DEFAULT_TTL_SECONDS) -> bool:
    """Fire :func:`refresh_if_stale` on a daemon thread; never blocks the loop.

    Mirrors :func:`brr.forge_pr_cache.refresh_if_stale_async` exactly: one
    in-flight refresh at a time, process-wide, so a busy tick cadence cannot
    stack threads behind a slow (or, here, deliberately serial-per-number)
    ``gh`` round trip. Returns whether a thread was started.
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
            refresh(repo_root, cited_numbers(repo_root))
        except Exception as exc:  # noqa: BLE001 - a cache refresh never kills the daemon
            print(f"[brnrd] forge issue-state refresh failed: {exc}")
        finally:
            with _refresh_lock:
                _refreshing = False

    threading.Thread(target=_work, name="forge-issue-cache", daemon=True).start()
    return True

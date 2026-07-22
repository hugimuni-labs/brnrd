"""Codex session-rollout quota collector — the level-facet source for the Codex Shell.

Codex's interactive ``/status`` panel (5h + weekly subscription limits, plan
type, context window) is **not** a CLI subcommand and cannot be invoked
head-less. But the numbers it shows are written to disk continuously: every
``token_count`` event in a session's *rollout file*
(``$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl``) carries a ``rate_limits``
block with a ``primary`` and a ``secondary`` window, each as ``used_percent`` +
``window_minutes`` + ``resets_at``, alongside ``plan_type`` and
``model_context_window``.

**Which window sits in which slot is not fixed.** It was long enough
(primary = the 5h window, secondary = the weekly one) that brr encoded the
assumption positionally — and 2026-07-13 a Plus account reported ``primary``
carrying the *weekly* window (``windowDurationMins: 10080``) with ``secondary:
null``, which made the dashboard label a weekly number "5h window" and report
the weekly window as unavailable. ``window_minutes`` is the only thing OpenAI
actually asserts about a window's identity, so every reader classifies off the
duration, never the slot.

So brr reads the *same data ``/status`` would print* by tailing the active run's
rollout file — no ``/status`` call, no extra subscription credits, no API key.
This is the Codex half of brr's per-Shell level collection, and it inverts the
asymmetry recorded earlier: it is Codex, not Claude, whose subscription quota
brr can read head-less. (Claude's ``statusLine`` is a TUI footer that never
fires under ``claude --print``; Claude's head-less result JSON carries
spend/context but no quota windows. See ``kb/design-resident-boundary.md`` §8.)

The parse is **defensive** (the rollout schema is OpenAI's, undocumented and
free to change): every field is optional, an unrecognized shape yields a
snapshot with no level slots (facets stay ``absent``), never an exception.

**Which rollout is "the active run's"?** :func:`load_levels` prefers exact
``thread_id`` correlation (:func:`_rollout_for_thread`) whenever the caller
proved one — ``runner.py`` captures it from ``codex exec --json``'s
``thread.started`` event, the same id the rollout filename embeds
(``rollout-…-<thread_id>.jsonl``). With no id it falls back to
:func:`_latest_rollout_fallback`'s newest-mtime guess, explicitly named as a
compatibility path rather than correlation — the assumption that "newest is
the active run" only holds when exactly one Codex Shell is alive at a time,
which is not guaranteed the moment a worker spawn or a sibling wake runs a
second one concurrently (issue #195).

Returns the shared *levels* snapshot shape
(``{"quota"|"context_window": {"summary": ...}}``), so the daemon folds it into
:func:`brr.facets.build` through the identical ``levels=`` seam.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# Shells whose binary exposes a session-rollout quota collector.
_CODEX_FLAVOURS = {"codex"}

# Level slots this collector can actually populate (per-slot honesty: Codex is a
# subscription Shell with no dollar-spend gauge, so ``spend`` stays
# unimplemented here — never a fabricated estimate).
COLLECTED_SLOTS: frozenset[str] = frozenset({"quota", "context_window"})


def supported(runner_name: str | None) -> bool:
    """True when *runner_name*'s Shell exposes the rollout quota collector."""
    if not runner_name:
        return False
    slug = str(runner_name).strip().lower()
    return any(slug == f or slug.startswith(f) for f in _CODEX_FLAVOURS)


def sessions_root(env: dict[str, str] | None = None) -> Path:
    """The Codex sessions directory, honouring ``CODEX_HOME`` (default ``~/.codex``)."""
    env = env if env is not None else dict(os.environ)
    home = env.get("CODEX_HOME")
    base = Path(home) if home else Path(env.get("HOME", str(Path.home()))) / ".codex"
    return base / "sessions"


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_pct(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def _fmt_reset(epoch: Any) -> str | None:
    secs = _num(epoch)
    if secs is None or secs <= 1_000_000_000:
        return None
    try:
        dt = datetime.fromtimestamp(secs, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return "resets " + dt.strftime("%H:%MZ")


def _window_label(window_minutes: Any) -> str:
    mins = _num(window_minutes)
    if mins is None:
        return "quota"
    mins = int(mins)
    if mins % 1440 == 0:
        return f"{mins // 1440}d"
    if mins % 60 == 0:
        return f"{mins // 60}h"
    return f"{mins}m"


def _window_summary(label_default: str, window: Any) -> str | None:
    """One rate-limit window → 'LABEL NN% left (resets HH:MMZ)'.

    ``used_percent`` is consumption, so headroom = ``100 - used``.
    """
    if not isinstance(window, dict):
        return None
    used = _num(window.get("used_percent"))
    if used is None:
        return None
    remaining = max(0.0, 100.0 - used)
    label = _window_label(window.get("window_minutes")) or label_default
    text = f"{label} {_fmt_pct(remaining)}% left"
    reset = _fmt_reset(window.get("resets_at"))
    return f"{text} ({reset})" if reset else text


def _fmt_event_timestamp(raw: Any) -> str | None:
    """Reformat a rollout event's own ``timestamp`` (``"...T..Z"``, millisecond
    precision, e.g. ``"2026-07-08T20:18:25.753Z"``) to the collector-shared
    ``updated_at`` format (``%Y-%m-%dT%H:%M:%SZ``). Returns ``None`` for
    anything unparseable so the caller can fall back to wall-clock time
    rather than raise — this module never raises on malformed input."""
    if not isinstance(raw, str) or not raw:
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_token_count(payload: dict[str, Any], event_timestamp: Any = None) -> dict[str, Any]:
    """Normalize one ``token_count`` event payload into the levels snapshot shape.

    ``payload`` is the ``token_count`` event's ``payload`` dict, carrying
    ``rate_limits`` and ``info`` (with ``model_context_window`` and
    ``last_token_usage``). Returns ``{"quota"|"context_window": {...}, "source",
    "updated_at", "plan_type"}`` with only the slots it could prove.

    ``event_timestamp`` is the rollout record's own top-level ``timestamp``
    (when the event actually happened), not the scrape time — live-caught
    2026-07-09 (a user screenshot showed the 5h window rendered ``critical,
    resets in now`` while the weekly window read a healthy 81%): this
    function used to stamp ``updated_at`` with wall-clock "now" on *every*
    call, including calls made long after the underlying run ended (brr
    re-reads the newest rollout file's last ``token_count`` event on every
    daemon poll tick, active run or not). That made a quota snapshot that
    was actually hours stale look freshly-scraped to
    ``activity_dashboard.py::_quota_views``'s staleness check — the exact
    "lying usage panel" bug already fixed for Claude (2026-07-07), silently
    reproduced for Codex because this collector was assumed exempt ("no
    comparable idle-gap") when it in fact has one: no rollout write happens
    at all between runs. Falls back to wall-clock time only when the event
    itself carries no parseable timestamp.
    """
    payload = payload if isinstance(payload, dict) else {}
    levels: dict[str, Any] = {
        "source": "codex session rollout",
        "updated_at": (
            _fmt_event_timestamp(event_timestamp)
            or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        ),
    }

    rate = payload.get("rate_limits")
    if isinstance(rate, dict):
        parts = [
            s for s in (
                _window_summary("5h", rate.get("primary")),
                _window_summary("weekly", rate.get("secondary")),
            ) if s
        ]
        if parts:
            primary = rate.get("primary") if isinstance(rate.get("primary"), dict) else {}
            secondary = rate.get("secondary") if isinstance(rate.get("secondary"), dict) else {}
            primary_used = _num(primary.get("used_percent"))
            secondary_used = _num(secondary.get("used_percent"))
            levels["quota"] = {
                "summary": "; ".join(parts),
                "primary_used_percent": primary_used,
                # Weekly window's used_percent, previously discarded past the
                # rendered summary string — quota pacing needs both windows'
                # numbers, not just the 5h one (kb/design-director-loop.md §B1).
                "secondary_used_percent": secondary_used,
                "primary_remaining_percent": (
                    100.0 - primary_used if primary_used is not None else None
                ),
                "secondary_remaining_percent": (
                    100.0 - secondary_used if secondary_used is not None else None
                ),
                # Raw reset epochs, previously discarded past `_fmt_reset()`'s
                # display string (2026-07-06) — the dashboard window-track
                # visual's time-remaining axis needs a machine-parseable
                # instant, not just "resets HH:MMZ" text.
                "primary_resets_at": _num(primary.get("resets_at")),
                "secondary_resets_at": _num(secondary.get("resets_at")),
                # Each window's *duration*, carried through structurally rather
                # than only rendered into `summary` above. The slot a window
                # arrives in is not its identity: observed live 2026-07-13
                # (codex-cli 0.144.1, Plus account) the app-server returned the
                # **weekly** window as `primary` (`windowDurationMins: 10080`)
                # with `secondary: null` — so a reader that assumes
                # primary=5h/secondary=weekly labels the weekly number "5h" and
                # reports weekly as unknown. `window_minutes` is what OpenAI
                # actually states about a window; everything downstream should
                # classify off it (see `gates/cloud.py::_codex_quota_windows`).
                "primary_window_minutes": _num(primary.get("window_minutes")),
                "secondary_window_minutes": _num(secondary.get("window_minutes")),
            }
        plan = rate.get("plan_type")
        if isinstance(plan, str) and plan.strip():
            levels["plan_type"] = plan.strip()

    info = payload.get("info")
    if isinstance(info, dict):
        window = _num(info.get("model_context_window"))
        last = info.get("last_token_usage")
        used = _num(last.get("input_tokens")) if isinstance(last, dict) else None
        total = _num(last.get("total_tokens")) if isinstance(last, dict) else None
        output = (
            max(0.0, total - used)
            if total is not None and used is not None else None
        )
        # ``input_tokens`` of the last request ≈ current context occupancy (the
        # full context is re-sent each turn). ``total_token_usage`` is cumulative
        # across the whole session and routinely exceeds the window, so it is the
        # wrong figure for headroom. Marked (est) — the exact occupancy Codex's
        # TUI uses is not documented.
        if window and window > 0 and used is not None:
            remaining = max(0.0, min(100.0, 100.0 * (1.0 - used / window)))
            levels["context_window"] = {
                "summary": f"{_fmt_pct(remaining)}% context left (est)",
                "remaining_percentage": remaining,
            }
        token_fields: dict[str, Any] = {}
        if used is not None:
            token_fields["input_tokens"] = int(used)
        if output is not None:
            token_fields["output_tokens"] = int(output)
        if window and window > 0 and used is not None:
            token_fields["context_window_used_percent"] = round(
                max(0.0, min(100.0, 100.0 * used / window)), 6
            )
        if token_fields:
            levels["tokens"] = token_fields

    return levels


def _latest_rollout_fallback(root: Path) -> Path | None:
    """The most recently modified ``rollout-*.jsonl`` under *root*, or None.

    **Compatibility fallback only — not correlation.** Until issue #195 this
    newest-mtime guess was the *only* way brr picked a rollout, on the theory
    that brr is single-flight per dominion so "newest" is "the active run"
    (the same heuristic ``ccusage`` uses). That theory breaks the moment two
    Codex Shells are alive at once — a worker spawn, a sibling wake, a stray
    interactive session — since this function has no way to tell whose
    rollout it just picked. :func:`load_levels` now prefers exact
    ``thread_id`` correlation (:func:`_rollout_for_thread`) whenever the
    caller has proven one; this stays reachable only when no id was proven
    (the Shell isn't codex, the invocation predates ``--json`` correlation,
    or a pinned ``runner_cmd`` override skipped it), and only ever as an
    explicitly-named fallback, never presented as correlation.
    """
    try:
        candidates = root.rglob("rollout-*.jsonl")
    except OSError:
        return None
    newest: Path | None = None
    newest_mtime = -1.0
    for path in candidates:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime > newest_mtime:
            newest, newest_mtime = path, mtime
    return newest


# codex thread ids are UUIDs in practice, but this only needs to be safe, not
# exactly right: any charset outside this is rejected rather than trusted, so
# a malformed/hostile id degrades to "no id available" (the mtime fallback)
# instead of ever reaching a filesystem glob. Blocks path separators and
# ``..`` traversal by construction (neither character is in the allowed set).
_SAFE_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _safe_thread_id(thread_id: Any) -> str | None:
    """*thread_id*, validated for safe use in a filename glob, or None."""
    if not isinstance(thread_id, str):
        return None
    candidate = thread_id.strip()
    if not candidate or not _SAFE_THREAD_ID_RE.match(candidate):
        return None
    return candidate


def _rollout_for_thread(root: Path, thread_id: str) -> Path | None:
    """The rollout file whose name ends in *thread_id*, or None.

    Exact correlation (issue #195): a rollout is named
    ``rollout-<timestamp>-<thread_id>.jsonl``, and *thread_id* here has
    already passed :func:`_safe_thread_id`, so this is a plain suffix glob —
    never a substring/prefix scan that could cross-match a sibling run's id.
    Multi-run safety depends on this being exact: a run must never read
    another Codex run's context-window/token snapshot, and "starts with the
    same characters" is not "is the same thread." More than one match should
    never happen (ids are unique per thread); newest mtime breaks that tie
    defensively rather than raising.
    """
    try:
        candidates = list(root.rglob(f"rollout-*-{thread_id}.jsonl"))
    except OSError:
        return None
    if not candidates:
        return None
    newest: Path | None = None
    newest_mtime = -1.0
    for path in candidates:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime > newest_mtime:
            newest, newest_mtime = path, mtime
    return newest


def _last_token_count(path: Path) -> tuple[dict[str, Any], Any] | None:
    """The ``(payload, timestamp)`` of the last ``token_count`` event in
    rollout *path*, or None. ``timestamp`` is the record's own top-level
    field (the event's real time), not a scrape time.

    Scans line by line (a rollout is JSONL) and keeps the last match; the file is
    small enough that a full pass is cheap, and the *last* event carries the
    freshest quota.
    """
    last: tuple[dict[str, Any], Any] | None = None
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or '"token_count"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = record.get("payload") if isinstance(record, dict) else None
                if isinstance(payload, dict) and payload.get("type") == "token_count":
                    last = (payload, record.get("timestamp"))
    except OSError:
        return None
    return last


def load_levels(
    env: dict[str, str] | None = None, *, thread_id: str | None = None,
) -> dict[str, Any] | None:
    """Read a Codex run's quota from its rollout file, or None.

    *thread_id*, when given and safe (:func:`_safe_thread_id`), resolves the
    rollout by exact filename correlation (:func:`_rollout_for_thread`) — the
    fix for issue #195: brr used to always take the newest-mtime rollout
    under the sessions root, which silently reads a *sibling* Codex run's
    quota/context snapshot the moment more than one is alive at once. With
    no usable *thread_id* (absent, malformed, or the caller never proved
    one — e.g. a pre-run read before any Codex invocation exists yet) this
    falls back to :func:`_latest_rollout_fallback`, the same newest-mtime
    guess as before, explicitly named so nothing downstream can mistake it
    for correlation.

    A *thread_id* that IS given but matches no rollout file is **not**
    retried against the mtime fallback: guessing there would risk exactly
    the sibling-read this parameter exists to prevent, so that case returns
    None (honest absence) same as "no rollout at all."

    Extracts the last ``token_count`` event and normalizes it either way.
    Returns None when no rollout, no ``token_count`` event, or nothing
    parseable is found — never raises.
    """
    root = sessions_root(env)
    if not root.is_dir():
        return None
    safe_id = _safe_thread_id(thread_id)
    rollout = _rollout_for_thread(root, safe_id) if safe_id else _latest_rollout_fallback(root)
    if rollout is None:
        return None
    found = _last_token_count(rollout)
    if found is None:
        return None
    payload, event_timestamp = found
    levels = parse_token_count(payload, event_timestamp)
    # Only worth returning if at least one level slot was proven.
    if not any(key in levels for key in COLLECTED_SLOTS):
        return None
    return levels


def collected_slots(runner_name: str | None) -> Iterable[str]:
    """Which level slots this Shell has a wired collector for (per-slot honesty)."""
    return COLLECTED_SLOTS if supported(runner_name) else frozenset()



# --- Trailing burn: retired 2026-07-19 ---------------------------------------
#
# `recent_burn()` and its rollout-scanning helpers (`_sample_window`,
# `_rollout_burn_samples`, `_event_epoch`) lived here, deriving a burn rate by
# tailing every `rollout-*.jsonl` inside the horizon. The measurement survives
# unchanged — same window filtering, same minimum-span refusal, same clamp and
# `sustainable` rule — but it now reads `brr.usage_samples`, the shell-agnostic
# store fed by the level reads brr already performs every heartbeat.
#
# Moved rather than duplicated, deliberately. The rollout scan was *not* kept as
# a seeder for the store: a fact stored twice is a fact that will eventually
# disagree with itself, and this repo has shipped two production bugs from
# exactly that shape (`kb/log.md` 2026-07-19, "One run, one truth"). The cost is
# a short blind period after deploy while samples accumulate, which is the
# honest failure — the reading already returns None on thin evidence and every
# renderer already draws that absence as absence.
#
# The gain: burn is no longer Codex-only. Claude's `/usage` scrape yields a
# point reading with no on-disk history to recover, so no rollout-shaped trick
# could ever have measured it; storing samples as they are read works for both.

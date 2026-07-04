"""Codex session-rollout quota collector — the level-facet source for the Codex Shell.

Codex's interactive ``/status`` panel (5h + weekly subscription limits, plan
type, context window) is **not** a CLI subcommand and cannot be invoked
head-less. But the numbers it shows are written to disk continuously: every
``token_count`` event in a session's *rollout file*
(``$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl``) carries a ``rate_limits``
block with ``primary`` (the 5h window) and ``secondary`` (the weekly window),
each as ``used_percent`` + ``window_minutes`` + ``resets_at``, alongside
``plan_type`` and ``model_context_window``.

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

Returns the shared *levels* snapshot shape
(``{"quota"|"context_window": {"summary": ...}}``), so the daemon folds it into
:func:`brr.facets.build` through the identical ``levels=`` seam.
"""

from __future__ import annotations

import json
import os
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


def parse_token_count(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize one ``token_count`` event payload into the levels snapshot shape.

    ``payload`` is the ``token_count`` event's ``payload`` dict, carrying
    ``rate_limits`` and ``info`` (with ``model_context_window`` and
    ``last_token_usage``). Returns ``{"quota"|"context_window": {...}, "source",
    "updated_at", "plan_type"}`` with only the slots it could prove.
    """
    payload = payload if isinstance(payload, dict) else {}
    levels: dict[str, Any] = {
        "source": "codex session rollout",
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
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
            }
        plan = rate.get("plan_type")
        if isinstance(plan, str) and plan.strip():
            levels["plan_type"] = plan.strip()

    info = payload.get("info")
    if isinstance(info, dict):
        window = _num(info.get("model_context_window"))
        last = info.get("last_token_usage")
        used = _num(last.get("input_tokens")) if isinstance(last, dict) else None
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

    return levels


def _latest_rollout(root: Path) -> Path | None:
    """The most recently modified ``rollout-*.jsonl`` under *root*, or None.

    brr is single-flight per dominion, so the newest rollout is the active run's
    session — the same heuristic ``ccusage`` uses.
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


def _last_token_count(path: Path) -> dict[str, Any] | None:
    """The payload of the last ``token_count`` event in rollout *path*, or None.

    Scans line by line (a rollout is JSONL) and keeps the last match; the file is
    small enough that a full pass is cheap, and the *last* event carries the
    freshest quota.
    """
    last: dict[str, Any] | None = None
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
                    last = payload
    except OSError:
        return None
    return last


def load_levels(env: dict[str, str] | None = None) -> dict[str, Any] | None:
    """Read the active Codex run's quota from its rollout file, or None.

    Finds the newest rollout under the sessions root, extracts the last
    ``token_count`` event, and normalizes it. Returns None when no rollout, no
    ``token_count`` event, or nothing parseable is found — never raises.
    """
    root = sessions_root(env)
    if not root.is_dir():
        return None
    rollout = _latest_rollout(root)
    if rollout is None:
        return None
    payload = _last_token_count(rollout)
    if payload is None:
        return None
    levels = parse_token_count(payload)
    # Only worth returning if at least one level slot was proven.
    if not any(key in levels for key in COLLECTED_SLOTS):
        return None
    return levels


def collected_slots(runner_name: str | None) -> Iterable[str]:
    """Which level slots this Shell has a wired collector for (per-slot honesty)."""
    return COLLECTED_SLOTS if supported(runner_name) else frozenset()

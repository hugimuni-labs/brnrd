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


# --- Trailing burn ----------------------------------------------------------
#
# Why this exists: on 2026-07-12 (~15:18→20:52 CEST, live Plus account,
# codex-cli 0.144.1 unchanged since 07-11) OpenAI stopped publishing the
# 300-minute window entirely — `secondary: null`, `primary` carrying the weekly
# window. Verified at the source: `account/rateLimits/read` reports exactly one
# window today, and every rollout since agrees. The 5h *bar* on the dashboard is
# therefore unrecoverable — there is no 5h number left to draw.
#
# But the question it answered is still live: **am I burning too fast right
# now?** A weekly window alone can't say — 53% left is calm at a slow drip and a
# fire alarm at 6 points an hour. brr already tails the rollouts that carry every
# `used_percent` reading with its own timestamp, so the short-horizon reading is
# derivable from data on disk: how much of the *reported* window this account
# actually burned over the trailing few hours, and where that rate lands it.
#
# Derived, and labelled as derived. Never a fabricated window.

_BURN_HORIZON_HOURS = 5.0
# Two samples five minutes apart can "prove" a 200%/day burn. Below this span the
# rate is noise, and a noisy projection is worse than no projection.
_BURN_MIN_SPAN_MINUTES = 30.0


def _sample_window(rate: Any) -> tuple[float, float, float] | None:
    """A rate-limit block → ``(used_percent, window_minutes, resets_at)``.

    Reads whichever slot actually carries a window (see the module docstring:
    the slot is not the identity), preferring the longest window reported — the
    subscription ceiling that matters is the one you can't wait out.
    """
    if not isinstance(rate, dict):
        return None
    best: tuple[float, float, float] | None = None
    for slot in ("primary", "secondary"):
        window = rate.get(slot)
        if not isinstance(window, dict):
            continue
        used = _num(window.get("used_percent"))
        minutes = _num(window.get("window_minutes"))
        resets = _num(window.get("resets_at"))
        if used is None or minutes is None or resets is None:
            continue
        if best is None or minutes > best[1]:
            best = (used, minutes, resets)
    return best


def _rollout_burn_samples(path: Path) -> list[tuple[float, float, float, float]]:
    """Every ``(event_epoch, used_percent, window_minutes, resets_at)`` in a
    rollout, oldest first. Malformed lines are skipped, never raised on."""
    samples: list[tuple[float, float, float, float]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if '"rate_limits"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                payload = record.get("payload")
                if not isinstance(payload, dict) or payload.get("type") != "token_count":
                    continue
                window = _sample_window(payload.get("rate_limits"))
                if window is None:
                    continue
                stamped = _event_epoch(record.get("timestamp"))
                if stamped is None:
                    continue
                samples.append((stamped, *window))
    except OSError:
        return []
    return samples


def _event_epoch(raw: Any) -> float | None:
    """A rollout record's own ISO-8601 ``timestamp`` → epoch seconds."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        moment = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def recent_burn(
    hours: float = _BURN_HORIZON_HOURS,
    env: dict[str, str] | None = None,
    now: float | None = None,
) -> dict[str, Any] | None:
    """How fast the account is spending its quota, over the trailing *hours*.

    Scans the rollouts touched inside the horizon, keeps the samples belonging
    to the window that is *currently* live (same duration, same ``resets_at`` —
    a spent reset credit restarts the window and its ``used_percent`` drops back
    to near zero, which would otherwise read as a negative burn), and measures
    the climb from the oldest surviving sample to the newest.

    Returns None when the evidence is too thin to mean anything: no rollouts, a
    single sample, a window that only started minutes ago, or a span below
    :data:`_BURN_MIN_SPAN_MINUTES`. Projecting from noise is the failure this
    guards — the point of the reading is to replace a bar that stopped being
    true, not to replace it with a guess.
    """
    root = sessions_root(env)
    if not root.is_dir():
        return None
    stamp = time.time() if now is None else now
    horizon_start = stamp - hours * 3600.0

    samples: list[tuple[float, float, float, float]] = []
    try:
        candidates = list(root.rglob("rollout-*.jsonl"))
    except OSError:
        return None
    for path in candidates:
        try:
            # A rollout whose last write predates the horizon can hold no sample
            # inside it — skip the read entirely (this runs every heartbeat).
            if path.stat().st_mtime < horizon_start:
                continue
        except OSError:
            continue
        samples.extend(_rollout_burn_samples(path))
    if not samples:
        return None

    samples.sort(key=lambda s: s[0])
    _, _, live_minutes, live_resets = samples[-1]
    live = [
        s
        for s in samples
        if s[2] == live_minutes and s[3] == live_resets and s[0] >= horizon_start
    ]
    if len(live) < 2:
        return None

    first, last = live[0], live[-1]
    span_minutes = (last[0] - first[0]) / 60.0
    if span_minutes < _BURN_MIN_SPAN_MINUTES:
        return None

    # `used_percent` can dip inside a live window (OpenAI's own accounting is not
    # strictly monotonic); clamp at zero rather than reporting a negative burn.
    burned = max(0.0, last[1] - first[1])
    remaining = max(0.0, 100.0 - last[1])
    per_minute = burned / span_minutes
    projected_remaining = max(0.0, remaining - per_minute * hours * 60.0)
    exhausts_at: float | None = None
    if per_minute > 0 and remaining > 0:
        exhausts_at = last[0] + (remaining / per_minute) * 60.0
    # A burn that runs out the clock before it runs out the quota is a burn you
    # can keep up: the window resets first, and saying "exhausts in 40h" about a
    # window that resets in 30h would be a scare, not a fact.
    sustainable = exhausts_at is None or exhausts_at >= last[3]
    return {
        "window_minutes": live_minutes,
        "hours": hours,
        "span_minutes": round(span_minutes, 1),
        "samples": len(live),
        "from_remaining_percent": round(max(0.0, 100.0 - first[1]), 1),
        "to_remaining_percent": round(remaining, 1),
        "burned_percent": round(burned, 1),
        "projected_remaining_percent": round(projected_remaining, 1),
        "exhausts_at": exhausts_at,
        "sustainable": sustainable,
        "source": "codex session rollout",
    }

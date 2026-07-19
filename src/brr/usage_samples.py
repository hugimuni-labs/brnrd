"""Shell-agnostic quota sample store — the one series brr measures burn from.

**Why this module exists.** Trailing burn ("am I burning too fast *right
now?*") was born Codex-only, because Codex was the one Shell whose
``used_percent`` readings arrive pre-timestamped on disk: every ``token_count``
event in a session rollout carries one, so a series could be *recovered* after
the fact by tailing files brr already read. Claude has no such artifact — its
levels come from a PTY scrape of the ``/usage`` screen, which yields a **point**
reading and forgets it. So the one honest instrument on the cost surface was
invisible on the Shell doing the large majority of the spending.

The asymmetry was never in the data, though — it was in the *storage*. brr
reads quota levels for both Shells, repeatedly, on the heartbeat cadence
(:mod:`brr.daemon`), at publish time (:mod:`brr.gates.cloud`) and at run
boundaries (:mod:`brr.run_ledger`). Nothing kept them. This module keeps them:
an append-only JSONL log of ``(observed_at, shell, window, used_percent)``,
written as a **side effect of reads that already happen**. No poller, no extra
probe, no spent quota. Give a point reading a memory and it becomes a series;
a series is all trailing burn ever needed.

**One store, not two.** :func:`recent_burn` here replaces the rollout-scanning
``codex_status.recent_burn`` outright rather than sitting beside it. A fact
stored twice is a fact that will disagree with itself, and this repo has
already shipped two production bugs from exactly that (``kb/log.md``
2026-07-19, "One run, one truth"). The rollout scan is *not* retained as a
seeder: after deploy there is a short blind period while samples accumulate,
which is fine and honest — the reading already returns ``None`` on thin
evidence, and every renderer already draws that absence as absence.

Every I/O path here is failure-tolerant by construction. A usage sample is
telemetry about the work, never the work; losing one must never fail a run, so
:func:`record` swallows everything and :func:`recent_burn` degrades to ``None``.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# The sample log, written into the account's shared ``.brr`` dir. Quota is
# **account** state, not run state (the same reasoning that puts Codex's probe
# cache there): one series every reader shares, warm across runs and daemon
# restarts, rather than a per-run file that fragments the history the burn
# measurement depends on.
LOG_NAME = "usage-samples.jsonl"

# Trailing-burn horizon. Inherited unchanged from `codex_status.recent_burn`.
BURN_HORIZON_HOURS = 5.0

# Two samples five minutes apart can "prove" a 200%/day burn. Below this span
# the rate is noise, and a noisy projection is worse than no projection.
BURN_MIN_SPAN_MINUTES = 30.0

# Records are kept for twice the horizon, then pruned on write: enough history
# that a full-horizon measurement is always available, bounded enough that the
# file never becomes a thing anyone has to think about.
_RETENTION_HOURS = BURN_HORIZON_HOURS * 2

# Hard ceiling on retained records, in case a pathological caller samples far
# faster than the throttle expects. Newest are kept.
_MAX_RECORDS = 20_000

# At most one record per (shell, window) per this interval. The heartbeat runs
# every 30s and the publish paths add their own reads; without a throttle the
# log would carry several identical rows a minute for no added resolution.
# Time-based, never value-based: skipping a record because the *value* was
# unchanged would silently truncate the measured span.
_MIN_SAMPLE_INTERVAL_SECONDS = 60.0

# Claude's `/usage` buckets carry no explicit duration — the screen names them
# rather than measuring them — so the durations are supplied here. These are
# the window lengths the buckets *are*, and burn only ever compares a window to
# itself, so the mapping needs to be stable rather than externally verified.
_CLAUDE_WINDOW_MINUTES = {"session": 300.0, "week": 10080.0}


def log_path(state_dir: Path | str) -> Path:
    """The sample log inside *state_dir* (the account's shared ``.brr``)."""
    return Path(state_dir) / LOG_NAME


def _num(value: Any) -> float | None:
    """Coerce to float, refusing bools and anything unparseable."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def windows_from_levels(levels: Any, shell: str | None) -> list[dict[str, float]]:
    """A levels snapshot → the quota windows it proves, for *shell*.

    Both collectors already normalize into the shared levels shape, but they
    put the windows in different places: Codex nests
    ``quota.{primary,secondary}_*`` (slot-positional, so duration is read from
    ``*_window_minutes`` and never inferred from the slot — see
    :mod:`brr.codex_status`), while Claude flattens ``session_*`` / ``week_*``
    onto the top level with the duration implied by the bucket name.

    Only windows carrying **all three** of ``used_percent``, duration and
    ``resets_at`` are returned: burn identifies a window by duration + reset
    instant, so a window missing either cannot be compared to itself and is
    worth nothing to this store.
    """
    if not isinstance(levels, dict):
        return []
    slug = str(shell or "").strip().lower()
    out: list[dict[str, float]] = []

    if slug.startswith("codex"):
        quota = levels.get("quota")
        if not isinstance(quota, dict):
            return []
        for slot in ("primary", "secondary"):
            used = _num(quota.get(f"{slot}_used_percent"))
            minutes = _num(quota.get(f"{slot}_window_minutes"))
            resets = _num(quota.get(f"{slot}_resets_at"))
            if used is None or minutes is None or resets is None:
                continue
            out.append(
                {"used_percent": used, "window_minutes": minutes, "resets_at": resets}
            )
        return out

    if slug.startswith("claude"):
        for bucket, minutes in _CLAUDE_WINDOW_MINUTES.items():
            used = _num(levels.get(f"{bucket}_used_percentage"))
            resets = _num(levels.get(f"{bucket}_resets_at"))
            if used is None or resets is None:
                continue
            out.append(
                {"used_percent": used, "window_minutes": minutes, "resets_at": resets}
            )
        return out

    return []


def observed_at(levels: Any, now: float) -> float:
    """When the reading in *levels* was actually taken, falling back to *now*.

    The collectors stamp every snapshot with the capture time of the underlying
    scrape/probe (``updated_at``), and brr reads those snapshots from cache as
    well as fresh — two of the three level call sites pass ``refresh=False`` and
    legitimately get a reading taken some time ago.

    Stamping a cached reading with wall-clock "now" is precisely the bug this
    codebase already paid for once on the display side (see
    :func:`brr.codex_status.parse_token_count`: a stale rollout rendered as
    freshly-scraped, the "lying usage panel" of 2026-07-07/07-09). On the
    *measurement* side the same mistake is worse than cosmetic: it invents a
    flat segment where nothing was observed, dragging the measured burn toward
    zero and quietly under-reporting a sprint. Dating the sample by the reading
    means a cache re-read is an exact duplicate of the sample already stored
    rather than new evidence — which is the truth of it.
    """
    raw = levels.get("updated_at") if isinstance(levels, dict) else None
    if isinstance(raw, str) and raw.strip():
        text = raw.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return now
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        stamped = moment.timestamp()
        # A clock-skewed or malformed future stamp is not evidence about the
        # past; fall back rather than seed the series with a sample that will
        # outlive every real one.
        if stamped <= now:
            return stamped
    return now


def _shell_slug(shell: str | None) -> str | None:
    """Normalize a runner name to the Shell family the samples are keyed by."""
    slug = str(shell or "").strip().lower()
    if slug.startswith("codex"):
        return "codex"
    if slug.startswith("claude"):
        return "claude"
    return None


def _read_records(path: Path) -> list[dict[str, Any]]:
    """Every parseable record in the log, oldest first. Never raises."""
    records: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    records.append(record)
    except OSError:
        return []
    records.sort(key=lambda r: _num(r.get("at")) or 0.0)
    return records


def _recent_enough(records: Iterable[dict[str, Any]], cutoff: float) -> list[dict[str, Any]]:
    kept = []
    for record in records:
        stamped = _num(record.get("at"))
        if stamped is not None and stamped >= cutoff:
            kept.append(record)
    return kept


def record(
    state_dir: Path | str | None,
    shell: str | None,
    levels: Any,
    now: float | None = None,
) -> int:
    """Append this observation's windows to the log. Returns records written.

    A side effect of a read that already happened — call it wherever levels are
    obtained, never on a cadence of its own. Throttled per
    ``(shell, window)`` to :data:`_MIN_SAMPLE_INTERVAL_SECONDS`, pruned to
    :data:`_RETENTION_HOURS` on write, and **silent on every failure**: a
    missing directory, an unwritable log, a corrupt line, a levels dict of an
    unexpected shape all yield ``0`` rather than an exception. Telemetry about
    the work is never worth failing the work.
    """
    try:
        if state_dir is None:
            return 0
        slug = _shell_slug(shell)
        if slug is None:
            return 0
        windows = windows_from_levels(levels, slug)
        if not windows:
            return 0

        wall = time.time() if now is None else float(now)
        # Dated by the reading, not by the moment brr happened to look at it.
        stamp = observed_at(levels, wall)
        path = log_path(state_dir)
        existing = _read_records(path)

        # Throttle: one record per window per interval. Keyed by the window's
        # identity (duration + reset instant), so a genuine reset — which mints
        # a new `resets_at` — is always recorded immediately rather than being
        # swallowed as a duplicate of the window it replaced.
        newest: dict[tuple[float, float], float] = {}
        for item in existing:
            if item.get("shell") != slug:
                continue
            minutes = _num(item.get("window_minutes"))
            resets = _num(item.get("resets_at"))
            at = _num(item.get("at"))
            if minutes is None or resets is None or at is None:
                continue
            key = (minutes, resets)
            if key not in newest or at > newest[key]:
                newest[key] = at

        fresh = []
        for window in windows:
            key = (window["window_minutes"], window["resets_at"])
            last = newest.get(key)
            if last is not None and stamp - last < _MIN_SAMPLE_INTERVAL_SECONDS:
                continue
            fresh.append({"at": stamp, "shell": slug, **window})
        if not fresh:
            return 0

        # Retention is measured against the wall clock, not the reading's own
        # date: how long brr keeps history is a real-time question.
        combined = _recent_enough(existing, wall - _RETENTION_HOURS * 3600.0) + fresh
        combined.sort(key=lambda r: _num(r.get("at")) or 0.0)
        if len(combined) > _MAX_RECORDS:
            combined = combined[-_MAX_RECORDS:]

        path.parent.mkdir(parents=True, exist_ok=True)
        # Rewrite through a temp file: pruning means this is never a pure
        # append, and a partial write would corrupt the series rather than just
        # lose the newest row.
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            for item in combined:
                handle.write(json.dumps(item) + "\n")
        os.replace(tmp, path)
        return len(fresh)
    except Exception:
        return 0


def recent_burn(
    state_dir: Path | str | None,
    shell: str | None,
    hours: float = BURN_HORIZON_HOURS,
    now: float | None = None,
) -> dict[str, Any] | None:
    """How fast *shell*'s account is spending its quota, over the trailing *hours*.

    Reads the sample store, keeps the samples belonging to the window that is
    *currently* live (same duration, same ``resets_at`` — a spent reset credit
    restarts the window and its ``used_percent`` drops back to near zero, which
    would otherwise read as a negative burn), and measures the climb from the
    oldest surviving sample to the newest.

    Where several windows are on record, the **longest** is measured: the
    subscription ceiling that matters is the one you can't wait out.

    Returns None when the evidence is too thin to mean anything: no samples, a
    single sample, a window that only started minutes ago, or a span below
    :data:`BURN_MIN_SPAN_MINUTES`. Projecting from noise is the failure this
    guards — the point of the reading is to replace a bar that stopped being
    true, not to replace it with a guess.
    """
    slug = _shell_slug(shell)
    if state_dir is None or slug is None:
        return None
    stamp = time.time() if now is None else float(now)
    horizon_start = stamp - hours * 3600.0

    samples: list[tuple[float, float, float, float]] = []
    for item in _read_records(log_path(state_dir)):
        if item.get("shell") != slug:
            continue
        at = _num(item.get("at"))
        used = _num(item.get("used_percent"))
        minutes = _num(item.get("window_minutes"))
        resets = _num(item.get("resets_at"))
        if at is None or used is None or minutes is None or resets is None:
            continue
        samples.append((at, used, minutes, resets))
    if not samples:
        return None

    samples.sort(key=lambda s: s[0])
    in_horizon = [s for s in samples if s[0] >= horizon_start]
    if not in_horizon:
        return None

    # The live window: longest duration on record inside the horizon, and for
    # that duration the newest `resets_at` (an older reset instant is a window
    # that has since rolled over).
    live_minutes = max(s[2] for s in in_horizon)
    live_resets = max(s[3] for s in in_horizon if s[2] == live_minutes)
    live = [s for s in in_horizon if s[2] == live_minutes and s[3] == live_resets]
    if len(live) < 2:
        return None

    first, last = live[0], live[-1]
    span_minutes = (last[0] - first[0]) / 60.0
    if span_minutes < BURN_MIN_SPAN_MINUTES:
        return None

    # `used_percent` can dip inside a live window (the providers' own accounting
    # is not strictly monotonic); clamp at zero rather than reporting a negative
    # burn.
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
        "source": "brr usage samples",
    }

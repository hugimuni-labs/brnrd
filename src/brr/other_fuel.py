"""The other Shells' fuel — the boundary line's view past the seat's own Shell.

The seat's boundary chip (``q S93↻4h18m·W75↻4d22h``) is the quota of the Shell
it runs on. A resident that plans work across Shells (a codex strand while it
sits on claude) also needs the *other* pool's headroom at its fingertips — two
codex strands died of the codex session wall while the line read only claude
(maintainer, 2026-09-21). This module is the pure half: a level snapshot in, a
row out; the daemon owns the cache-only read (``daemon._other_shells_fuel``).

A row is ``{shell, binding_remaining_pct, resets_in, read_at, stale, buckets}``.
Honesty rules, same as the seat's chip:

- a reading older than :data:`STALE_AFTER_SECONDS` for its Shell renders
  ``codex ?`` — never a stale number without a mark;
- a Shell with no numeric reading yields no row at all (omitted, not zeroed).
"""

from __future__ import annotations

import calendar
import time
from collections.abc import Mapping
from typing import Any

#: Stale threshold per Shell: twice the cadence the cloud publisher refreshes
#: that Shell's cached reading on (``gates.cloud_publisher._CLAUDE_/_CODEX_
#: QUOTA_PUBLISH_MAX_AGE_SECONDS`` = 240s / 120s) — one missed refresh is
#: tolerated, two is a reading nobody is keeping alive.
STALE_AFTER_SECONDS: dict[str, float] = {"claude": 480.0, "codex": 240.0}
_DEFAULT_STALE_AFTER = 480.0

_CODEX_LETTERS = {300: "S", 10080: "W"}


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _epoch(raw: Any) -> float | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return float(calendar.timegm(time.strptime(raw, "%Y-%m-%dT%H:%M:%SZ")))
    except (ValueError, OverflowError):
        return None


def duration(seconds: float) -> str:
    """``7440`` → ``2h04m`` · ``90000`` → ``1d1h`` · under a minute → ``1m``
    (the seat chip's own format, ``hooks._relative_reset``)."""
    remaining = int(seconds)
    if remaining <= 0:
        return "0m"
    days, rest = divmod(remaining, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days}d{hours}h"
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{max(minutes, 1)}m"


def _buckets(levels: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Account-wide buckets as ``{letter, remaining_pct, resets_at}``.

    Per-model Claude week buckets are left out, as in
    :func:`runner_quota.binding_quota_remaining_pct` with no model: they bind
    only the Core that spends them, and this row is about the Shell's pool.
    """
    quota = levels.get("quota")
    if not isinstance(quota, Mapping):
        return []
    out: list[dict[str, Any]] = []
    buckets = quota.get("buckets")
    if isinstance(buckets, Mapping):
        for key, letter in (("session", "S"), ("week", "W")):
            bucket = buckets.get(key)
            pct = _num(bucket.get("remaining_percentage")) if isinstance(bucket, Mapping) else None
            if pct is None:
                continue
            resets = _num(quota.get(f"{key}_resets_at"))
            if resets is None:
                resets = _num(levels.get(f"{key}_resets_at"))
            out.append({"letter": letter, "remaining_pct": pct, "resets_at": resets})
    for slot, fallback in (("primary", "S"), ("secondary", "W")):
        pct = _num(quota.get(f"{slot}_remaining_percent"))
        if pct is None:
            continue
        minutes = _num(quota.get(f"{slot}_window_minutes"))
        letter = _CODEX_LETTERS.get(int(minutes), fallback) if minutes else fallback
        out.append({
            "letter": letter, "remaining_pct": pct,
            "resets_at": _num(quota.get(f"{slot}_resets_at")),
        })
    return out


def fuel_row(
    shell: str, levels: Mapping[str, Any] | None, *, now: float | None = None,
) -> dict[str, Any] | None:
    """One Shell's fuel row from its level snapshot, or ``None`` (no reading)."""
    if not isinstance(levels, Mapping):
        return None
    buckets = _buckets(levels)
    if not buckets:
        return None
    now = time.time() if now is None else now
    read_at = levels.get("updated_at")
    read_epoch = _epoch(read_at)
    limit = STALE_AFTER_SECONDS.get(shell, _DEFAULT_STALE_AFTER)
    # An unparseable capture time is unprovable freshness ⇒ stale, not fresh.
    stale = read_epoch is None or (now - read_epoch) > limit
    binding = min(buckets, key=lambda b: b["remaining_pct"])
    resets_at = binding["resets_at"]
    return {
        "shell": shell,
        "binding_remaining_pct": binding["remaining_pct"],
        "binding_bucket": binding["letter"],
        "resets_in": duration(resets_at - now) if resets_at is not None else None,
        "read_at": read_at if isinstance(read_at, str) else None,
        "stale": stale,
        "buckets": [
            {
                "bucket": b["letter"],
                "remaining_pct": b["remaining_pct"],
                "resets_in": (
                    duration(b["resets_at"] - now) if b["resets_at"] is not None else None
                ),
            }
            for b in buckets
        ],
    }


def chip(rows: Any) -> str | None:
    """``codex S12↻2h00m·W82`` (``·``-joined per Shell, ``codex ?`` when stale)."""
    parts: list[str] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, Mapping) or not row.get("shell"):
            continue
        if row.get("stale"):
            parts.append(f"{row['shell']} ?")
            continue
        segs = []
        for b in row.get("buckets") or []:
            seg = f"{b['bucket']}{int(b['remaining_pct'])}"
            # Only the binding bucket carries its clock — the one that decides.
            if b["bucket"] == row.get("binding_bucket") and b.get("resets_in"):
                seg += f"↻{b['resets_in']}"
            segs.append(seg)
        if segs:
            parts.append(f"{row['shell']} " + "·".join(segs))
    return " · ".join(parts) or None

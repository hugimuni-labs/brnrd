"""The dungeon's feed — the §5 additions of ``design-the-dungeon.md``.

Every reader here is a fact the screen lays out; none of them lays anything
out. Each tolerates absence by answering ``None`` / ``[]`` — never a
fabricated zero (an allowance gauge on a seat, a rate from a three-minute
window) — because the page renders an absent instrument as *absent*.

- ``fuel`` — every Shell's quota buckets as windows with their reset clocks
  and, when measurable, a forecast (``brr.claude_usage`` / ``brr.codex_usage``
  snapshots beside the portal and under ``.brr/``; ``usage-samples.jsonl`` for
  the measured rate, whole-window arithmetic as the fallback — the same two
  sources ``src/frontend/src/lib/tankForecast.ts`` reads, in the same order).
- ``pack`` — the wake's context blocks (``runs/<id>/wake-manifest.json``):
  kept bytes, what the render cut, and the file each was rendered from.
- ``warp`` enrichment — ``visited_at`` (the home tree's last touch of the
  item's own file), ``opens`` (reverse ``needs`` edges among open items),
  ``stake`` (an item file's ``stake:`` row, verbatim), ``receipt`` (``null``
  until the ledger joins a done item to its run's spend — not guessed).
- ``shuttle.place`` — the room the actor stands in.
- ``relics`` — the run's produce manifest (``.relics.jsonl``) with the room
  each relic dropped in.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Mapping

WINDOW_NAMES_CLAUDE = {"session": "session", "week": "week"}
SESSION_S = 5 * 3600
WEEK_S = 7 * 86400
#: Below this fraction of a window elapsed, whole-window arithmetic is noise
#: (``tankForecast.ts`` ``MIN_ELAPSED_FRACTION``).
MIN_ELAPSED_FRACTION = 0.04
#: The measured rate reads samples over this horizon and needs this much span.
MEASURED_HORIZON_S = 3 * 3600
MEASURED_MIN_SPAN_S = 30 * 60

_PLACE_ROOM = {
    "file": "archive", "forge": "forge", "wire": "wire", "crew": "crew",
    "clock": "clock", "shed": "shed",
}
_RELIC_ROOM = {"issue": "forge", "pr": "forge", "commit": "forge", "branch": "forge", "merge": "forge",
               "page": "archive", "file": "archive", "message": "wire", "post": "wire"}


def _num(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def _read_json(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# ── fuel ─────────────────────────────────────────────────────────────────


def _samples(brr_dir: Path, shell: str, window_minutes: float, now: float) -> list[tuple[float, float]]:
    """``(at, used_percent)`` rows for one Shell's window over the horizon."""
    path = brr_dir / "usage-samples.jsonl"
    try:
        size = path.stat().st_size
    except OSError:
        return []
    out: list[tuple[float, float]] = []
    try:
        with path.open("rb") as fh:
            fh.seek(max(0, size - 256_000))
            chunk = fh.read().decode("utf-8", "replace")
    except OSError:
        return []
    for line in chunk.splitlines()[1:] if size > 256_000 else chunk.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or str(row.get("shell") or "").lower() != shell.lower():
            continue
        if _num(row.get("window_minutes")) != window_minutes:
            continue
        at, used = _num(row.get("at")), _num(row.get("used_percent"))
        if at is None or used is None or at < now - MEASURED_HORIZON_S or at > now + 60:
            continue
        out.append((at, used))
    out.sort()
    return out


def forecast(
    pct_left: float, resets_at: float | None, window_s: float | None, now: float,
    samples: list[tuple[float, float]] | None = None,
) -> dict[str, Any] | None:
    """A rate and where it lands — or ``None`` when nothing measurable says.

    Measured first: the used-percent slope over the sampled horizon (≥ 30 min
    of span, monotone-by-window: a reset inside the span zeroes it). Else the
    whole-window average once ≥ 4 % of the window has elapsed. Both come with
    ``source`` so the reader knows which pace it is looking at."""
    if resets_at is None:
        return None
    resets_in = resets_at - now
    rate = source = span = None
    rows = [r for r in (samples or []) if r[0] <= now]
    if len(rows) >= 2 and rows[-1][0] - rows[0][0] >= MEASURED_MIN_SPAN_S and rows[-1][1] >= rows[0][1]:
        hours = (rows[-1][0] - rows[0][0]) / 3600.0
        rate = (rows[-1][1] - rows[0][1]) / hours
        source, span = "measured", int(rows[-1][0] - rows[0][0])
    elif window_s and window_s > 0:
        elapsed = window_s - resets_in
        if elapsed / window_s >= MIN_ELAPSED_FRACTION and elapsed > 0:
            rate = (100.0 - pct_left) / (elapsed / 3600.0)
            source = "window"
    if rate is None:
        return None
    dry_in = (pct_left / rate) * 3600.0 if rate > 0 else None
    at_reset = pct_left - rate * (resets_in / 3600.0) if resets_in > 0 else None
    return {
        "rate_pct_per_h": round(rate, 2),
        "source": source,
        "span_s": span,
        "dry_in_s": int(dry_in) if dry_in is not None else None,
        "resets_in_s": int(resets_in),
        "pct_left_at_reset": round(at_reset, 1) if at_reset is not None else None,
        "dries_first": bool(dry_in is not None and resets_in > 0 and dry_in < resets_in),
    }


def _window(name: str, pct_left: float | None, resets_at: float | None, window_s: float | None,
            now: float, samples: list[tuple[float, float]]) -> dict[str, Any] | None:
    if pct_left is None:
        return None
    return {
        "name": name,
        "pct_left": int(round(pct_left)),
        "resets_at": int(resets_at) if resets_at is not None else None,
        "resets_in_s": int(resets_at - now) if resets_at is not None else None,
        "window_s": int(window_s) if window_s else None,
        "binding": False,
        "forecast": forecast(pct_left, resets_at, window_s, now, samples),
    }


def _claude_bucket(brr_dir: Path, outbox_dir: Path | None, now: float) -> dict[str, Any] | None:
    from .. import claude_usage

    snap = _read_json(outbox_dir / claude_usage.SNAPSHOT_NAME) if outbox_dir else None
    if not snap:
        snap = _read_json(brr_dir / claude_usage.SNAPSHOT_NAME)
    if not snap:
        return None
    quota = snap.get("quota") if isinstance(snap.get("quota"), dict) else {}
    buckets = quota.get("buckets") if isinstance(quota.get("buckets"), dict) else {}
    session_at = _num(quota.get("session_resets_at")) or _num(snap.get("session_resets_at"))
    week_at = _num(quota.get("week_resets_at")) or _num(snap.get("week_resets_at"))
    windows: list[dict[str, Any]] = []
    session = (buckets.get("session") or {}).get("remaining_percentage") if isinstance(buckets.get("session"), dict) else None
    week = (buckets.get("week") or {}).get("remaining_percentage") if isinstance(buckets.get("week"), dict) else None
    row = _window("session", _num(session), session_at, SESSION_S, now, _samples(brr_dir, "claude", 300.0, now))
    if row:
        windows.append(row)
    row = _window("week", _num(week), week_at, WEEK_S, now, _samples(brr_dir, "claude", 10080.0, now))
    if row:
        windows.append(row)
    models = buckets.get("week_models") if isinstance(buckets.get("week_models"), dict) else {}
    for model, data in models.items():
        pct = data.get("remaining_percentage") if isinstance(data, dict) else None
        row = _window(f"{model} week", _num(pct), week_at, WEEK_S, now, [])
        if row:
            windows.append(row)
    if not windows:
        return None
    return {"name": "claude", "shell": "claude", "plan": snap.get("plan_type"),
            "updated_at": snap.get("updated_at"), "windows": windows}


def _codex_bucket(brr_dir: Path, now: float) -> dict[str, Any] | None:
    from .. import codex_usage

    snap = _read_json(brr_dir / codex_usage.SNAPSHOT_NAME)
    if not snap:
        return None
    quota = snap.get("quota") if isinstance(snap.get("quota"), dict) else {}
    windows: list[dict[str, Any]] = []
    for prefix in ("primary", "secondary"):
        minutes = _num(quota.get(f"{prefix}_window_minutes"))
        pct = _num(quota.get(f"{prefix}_remaining_percent"))
        if pct is None:
            continue
        name = "5h" if minutes == 300 else "7d" if minutes == 10080 else (f"{int(minutes)}m" if minutes else prefix)
        row = _window(name, pct, _num(quota.get(f"{prefix}_resets_at")), (minutes or 0) * 60, now,
                      _samples(brr_dir, "codex", minutes, now) if minutes else [])
        if row:
            windows.append(row)
    if not windows:
        return None
    return {"name": "codex", "shell": "codex", "plan": snap.get("plan_type"),
            "updated_at": snap.get("updated_at"), "windows": windows}


def read_fuel(brr_dir: Path, outbox_dir: Path | None, seat_shell: str | None, now: float | None = None) -> dict[str, Any]:
    """``fuel`` — ``{"buckets": [...]}``; the seat's bucket first and flagged;
    ``binding`` marks the window with the least left in each bucket."""
    now = time.time() if now is None else now
    buckets = [b for b in (_claude_bucket(brr_dir, outbox_dir, now), _codex_bucket(brr_dir, now)) if b]
    for bucket in buckets:
        bucket["seat"] = bool(seat_shell and bucket["shell"] == str(seat_shell).lower())
        least = min(bucket["windows"], key=lambda w: w["pct_left"], default=None)
        if least is not None:
            least["binding"] = True
    buckets.sort(key=lambda b: (not b["seat"], b["name"]))
    return {"buckets": buckets}


# ── pack ─────────────────────────────────────────────────────────────────


def _window_tokens(full: Mapping[str, Any] | None) -> int | None:
    facet = ((full or {}).get("resources") or {}).get("context_window")
    if not isinstance(facet, dict):
        return None
    for key in ("window_tokens", "window", "size_tokens", "limit_tokens"):
        value = _num(facet.get(key))
        if value is not None:
            return int(value)
    return None


#: ``bytes_cut`` uses 1 as its "nothing was cut" sentinel (a trailing
#: newline), so a cut is only a cut above it.
_CUT_FLOOR = 1


def _block_source(row: Mapping[str, Any], root: Path) -> tuple[str | None, str | None]:
    """``(source, source_rel)`` for a manifest block: the first source that
    names a real path, verbatim, and its repo-relative form when it is under
    the checkout.

    Verbatim because that is the vocabulary ``beads[].chunks[].path`` and
    ``beads[].places`` already speak — the join a reader wants (*did anything
    this run go back to this block's own file?*) is a string compare against
    that, and relativizing the only copy would break it the way #2011 broke
    the chunk ground. A synthesized block (``dominion``, ``work-surface``,
    ``run-context-bundle`` — the three biggest) has no path at all, and its
    ``None`` is load-bearing: it means *this stratum cannot be joined*, which
    is a different fact from *nothing touched it*."""
    for src in row.get("sources") or ():
        if not isinstance(src, dict):
            continue
        path = src.get("path")
        if not path:
            continue
        text = str(path)
        try:
            rel = str(Path(text).relative_to(root))
        except (ValueError, OSError):
            rel = None
        return text, rel
    return None, None


def read_pack(brr_dir: Path, run_id: str | None, hud: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """``pack`` — the wake's blocks with their kept bytes, what the render cut
    from each, the file each was rendered from, the total, and the context
    occupancy; ``None`` when the run has no manifest.

    ``source`` / ``cut`` are the two readings the byte count alone cannot
    give. ``cut`` is what the wake budget already spent on this block
    (``portal-verb-grammar`` keeps 12.1 KB of a 71.9 KB page); ``source`` is
    the only join any reader gets between a stratum and the run's own acts.
    Neither is a claim about whether the block was *used* — nothing measures
    that — and the page that draws them has to say so."""
    if not run_id:
        return None
    manifest = _read_json(brr_dir / "runs" / run_id / "wake-manifest.json")
    if not manifest or not isinstance(manifest.get("blocks"), list):
        return None
    root = brr_dir.parent
    blocks = []
    for row in manifest["blocks"]:
        if not isinstance(row, dict) or not row.get("name"):
            continue
        kept = _num(row.get("bytes_kept"))
        cut = _num(row.get("bytes_cut"))
        source, source_rel = _block_source(row, root)
        blocks.append({
            "name": str(row["name"]),
            "label": row.get("label") or str(row["name"]),
            "bytes": int(kept) if kept is not None else None,
            "cut": int(cut) if cut is not None and cut > _CUT_FLOOR else None,
            "budget_bytes": int(_num(row.get("budget_bytes"))) if _num(row.get("budget_bytes")) is not None else None,
            "present": bool(row.get("present", True)),
            "owner": row.get("owner"),
            "authority": row.get("authority"),
            "source": source,
            "source_rel": source_rel,
        })
    total = sum(b["bytes"] for b in blocks if b["bytes"] is not None)
    return {
        "blocks": blocks,
        "bytes": total,
        "ctx_tokens": (hud or {}).get("ctx_tokens"),
        "window_tokens": _window_tokens((hud or {}).get("full")),
    }


# ── warp enrichment ──────────────────────────────────────────────────────

_STAKE_RE = re.compile(r"^stake:\s*(?P<stake>.+?)\s*$", re.MULTILINE)


def enrich_warp(warp: Mapping[str, Any], account_home: Path | None, tree: Mapping[str, Any] | None) -> dict[str, Any]:
    """``warp.items[]`` gain ``visited_at`` · ``opens`` · ``stake`` · ``receipt``."""
    items = [dict(row) for row in (warp.get("items") or ()) if isinstance(row, dict)]
    open_ids = {row["id"] for row in items if row.get("state") not in ("done", "retired")}
    opens: dict[str, list[str]] = {}
    for row in items:
        for need in row.get("needs") or ():
            if row["id"] in open_ids:
                opens.setdefault(str(need), []).append(row["id"])
    last_by_path = {}
    for place in ((tree or {}).get("home") or {}).get("places") or ():
        if isinstance(place, dict) and place.get("path"):
            last_by_path[str(place["path"])] = place.get("last")
    warp_dir = None
    if account_home is not None:
        from .. import items as items_mod

        warp_dir = Path(account_home) / "surface" / items_mod.WARP_DIRNAME
    for row in items:
        rel = f"surface/warp/{row['id']}.md"
        row["visited_at"] = last_by_path.get(rel)
        row["opens"] = sorted(opens.get(row["id"], []))
        stake = None
        if warp_dir is not None:
            try:
                match = _STAKE_RE.search((warp_dir / f"{row['id']}.md").read_text(encoding="utf-8"))
            except OSError:
                match = None
            stake = match.group("stake") if match else None
        row["stake"] = stake
        row["receipt"] = None
    return {"goals": list(warp.get("goals") or ()), "items": items}


# ── the actor's room ─────────────────────────────────────────────────────


def shuttle_place(shuttle: Mapping[str, Any] | None, run_id: str | None,
                  warp: Mapping[str, Any], beads: list[Mapping[str, Any]]) -> str | None:
    """The room the actor stands in: the shed while listening; the item the
    run took; else the fixed room of the last bead's ``place_kind``; else the
    shed when a seat exists at all, ``None`` when it does not."""
    if not shuttle:
        return None
    if shuttle.get("state") == "listening":
        return "shed"
    if run_id:
        for row in warp.get("items") or ():
            if isinstance(row, dict) and row.get("taken") == run_id and row.get("state") not in ("done", "retired"):
                return str(row["id"])
    for bead in reversed(beads):
        kind = bead.get("place_kind")
        if kind in _PLACE_ROOM:
            return _PLACE_ROOM[kind]
    return "shed"


# ── relics ───────────────────────────────────────────────────────────────


def read_relics(outbox_dir: Path | None) -> list[dict[str, Any]]:
    """``relics`` — the produce manifest, each with the room it dropped in."""
    if outbox_dir is None:
        return []
    path = Path(outbox_dir) / ".relics.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or not row.get("kind"):
            continue
        kind = str(row["kind"])
        out.append({
            "kind": kind,
            "number": row.get("number"),
            "ref": row.get("ref") or row.get("sha") or row.get("path") or row.get("url"),
            "action": row.get("action"),
            "at": row.get("at"),
            "place": _RELIC_ROOM.get(kind, "shed"),
        })
    return out

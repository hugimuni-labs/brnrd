"""Self-scheduled thoughts — the resident wakes itself on its own clock.

The resident keeps a declarative **schedule** in its dominion
(``.brr/dominion/schedule.md``); the daemon's reflex loop reads it each
tick and fires due entries as ordinary inbox events, which flow through
the normal single-flight pipeline. A self-scheduled wake *is just an
event* whose source happens to be the resident itself — consistent with
the agent-as-memory thesis. See ``kb/design-self-scheduled-thoughts.md``.

Two trigger forms cover the ground without cron's 5-field grammar:

- ``at: <ISO-8601>`` — one-shot, absolute (deferral, reminders); the
  absolute time travels with the dominion, so it fires correctly on a
  second machine / after reinstall.
- ``every: <duration>`` — recurring at a fixed interval (``30m``, ``1h``,
  ``24h``, ``1h30m``); anchored on first sight (adding it does not fire
  instantly), then fired each interval.

Split of concerns mirrors the memory layers: the **specs** are owned and
durable (dominion, committed); the **firing-state** (last-fired
timestamps) is operational — daemon-owned, gitignored, machine-persistent
(survives daemon restarts; lost only on machine-loss). The daemon never
writes the agent's ``schedule.md``, so firing never races the dominion
commit lock.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEDULE_FILE = "schedule.md"  # in the dominion
STATE_DIRNAME = "schedule"  # under the .brr runtime dir
STATE_FILE = "state.json"
DEFAULT_STALE_GRACE_S = 7 * 24 * 3600  # an `at:` older than this won't surprise-fire

_FIELD_RE = re.compile(r"^\s*(at|every|conversation_key)\s*:\s*(.+?)\s*$", re.IGNORECASE)
_DURATION_TOKEN_RE = re.compile(r"(\d+)\s*([smhd])", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass
class ScheduleEntry:
    """One schedule spec: a thought to emit when its trigger is due."""

    id: str
    kind: str  # "at" | "every"
    body: str
    at: float | None = None  # epoch seconds, for kind == "at"
    interval: float | None = None  # seconds, for kind == "every"
    raw_when: str = ""  # original trigger string, for messages
    # Optional conversation this entry's firings thread into. Defaults
    # (at fire time) to ``schedule:<id>`` so a recurring entry's wakes
    # share a readable history; set explicitly to thread into an existing
    # gate conversation (e.g. ``telegram:12345:``).
    conversation_key: str | None = None


# ── Parsing ──────────────────────────────────────────────────────────


def parse_duration(text: str) -> float | None:
    """Parse ``1h30m`` / ``45s`` / ``2d`` into seconds, or ``None``.

    Tokens are ``<int><unit>`` with unit s/m/h/d, summed. The whole string
    must be tokens (and whitespace) — a stray word makes it invalid.
    """
    if not text:
        return None
    if _DURATION_TOKEN_RE.sub("", text).strip():
        return None  # leftover non-token characters
    total = 0
    matched = False
    for amount, unit in _DURATION_TOKEN_RE.findall(text):
        total += int(amount) * _UNIT_SECONDS[unit.lower()]
        matched = True
    return float(total) if matched else None


def parse_iso(text: str) -> float | None:
    """Parse an ISO-8601 timestamp into epoch seconds (UTC), or ``None``.

    Accepts a trailing ``Z`` and naive timestamps (assumed UTC).
    """
    if not text:
        return None
    candidate = text.strip()
    if candidate.endswith(("Z", "z")):
        candidate = candidate[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _slug(title: str) -> str:
    return _SLUG_RE.sub("-", title.strip().lower()).strip("-")


def _build_entry(title: str, fields: dict[str, str], body_lines: list[str]) -> ScheduleEntry | None:
    eid = _slug(title)
    if not eid:
        return None
    body = "\n".join(body_lines).strip()
    conv = (fields.get("conversation_key") or "").strip() or None
    # `every` wins if both are present (one trigger per entry is the convention).
    if "every" in fields:
        interval = parse_duration(fields["every"])
        if not interval or interval <= 0:
            return None
        return ScheduleEntry(
            eid, "every", body, interval=interval,
            raw_when=fields["every"], conversation_key=conv,
        )
    if "at" in fields:
        at = parse_iso(fields["at"])
        if at is None:
            return None
        return ScheduleEntry(
            eid, "at", body, at=at, raw_when=fields["at"], conversation_key=conv,
        )
    return None  # no trigger → inert, skipped


def parse_schedule(dominion_dir: Path) -> list[ScheduleEntry]:
    """Parse the dominion's ``schedule.md`` into :class:`ScheduleEntry` records.

    Format: a ``## `` heading per entry (its id is the slugified heading),
    an ``at:`` or ``every:`` line, an optional ``conversation_key:`` line
    (threads the firings; defaults to ``schedule:<id>`` at fire time), then
    optional body prose (the thought to run). Text before the first heading
    is a comment/header and ignored. An entry with no/invalid trigger is
    dropped.
    """
    path = dominion_dir / SCHEDULE_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []

    entries: list[ScheduleEntry] = []
    title: str | None = None
    fields: dict[str, str] = {}
    body_lines: list[str] = []

    def _flush() -> None:
        if title is None:
            return
        entry = _build_entry(title, fields, body_lines)
        if entry:
            entries.append(entry)

    for line in text.splitlines():
        if line.startswith("## "):
            _flush()
            title = line[3:].strip()
            fields = {}
            body_lines = []
            continue
        if title is None:
            continue
        m = _FIELD_RE.match(line)
        if m:
            fields[m.group(1).lower()] = m.group(2).strip()
            continue
        body_lines.append(line)
    _flush()
    return entries


# ── Firing-state (runtime, daemon-owned) ─────────────────────────────


def _state_path(brr_dir: Path) -> Path:
    return brr_dir / STATE_DIRNAME / STATE_FILE


def load_state(brr_dir: Path) -> dict:
    """Load the firing-state map (entry id → record). ``{}`` on absence/parse error."""
    try:
        return json.loads(_state_path(brr_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(brr_dir: Path, state: dict) -> None:
    """Persist the firing-state map atomically (temp + rename)."""
    path = _state_path(brr_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.write(fd, json.dumps(state, indent=2).encode("utf-8"))
        os.close(fd)
        os.rename(tmp, path)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── Due computation (pure) ───────────────────────────────────────────


def due_entries(
    entries: list[ScheduleEntry],
    state: dict,
    now: float,
    *,
    stale_grace: float = DEFAULT_STALE_GRACE_S,
) -> tuple[list[ScheduleEntry], dict]:
    """Decide which entries are due, returning ``(due, new_state)``.

    Pure: no clock, no I/O — ``now`` and ``state`` are inputs. ``new_state``
    reflects the firings/anchorings this call implies and is pruned to the
    ids still present in *entries* (so removing an entry forgets its state).

    - ``every`` — anchored (recorded, not fired) on first sight; fired when
      ``now - last_fired >= interval``.
    - ``at`` — fired once when ``now >= at``; an ``at`` more than
      *stale_grace* in the past is anchored-as-fired without firing (so a
      stale one-shot can't surprise-fire after a machine-loss state wipe).
    """
    new_state = dict(state)
    due: list[ScheduleEntry] = []

    for e in entries:
        rec = new_state.get(e.id)
        seen = rec is not None
        if e.kind == "every":
            if not seen:
                new_state[e.id] = {"kind": "every", "last_fired": now}
                continue
            last = rec.get("last_fired")
            if last is None or (now - last) >= (e.interval or 0):
                due.append(e)
                new_state[e.id] = {"kind": "every", "last_fired": now}
        elif e.kind == "at":
            if seen and rec.get("fired"):
                continue
            if now >= (e.at or 0):
                fired_record = {"kind": "at", "last_fired": now, "fired": True}
                if (now - (e.at or 0)) > stale_grace:
                    new_state[e.id] = fired_record  # too late — anchor, don't fire
                else:
                    due.append(e)
                    new_state[e.id] = fired_record

    present = {e.id for e in entries}
    new_state = {k: v for k, v in new_state.items() if k in present}
    return due, new_state

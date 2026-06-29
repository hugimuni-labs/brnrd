"""Protocol — event and response file CRUD for the ``.brr/`` filesystem API.

All writes use atomic temp-file-then-rename to prevent races between
gate threads and the daemon main thread.  Reads silently skip files
that fail to parse (transient state during rename).
"""

from __future__ import annotations

import os
import re
import tempfile
import threading
import time
import random
import string
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── Inbox wake signal ────────────────────────────────────────────────

# A process-local edge-trigger the daemon loop waits on so a fresh
# in-process event (a gate thread enqueuing a message, a self-scheduled
# thought firing) is picked up promptly instead of sleeping out a full
# poll tick. ``create_event`` sets it whenever it writes a ``pending``
# event in this process; cross-process writers (the ``brr run`` CLI) can't
# reach it, so the daemon's periodic poll stays the backstop for those.
_inbox_wake = threading.Event()


def inbox_wake() -> threading.Event:
    """Return the process-local inbox wake signal (see module note)."""
    return _inbox_wake


# ── Frontmatter parsing ─────────────────────────────────────────────


def parse_frontmatter(text: str) -> dict[str, Any]:
    """Parse YAML-like frontmatter (``---`` delimited) into a flat dict.

    Handles the restricted subset used by brr: simple ``key: value``
    pairs and one level of nesting (for runners.md profiles).
    No dependency on pyyaml.
    """
    m = re.match(r"^---\n(.*?\n)---", text, re.DOTALL)
    if not m:
        return {}
    lines = m.group(1).splitlines()
    return _parse_block(lines, 0)[0]


def frontmatter_body(text: str) -> str:
    """Return the body text after the frontmatter."""
    m = re.match(r"^---\n.*?\n---\n?", text, re.DOTALL)
    if m:
        return text[m.end():]
    return text


# Routing selectors that may lead an outbox message's frontmatter. Used
# only to gate the lenient (missing-opening-fence) parse below — see
# ``parse_outbox_message``.
_OUTBOX_ROUTING_KEYS = ("event", "gate", "respawn")


def parse_outbox_message(text: str) -> tuple[dict[str, Any], str]:
    """Parse an outbox message's routing frontmatter and body, tolerantly.

    Returns ``(meta, body)``. Accepts two shapes:

    - **Canonical** — a ``---``-fenced frontmatter block, exactly as
      :func:`parse_frontmatter` / :func:`frontmatter_body` handle it.
    - **Lenient** — a leading ``key: value`` block with *no opening
      fence*, terminated by a ``---`` line: e.g. ``event: <id>\\n---\\nbody``.

    The lenient shape exists because the resident reaches for it
    naturally — the delivery contract names ``event:`` / ``gate:`` /
    ``respawn:`` as
    "frontmatter" without showing the fences, and writing the selector
    line then a ``---`` separator reads as obviously correct. Under the
    strict parser that silently failed: the routing was dropped, the
    literal ``event:`` line leaked into the delivered message, and the
    reply attached to the run's *lead* event instead of its target (the
    "messed-up quotes" failure). Tolerating it moves the lesson off the
    "remember the exact fences" rung of the robustness ladder.

    To avoid mistaking a plain message for routing, the lenient path
    engages **only** when the first non-empty line is a recognised
    routing selector (``event:`` / ``gate:`` / ``respawn:``) *and* a closing ``---``
    line follows in the contiguous leading key-block. A normal message
    that merely contains ``---`` dividers (a PLAN, say) is never touched.
    Misparses degrade safely: the drain drops an unknown ``event:`` target
    or unconfigured ``gate:`` with a console note rather than misdelivering.
    """
    if text.startswith("---\n"):
        return parse_frontmatter(text), frontmatter_body(text)

    lines = text.splitlines(keepends=True)
    idx = 0
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    if idx >= len(lines):
        return {}, text

    first = lines[idx].strip()
    lead_key = first.split(":", 1)[0].strip() if ":" in first else ""
    if lead_key not in _OUTBOX_ROUTING_KEYS:
        return {}, text

    block: list[str] = []
    j = idx
    while j < len(lines):
        stripped = lines[j].strip()
        if stripped == "---":
            meta = _parse_block(block, 0)[0] if block else {}
            body = "".join(lines[j + 1:])
            return meta, body
        if stripped == "" or (":" in stripped and not stripped.startswith("#")):
            block.append(lines[j].rstrip("\n"))
            j += 1
            continue
        break
    return {}, text


def _parse_block(lines: list[str], base_indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        indent = len(line) - len(stripped)
        if indent < base_indent:
            break
        if ":" not in stripped:
            i += 1
            continue
        key, _, val = stripped.partition(":")
        key = key.strip()
        val = val.strip()
        if val:
            result[key] = _coerce(val)
            i += 1
        else:
            child_indent = indent + 2
            if i + 1 < len(lines):
                next_stripped = lines[i + 1].lstrip()
                next_indent = len(lines[i + 1]) - len(next_stripped)
                if next_indent >= child_indent and next_stripped:
                    child, consumed = _parse_block(lines[i + 1:], child_indent)
                    result[key] = child
                    i += 1 + consumed
                    continue
            result[key] = ""
            i += 1
    return result, i


def _coerce(val: str) -> Any:
    if val in ("true", "True"):
        return True
    if val in ("false", "False"):
        return False
    if val in ("null", "None", "~"):
        return None
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
        return val[1:-1]
    try:
        return int(val)
    except ValueError:
        return val


# ── Atomic file I/O ──────────────────────────────────────────────────


def _atomic_write(path: Path, content: str) -> None:
    """Write *content* to *path* atomically via temp + rename."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.write(fd, content.encode("utf-8"))
        os.close(fd)
        os.rename(tmp, path)
    except BaseException:
        os.close(fd) if not os.get_inheritable(fd) else None
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _format_meta_value(value: object) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return ""
    return str(value)


def _generate_id() -> str:
    ts = time.time_ns()
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"evt-{ts}-{rand}"


# ── Event files ──────────────────────────────────────────────────────


def create_event(
    inbox_dir: Path,
    source: str,
    body: str,
    *,
    status: str = "pending",
    **meta: object,
) -> Path:
    """Create a new event file in *inbox_dir*. Returns the file path.

    *status* defaults to ``pending`` (a normal inbound event the daemon
    will wake on). Pass ``status="done"`` to inject an outbound-only event
    a gate delivers but the daemon never processes — the mechanism behind
    agent-initiated out-of-bound / scheduled delivery (the event is born
    ``done`` in one atomic write so the inbox poll can never grab it as
    pending and spawn a stray thought).
    """
    inbox_dir.mkdir(parents=True, exist_ok=True)
    eid = _generate_id()
    lines = [
        "---",
        f"id: {eid}",
        f"source: {source}",
        f"status: {status}",
    ]
    for k, v in meta.items():
        lines.append(f"{k}: {v}")
    lines.append(f"created: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    lines.append("---")
    lines.append(body)
    path = inbox_dir / f"{eid}.md"
    _atomic_write(path, "\n".join(lines) + "\n")
    if status == "pending":
        # Nudge a waiting daemon loop so it reacts to this event without
        # waiting out a full poll tick. Outbound-only (``done``) events
        # are delivered by gate threads, not the spawn loop, so they
        # don't wake it. Harmless no-op outside the daemon process.
        _inbox_wake.set()
    return path


def _read_event(path: Path) -> dict[str, Any] | None:
    """Parse an event file, returning metadata + body. None on failure."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    fm = parse_frontmatter(text)
    if not fm.get("id"):
        return None
    fm["body"] = frontmatter_body(text).strip()
    fm["_path"] = path
    return fm


def _event_sort_key(entry: os.DirEntry) -> tuple[int, str]:
    try:
        mtime = entry.stat().st_mtime_ns
    except OSError:
        mtime = 0
    return (mtime, entry.name)


def list_pending(inbox_dir: Path) -> list[dict[str, Any]]:
    """Return events with status pending or processing, oldest first."""
    if not inbox_dir.exists():
        return []
    events = []
    for entry in sorted(os.scandir(inbox_dir), key=_event_sort_key):
        if not entry.name.endswith(".md"):
            continue
        ev = _read_event(Path(entry.path))
        if ev and ev.get("status") in ("pending", "processing"):
            events.append(ev)
    return events


def _parse_iso_epoch(value: object) -> float | None:
    """Parse the event ``defer_until`` timestamp, returning epoch seconds."""
    if value is None:
        return None
    candidate = str(value).strip()
    if not candidate:
        return None
    if candidate.endswith(("Z", "z")):
        candidate = candidate[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def event_is_deferred(event: dict[str, Any], now: float | None = None) -> bool:
    """Return true when an event has a future ``defer_until`` timestamp.

    Invalid timestamps degrade to "not deferred" so a malformed event does
    not disappear from dispatch forever.
    """
    until = _parse_iso_epoch(event.get("defer_until"))
    if until is None:
        return False
    return until > (time.time() if now is None else now)


def list_dispatchable(
    inbox_dir: Path,
    *,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Return pending/processing events whose deferral has expired.

    ``list_pending`` intentionally keeps returning deferred events so a
    fresh wake can still see and fold them from the live inbox. The daemon
    dispatch loop uses this narrower view for choosing a lead event.
    """
    return [
        event for event in list_pending(inbox_dir)
        if not event_is_deferred(event, now=now)
    ]


def list_done(inbox_dir: Path, source: str) -> list[dict[str, Any]]:
    """Return done events matching *source*, oldest first."""
    if not inbox_dir.exists():
        return []
    events = []
    for entry in sorted(os.scandir(inbox_dir), key=_event_sort_key):
        if not entry.name.endswith(".md"):
            continue
        ev = _read_event(Path(entry.path))
        if ev and ev.get("status") == "done" and ev.get("source") == source:
            events.append(ev)
    return events


def list_active(inbox_dir: Path, source: str) -> list[dict[str, Any]]:
    """Return processing+done events matching *source*, oldest first.

    The delivery surface for the streaming (multi-response) protocol: a
    *processing* event may already have interim responses queued, and a
    *done* event additionally has its terminal response ready. A plain
    single-response run shows up here only once it reaches ``done`` (it
    has no partials while processing), so this stays behaviourally
    identical to ``list_done`` for that case.
    """
    if not inbox_dir.exists():
        return []
    events = []
    for entry in sorted(os.scandir(inbox_dir), key=_event_sort_key):
        if not entry.name.endswith(".md"):
            continue
        ev = _read_event(Path(entry.path))
        if ev and ev.get("status") in ("processing", "done") \
                and ev.get("source") == source:
            events.append(ev)
    return events


def set_status(event: dict[str, Any], status: str) -> None:
    """Update the status field of an event file atomically."""
    path: Path = event["_path"]
    text = path.read_text(encoding="utf-8")
    old_status = event.get("status", "pending")
    new_text = text.replace(f"status: {old_status}", f"status: {status}", 1)
    _atomic_write(path, new_text)
    event["status"] = status


def update_event_meta(event: dict[str, Any], **updates: object) -> None:
    """Set or clear flat event frontmatter keys atomically.

    Passing ``None`` removes a key. Event frontmatter is intentionally flat
    today; nested blocks are preserved if present but not edited.
    """
    path: Path = event["_path"]
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?\n)---\n?", text, re.DOTALL)
    if not m:
        return

    seen: set[str] = set()
    lines: list[str] = []
    for line in m.group(1).splitlines():
        stripped = line.lstrip()
        if ":" not in stripped or line.startswith(" "):
            lines.append(line)
            continue
        key = stripped.split(":", 1)[0].strip()
        if key in updates:
            seen.add(key)
            value = updates[key]
            if value is None:
                continue
            lines.append(f"{key}: {_format_meta_value(value)}")
        else:
            lines.append(line)

    insert_at = len(lines)
    for idx, line in enumerate(lines):
        if line.split(":", 1)[0].strip() == "created":
            insert_at = idx
            break
    additions = [
        f"{key}: {_format_meta_value(value)}"
        for key, value in updates.items()
        if key not in seen and value is not None
    ]
    lines[insert_at:insert_at] = additions

    body = text[m.end():]
    _atomic_write(path, "---\n" + "\n".join(lines) + "\n---\n" + body)
    for key, value in updates.items():
        if value is None:
            event.pop(key, None)
        else:
            event[key] = value


# ── Response files ───────────────────────────────────────────────────


def response_path(responses_dir: Path, event_id: str) -> Path:
    """Return the expected response file path for an event."""
    return responses_dir / f"{event_id}.md"


def response_exists(responses_dir: Path, event_id: str) -> bool:
    """Check if a response file exists for the given event."""
    return response_path(responses_dir, event_id).exists()


def read_response(responses_dir: Path, event_id: str) -> str | None:
    """Read the response body, or None if missing.

    Responses are plain markdown — what the runner printed on stdout.
    For backwards compatibility we still strip a leading frontmatter
    block if one happens to be present (it never is in normal flow).
    """
    path = response_path(responses_dir, event_id)
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    return frontmatter_body(text).strip()


def write_response(responses_dir: Path, event_id: str, body: str) -> Path:
    """Write a plain-text response file. Returns the file path.

    The wire format is just the body — there is no frontmatter
    contract on response files. ``event_id`` is preserved in the
    filename so the daemon and gates can correlate without parsing.
    """
    responses_dir.mkdir(parents=True, exist_ok=True)
    if not body.endswith("\n"):
        body = body + "\n"
    path = response_path(responses_dir, event_id)
    _atomic_write(path, body)
    return path


# ── Interim response partials (the streaming queue) ─────────────────
# A per-event queue of interim responses the resident ships mid-flight
# (the multi-response protocol, see kb/design-multi-response.md). The
# terminal response stays ``<eid>.md``; partials live in
# ``<eid>.partials/`` as ordered files, delivered before the terminal
# and deleted as they go. Absent any partials, delivery is exactly the
# single-response flow — this surface no-ops when unused.


def partials_dir(responses_dir: Path, event_id: str) -> Path:
    """Return the interim-response queue directory for an event."""
    return responses_dir / f"{event_id}.partials"


def list_partials(responses_dir: Path, event_id: str) -> list[Path]:
    """Return pending interim response files for an event, oldest first.

    Names are zero-padded sequence numbers, so a lexical sort is a
    chronological sort. Delivered partials are deleted, so the queue
    holds only the not-yet-delivered tail.
    """
    pdir = partials_dir(responses_dir, event_id)
    if not pdir.exists():
        return []
    return sorted(
        (p for p in pdir.iterdir() if p.suffix == ".md"),
        key=lambda p: p.name,
    )


def write_partial(responses_dir: Path, event_id: str, body: str) -> Path:
    """Append an interim response to an event's queue. Returns the path.

    Sequence numbers continue past the current max so ordering survives
    even though delivered partials are deleted (a reset can only happen
    once the queue is empty, i.e. nothing is left to mis-order against).
    """
    pdir = partials_dir(responses_dir, event_id)
    pdir.mkdir(parents=True, exist_ok=True)
    existing = [int(p.stem) for p in pdir.glob("*.md") if p.stem.isdigit()]
    seq = (max(existing) + 1) if existing else 1
    if not body.endswith("\n"):
        body = body + "\n"
    path = pdir / f"{seq:06d}.md"
    _atomic_write(path, body)
    return path


def read_partial(path: Path) -> str | None:
    """Read an interim response body, or None if it can't be read."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return frontmatter_body(text).strip()


def cleanup(
    event_path: Path,
    response_path: Path | None = None,
    partials: Path | None = None,
) -> None:
    """Delete event, optional terminal response, and the partials queue."""
    event_path.unlink(missing_ok=True)
    if response_path:
        response_path.unlink(missing_ok=True)
    if partials and partials.exists():
        for p in partials.iterdir():
            p.unlink(missing_ok=True)
        partials.rmdir()

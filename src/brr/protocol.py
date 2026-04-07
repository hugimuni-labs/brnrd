"""Protocol — event and response file CRUD for the ``.brr/`` filesystem API.

All writes use atomic temp-file-then-rename to prevent races between
gate threads and the daemon main thread.  Reads silently skip files
that fail to parse (transient state during rename).
"""

from __future__ import annotations

import os
import re
import tempfile
import time
import random
import string
from pathlib import Path
from typing import Any


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


def _generate_id() -> str:
    ts = int(time.time())
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"evt-{ts}-{rand}"


# ── Event files ──────────────────────────────────────────────────────


def create_event(
    inbox_dir: Path,
    source: str,
    body: str,
    **meta: object,
) -> Path:
    """Create a new event file in *inbox_dir*. Returns the file path."""
    inbox_dir.mkdir(parents=True, exist_ok=True)
    eid = _generate_id()
    lines = [
        "---",
        f"id: {eid}",
        f"source: {source}",
        "status: pending",
    ]
    for k, v in meta.items():
        lines.append(f"{k}: {v}")
    lines.append(f"created: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    lines.append("---")
    lines.append(body)
    path = inbox_dir / f"{eid}.md"
    _atomic_write(path, "\n".join(lines) + "\n")
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


def list_pending(inbox_dir: Path) -> list[dict[str, Any]]:
    """Return events with status pending or processing, oldest first."""
    if not inbox_dir.exists():
        return []
    events = []
    for entry in sorted(os.scandir(inbox_dir), key=lambda e: e.name):
        if not entry.name.endswith(".md"):
            continue
        ev = _read_event(Path(entry.path))
        if ev and ev.get("status") in ("pending", "processing"):
            events.append(ev)
    return events


def list_done(inbox_dir: Path, source: str) -> list[dict[str, Any]]:
    """Return done events matching *source*, oldest first."""
    if not inbox_dir.exists():
        return []
    events = []
    for entry in sorted(os.scandir(inbox_dir), key=lambda e: e.name):
        if not entry.name.endswith(".md"):
            continue
        ev = _read_event(Path(entry.path))
        if ev and ev.get("status") == "done" and ev.get("source") == source:
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


# ── Response files ───────────────────────────────────────────────────


def response_path(responses_dir: Path, event_id: str) -> Path:
    """Return the expected response file path for an event."""
    return responses_dir / f"{event_id}.md"


def response_exists(responses_dir: Path, event_id: str) -> bool:
    """Check if a response file exists for the given event."""
    return response_path(responses_dir, event_id).exists()


def read_response(responses_dir: Path, event_id: str) -> str | None:
    """Read the response body, or None if missing."""
    path = response_path(responses_dir, event_id)
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    return frontmatter_body(text).strip()


def write_response(responses_dir: Path, event_id: str, body: str) -> Path:
    """Write a response file. Returns the file path."""
    responses_dir.mkdir(parents=True, exist_ok=True)
    content = f"---\nevent_id: {event_id}\n---\n{body}\n"
    path = response_path(responses_dir, event_id)
    _atomic_write(path, content)
    return path


def cleanup(event_path: Path, response_path: Path | None = None) -> None:
    """Delete event and optionally response files."""
    event_path.unlink(missing_ok=True)
    if response_path:
        response_path.unlink(missing_ok=True)

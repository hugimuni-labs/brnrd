"""A body's tool calls, one line each, for a person watching a strand work.

The jack appends ``{at, tool, arg}`` to ``port/tools.jsonl`` on every post
hook. ``arg`` is what a reader can see without seeing a secret: a Bash
call's description, a file's basename, a search pattern; never a command.
The append is one write and never raises: a lost line costs nothing, a
slowed or failed hook costs the body. The file rotates to ``tools.jsonl.1``
past ``CAP`` bytes, so a room holds at most twice that.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

CAP = 1_000_000
ARG_MAX = 80

_PATH_KEYS = ("file_path", "notebook_path", "path")
_PATTERN_KEYS = ("pattern", "query")


def _path(room: Path) -> Path:
    return Path(room) / "port" / "tools.jsonl"


def arg_of(tool: str, given: object) -> str:
    """The one safe word for a call. Bash says only its description."""
    if not isinstance(given, dict):
        return ""
    if tool == "Bash":
        text = given.get("description")
    elif tool in {"Agent", "Task"}:
        text = given.get("description")
    elif tool == "WebFetch":
        # A URL's path and query can carry a token; its host cannot.
        text = urlsplit(str(given.get("url") or "")).hostname
    else:
        text = next((given[key] for key in _PATTERN_KEYS if given.get(key)), None)
        if text is None:
            text = next((Path(str(given[key])).name for key in _PATH_KEYS if given.get(key)), None)
    if not isinstance(text, str):
        return ""
    text = " ".join(text.split())
    return text if len(text) <= ARG_MAX else text[: ARG_MAX - 1] + "…"


def note(room: Path, stdin_text: str) -> None:
    """Append this call's line. Swallows every error."""
    try:
        payload = json.loads(stdin_text)
        tool = payload.get("tool_name") if isinstance(payload, dict) else None
        if not isinstance(tool, str) or not tool:
            return
        line = json.dumps({"at": round(time.time(), 3), "tool": tool[:40],
                           "arg": arg_of(tool, payload.get("tool_input"))},
                          ensure_ascii=False, separators=(",", ":")) + "\n"
        path = _path(room)
        try:
            if path.stat().st_size > CAP:
                os.replace(path, path.with_name(path.name + ".1"))
        except OSError:
            pass
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line.encode())
        finally:
            os.close(fd)
    except Exception:  # noqa: BLE001 - the trace must never cost the hook
        return


def read(room: Path, since: float = 0.0, limit: int = 500) -> list[dict]:
    """The room's last ``limit`` calls after ``since``, oldest first."""
    rows: list[dict] = []
    path = _path(room)
    for part in (path.with_name(path.name + ".1"), path):
        try:
            text = part.read_text(errors="replace")
        except OSError:
            continue
        for raw in text.splitlines():
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            if isinstance(row, dict) and isinstance(row.get("at"), (int, float)) and row["at"] > since:
                rows.append({"at": row["at"], "tool": str(row.get("tool", "")), "arg": str(row.get("arg", ""))})
    return rows[-limit:] if limit else rows

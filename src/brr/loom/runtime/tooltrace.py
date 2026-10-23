"""A body's tool calls, a line at each end, for a person watching a strand work.

The jack appends a start line at the pre hook and an end line at the post
hook to ``port/tools.jsonl``; the two share the call's ``id``
(``tool_use_id``). A start carries ``cmd``, a Bash command's first line with
its secrets masked, and ``say``, the body's last sentence when it changed.
``arg`` is the one safe word: a Bash call's description, a file's basename,
a search pattern. The append is one write and never raises: a lost line
costs nothing, a slowed or failed hook costs the body. The file rotates to
``tools.jsonl.1`` past ``CAP`` bytes, so a room holds at most twice that.

``read`` pairs the lines into calls and ``live`` says what is open now. A
codex body runs no tool hooks; both read its rollout log instead.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from brr.pause import read_paused_record
from brr.redact import redact_detail

CAP = 1_000_000
ARG_MAX = 80
CMD_MAX = 120
SAY_MAX = 160
TAIL = 64_000

_PATH_KEYS = ("file_path", "notebook_path", "path")
_PATTERN_KEYS = ("pattern", "query")
# The shape of cloud_publisher's heredoc pattern; that module is too heavy
# to import on every call.
_HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
_USERINFO_RE = re.compile(r"://[^/\s@]+@")
_LONG_RE = re.compile(r"[A-Za-z0-9_\-+=]{32,}")
_SENTENCE_RE = re.compile(r"(?<=[.!?:])\s")


def _path(room: Path) -> Path:
    return Path(room) / "port" / "tools.jsonl"


def _cap(text: str, most: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= most else text[: most - 1] + "…"


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
    return _cap(text, ARG_MAX)


def _masked(text: str) -> str:
    text = _USERINFO_RE.sub("://<redacted>@", redact_detail(text))
    return _LONG_RE.sub(lambda m: "<redacted>" if re.search(r"[A-Za-z]", m[0]) and re.search(r"\d", m[0])
                        else m[0], text)


def cmd_of(command: object) -> str:
    """A command's first line, a heredoc's body cut, secrets masked."""
    if not isinstance(command, str) or not command.strip():
        return ""
    lines = command.strip().splitlines()
    head = lines[0]
    heredoc = _HEREDOC_RE.search(head)
    if heredoc:
        head = head[: heredoc.end()] + " …"
    elif len(lines) > 1:
        head += " …"
    return _cap(_masked(head), CMD_MAX)


def _sentence(text: str) -> str:
    text = " ".join(text.split())
    return _cap(_masked(_SENTENCE_RE.split(text, 1)[0]), SAY_MAX) if text else ""


def _said(transcript: object) -> str:
    """The first sentence of the body's newest narration in its transcript."""
    if not isinstance(transcript, str) or not transcript:
        return ""
    with open(transcript, "rb") as handle:
        handle.seek(max(0, os.fstat(handle.fileno()).st_size - TAIL))
        raw = handle.read().decode(errors="replace")
    for line in reversed(raw.splitlines()):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("type") != "assistant":
            continue
        content = (entry.get("message") or {}).get("content")
        texts = [b.get("text") for b in content or [] if isinstance(b, dict) and b.get("type") == "text"]
        if texts and isinstance(texts[-1], str) and texts[-1].strip():
            return _sentence(texts[-1])
    return ""


def note(room: Path, stdin_text: str, phase: str = "post") -> None:
    """Append this call's start (``pre``) or end (``post``, ``refused``) line. Swallows every error."""
    try:
        payload = json.loads(stdin_text)
        tool = payload.get("tool_name") if isinstance(payload, dict) else None
        if not isinstance(tool, str) or not tool:
            return
        given = payload.get("tool_input")
        row: dict = {"at": round(time.time(), 3), "end": 0 if phase == "pre" else 1,
                     "tool": tool[:40], "arg": arg_of(tool, given)}
        if isinstance(payload.get("tool_use_id"), str):
            row["id"] = payload["tool_use_id"][:80]
        path = _path(room)
        if phase == "pre":
            if tool == "Bash" and isinstance(given, dict):
                row["cmd"] = cmd_of(given.get("command"))
            if isinstance(payload.get("agent_id"), str):
                row["by"] = payload["agent_id"][:40]
            try:
                said = _said(payload.get("transcript_path"))
                last = path.with_suffix(".say")
                if said and said != (last.read_text() if last.exists() else ""):
                    last.write_text(said)
                    row["say"] = said
            except OSError:
                pass
        else:
            if isinstance(payload.get("duration_ms"), (int, float)):
                row["ms"] = int(payload["duration_ms"])
            if phase == "refused" or payload.get("hook_event_name") == "PostToolUseFailure":
                row["failed"] = 1
        line = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
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


def _call(at: float, row: dict) -> dict:
    return {"at": at, "end": None, "ms": None, "tool": str(row.get("tool", "")), "arg": str(row.get("arg", "")),
            "cmd": str(row.get("cmd", "")), "say": str(row.get("say", "")), "by": str(row.get("by", "")),
            "failed": False}


def _close(call: dict, at: float, row: dict) -> None:
    ms = row.get("ms")
    call.update(end=at, ms=int(ms) if isinstance(ms, (int, float)) else round((at - call["at"]) * 1000),
                failed=bool(row.get("failed")))


def _hooked(room: Path, tail: int = 0) -> list[dict]:
    """The hook lines paired into calls, oldest first by start."""
    calls: list[dict] = []
    by_id: dict[str, dict] = {}
    anonymous: list[dict] = []
    path = _path(room)
    for part in (() if tail else (path.with_name(path.name + ".1"),)) + (path,):
        try:
            with open(part, "rb") as handle:
                if tail:
                    handle.seek(max(0, os.fstat(handle.fileno()).st_size - tail))
                text = handle.read().decode(errors="replace")
        except OSError:
            continue
        for raw in text.splitlines():
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(row, dict) or not isinstance(row.get("at"), (int, float)):
                continue
            at, ident = float(row["at"]), row.get("id")
            if "end" not in row:  # a line from before starts were written
                call = _call(at, row)
                _close(call, at, {})
                calls.append(call)
            elif not row["end"]:
                call = _call(at, row)
                calls.append(call)
                if isinstance(ident, str):
                    by_id[ident] = call
                else:
                    anonymous.append(call)
            else:
                if isinstance(ident, str):
                    call = by_id.pop(ident, None)
                else:
                    call = next((c for c in anonymous if c["tool"] == row.get("tool")), None)
                    if call is not None:
                        anonymous.remove(call)
                if call is None:  # an end whose start was lost is still a whole call
                    ms = row.get("ms")
                    call = _call(at - (ms / 1000 if isinstance(ms, (int, float)) else 0), row)
                    calls.append(call)
                _close(call, at, row)
    calls.sort(key=lambda c: c["at"])
    return calls


# --- codex: no tool hooks, so its rollout log is the trace -----------------

_CMD_RE = re.compile(r"""\bcmd["']?\s*:\s*(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")""")
_CELL_RE = re.compile(r"Script running with cell ID (\w+)")
_rollouts: dict[str, Path] = {}
_parsed: dict[Path, tuple[int, dict]] = {}


def _thread_id(room: Path) -> str:
    """The codex thread this room's body runs, from its last ``thread.started``."""
    found = ""
    try:
        with open(Path(room) / "port" / "body.log", errors="replace") as handle:
            for line in handle:
                if '"thread.started"' in line:
                    try:
                        found = json.loads(line).get("thread_id") or found
                    except (ValueError, AttributeError):
                        pass
    except OSError:
        pass
    return found if isinstance(found, str) else ""


def _rollout(room: Path) -> Path | None:
    thread = _thread_id(room)
    if not thread or not re.fullmatch(r"[\w-]+", thread):
        return None
    if thread not in _rollouts:
        home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        found = sorted(home.glob(f"sessions/*/*/*/rollout-*-{thread}.jsonl"))
        if not found:
            return None
        _rollouts[thread] = found[-1]
    return _rollouts[thread]


def _stamp(text: object) -> float | None:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _codex_cmd(name: str, given: object) -> str:
    if isinstance(given, str) and name == "exec":
        found = _CMD_RE.search(given)
        if not found:
            return ""
        quoted = found[1] if found[1] is not None else found[2]
        return cmd_of(re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m[1], m[1]), quoted))
    if isinstance(given, str):
        try:
            given = json.loads(given)
        except ValueError:
            return ""
    if isinstance(given, dict):
        command = given.get("cmd") or given.get("command")
        if isinstance(command, list):
            command = " ".join(map(str, command))
        return cmd_of(command)
    return ""


def _codex_step(state: dict, record: dict) -> None:
    payload = record.get("payload")
    at = _stamp(record.get("timestamp"))
    if not isinstance(payload, dict) or at is None:
        return
    kind = payload.get("type")
    if kind == "message" and payload.get("role") == "assistant" and payload.get("phase") == "commentary":
        text = " ".join(b.get("text", "") for b in payload.get("content") or [] if isinstance(b, dict))
        if text.strip():
            state["say"] = _sentence(text)
            state["said"] = True
        return
    ident = payload.get("call_id")
    if not isinstance(ident, str):
        return
    if kind in ("custom_tool_call", "function_call"):
        name = str(payload.get("name") or "")
        given = payload.get("input") if kind == "custom_tool_call" else payload.get("arguments")
        if name == "wait":
            try:
                cell = str(json.loads(given).get("cell_id"))
            except (ValueError, TypeError, AttributeError):
                cell = ""
            running = state["cells"].get(cell)
            if running is not None:  # still the exec's own call: one command, one timer
                running.update(end=None, ms=None)
                state["open"][ident] = running
                return
        call = _call(at, {"tool": name, "cmd": _codex_cmd(name, given)})
        if state.pop("said", False):
            call["say"] = state["say"]
        state["calls"].append(call)
        state["open"][ident] = call
    elif isinstance(kind, str) and kind.endswith("_output"):
        call = state["open"].pop(ident, None)
        if call is None:
            return
        _close(call, at, {})
        cell = _CELL_RE.search(json.dumps(payload.get("output"))[:2000])
        if cell:
            state["cells"][cell[1]] = call
            call.update(end=None, ms=None)


def _codex(room: Path) -> tuple[list[dict], str]:
    """A codex room's calls and its last narration, read on from where the last read stopped."""
    path = _rollout(room)
    if path is None:
        return [], ""
    offset, state = _parsed.get(path, (0, None))
    if state is None:
        state = {"calls": [], "open": {}, "cells": {}, "say": ""}
    try:
        with open(path, "rb") as handle:
            handle.seek(offset)
            chunk = handle.read()
    except OSError:
        return [], ""
    whole = chunk[: chunk.rfind(b"\n") + 1]
    for line in whole.decode(errors="replace").splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            _codex_step(state, record)
    _parsed[path] = (offset + len(whole), state)
    return state["calls"], state["say"]


def _calls(room: Path, tail: int = 0) -> tuple[list[dict], str]:
    calls = _hooked(room, tail)
    if calls:
        return calls, ""
    return _codex(room)


def read(room: Path, since: float = 0.0, limit: int = 500) -> list[dict]:
    """The room's last ``limit`` calls still running or ended after ``since``, oldest first.

    Each is ``{at, end, ms, tool, arg, cmd, say, by, failed}``: ``at`` is its
    start, ``end`` is None while it runs.
    """
    rows = [dict(c) for c in _calls(room)[0] if c["end"] is None or c["end"] > since]
    return rows[-limit:] if limit else rows


def live(room: Path) -> dict:
    """What the body is on now: its open calls, its last line, when it last ended a call, and its pause."""
    calls, said = _calls(room, tail=TAIL * 4)
    if not said:
        try:
            said = _path(room).with_suffix(".say").read_text()
        except OSError:
            said = ""
    ended = [c["end"] for c in calls if c["end"] is not None]
    paused = read_paused_record(Path(room) / "port") or None
    return {"open": [dict(c) for c in calls if c["end"] is None], "say": said,
            "last_end": max(ended) if ended else None, "paused": paused}

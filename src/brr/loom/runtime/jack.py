"""The jack. It reads boundary.md and stdin, never the ledger. It fails open."""

from __future__ import annotations

import json
import time
import traceback
from pathlib import Path

from .home import atomic_write, mint
from .port import Boundary, parse_boundary


def _boundary_text(room: Path) -> str:
    path = room / "port" / "in" / "boundary.md"
    if not path.is_file():
        return ""
    return path.read_text()


def _load(room: Path) -> Boundary:
    text = _boundary_text(room)
    if not text.strip():
        return Boundary.empty()
    return parse_boundary(text)


def _state(room: Path) -> int:
    path = room / "port" / "jack-state.json"
    if not path.is_file():
        return 0
    return int(json.loads(path.read_text()).get("stop_blocks") or 0)


def _set_state(room: Path, count: int) -> None:
    atomic_write(room / "port" / "jack-state.json",
                 json.dumps({"stop_blocks": count}) + "\n")


def _wait(room: Path) -> float:
    path = room / "port" / "wait"
    if not path.is_file():
        return 3600.0
    return float(path.read_text().strip())


def _molt_pending(room: Path) -> bool:
    return (room / "port" / "molt-pending").is_file()


def _shown(room: Path, parsed: Boundary) -> None:
    if not parsed.strand:
        raise ValueError("boundary names owed letters and no strand")
    stem = mint(5)
    ids = " ".join(parsed.ids)
    text = (
        "---\n"
        "kind: shown\n"
        f"id: {parsed.strand}/{stem}\n"
        f"strand: {parsed.strand}\n"
        f"gen: {parsed.gen}\n"
        f"ids: {ids}\n"
        "---\n"
    )
    atomic_write(room / "port" / "out" / f"{stem}.md", text)


def _json(payload: dict) -> str:
    return json.dumps(payload, separators=(",", ":")) + "\n"


def _block(room: Path, text: str, parsed: Boundary) -> str:
    count = _state(room)
    if count >= 3:
        return ""
    _shown(room, parsed)
    _set_state(room, count + 1)
    return _json({"decision": "block", "reason": text})


def _poll(room: Path) -> str:
    deadline = time.monotonic() + _wait(room)
    while True:
        if _molt_pending(room):
            return ""
        text = _boundary_text(room)
        if text.strip():
            parsed = parse_boundary(text)
            if parsed.ids:
                return _block(room, text, parsed)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return ""
        time.sleep(min(0.2, remaining))


def execute(event: str, room: Path, stdin_text: str) -> str:
    """Return the hook's stdout. Raise on trouble; ``run`` turns that into fail-open."""
    del stdin_text  # Claude's hook JSON is not the port.
    room = Path(room)
    if event not in {"start", "post", "stop"}:
        raise ValueError(f"unknown jack event {event!r}")
    if event == "post":
        _set_state(room, 0)
    text = _boundary_text(room)
    parsed = parse_boundary(text) if text.strip() else Boundary.empty()
    if event == "start":
        if not parsed.ids:
            return ""
        _shown(room, parsed)
        return text if text.endswith("\n") else text + "\n"
    if event == "post":
        if not parsed.ids:
            return ""
        _shown(room, parsed)
        return _json({"hookSpecificOutput": {
            "hookEventName": "PostToolUse", "additionalContext": text}})
    if _molt_pending(room):
        return ""
    if parsed.ids:
        return _block(room, text, parsed)
    return _poll(room)


def log_error(room: Path) -> None:
    path = Path(room) / "port" / "jack-errors.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as handle:
            handle.write(traceback.format_exc())
            if not traceback.format_exc().endswith("\n"):
                handle.write("\n")
    except Exception:
        return


def run(event: str, room: Path, stdin_text: str) -> tuple[int, str]:
    """Fail open: exit 0 and no stdout, with the traceback in jack-errors.log."""
    try:
        return 0, execute(event, room, stdin_text)
    except Exception:
        log_error(room)
        return 0, ""

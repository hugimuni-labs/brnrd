"""The jack. It reads boundary.md and stdin, never the ledger. It fails open."""

from __future__ import annotations

import json
import os
import time
import traceback
from pathlib import Path

from .home import atomic_write, home_of_room, mint
from .port import Boundary, parse_boundary


def _boundary_text(room: Path) -> str:
    path = room / "port" / "in" / "boundary.md"
    if not path.is_file():
        return ""
    return path.read_text()


def _state(room: Path) -> dict:
    path = room / "port" / "jack-state.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text())


def _set_state(room: Path, **updates) -> None:
    state = _state(room)
    state.update(updates)
    atomic_write(room / "port" / "jack-state.json",
                 json.dumps(state) + "\n")


# The prompt cache lives an hour from its last read. A hold that wakes at
# 3600 s lands on the edge, and one of three such wakes rewrote 109k tokens;
# at 55 minutes every keepalive is a cache read.
HOLD = 3300.0
# How long one stop hook may run: long enough to sleep through an exhausted
# five-hour window instead of leaving. The body's adapter pins the hook
# timeout from the same number, in BRNRD_STOP_TIMEOUT.
PARK = 6 * 3600.0
MARGIN = 90.0


def _wait(room: Path) -> float:
    path = room / "port" / "wait"
    if not path.is_file():
        return HOLD
    return float(path.read_text().strip())


def _stop_timeout() -> float:
    return float(os.environ.get("BRNRD_STOP_TIMEOUT") or PARK + 60)


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


def _hand() -> bool:
    return os.environ.get("BRNRD_BODY_JACK") == "hand"


def _block(room: Path, text: str, parsed: Boundary) -> str:
    count = int(_state(room).get("stop_blocks") or 0)
    if _hand() and count >= 3:
        return ""
    _shown(room, parsed)
    _set_state(room, stop_blocks=count + 1)
    return _json({"decision": "block", "reason": text})


def _poll(room: Path) -> str:
    from .usage import dry_until
    home = home_of_room(room)
    shell = os.environ.get("BRNRD_BODY_SHELL")
    begun = time.monotonic()
    # Leave before the shell kills this hook: a killed hook ends the body.
    ceiling = begun + max(_stop_timeout() - MARGIN, 0.0)
    deadline = min(begun + _wait(room), ceiling)
    parked = False
    from .lifecycle import read_thread
    thread = "" if _hand() else os.environ.get("BRNRD_THREAD", "")
    opener = str(read_thread(home, thread).get("opened-by", "")) if thread else ""
    while True:
        if _state(room).get("molted"):
            return ""
        text = _boundary_text(room)
        parsed = parse_boundary(text)
        if parsed.settled is not None:
            return ""
        if shell and not _hand():
            until = dry_until(home, shell)
            if until is not None or parked and _refilling(home, shell):
                # Out of fuel: sleep in the hook, letters wait. A turn now
                # would fail, and leaving loses the conversation. A refill
                # past this hook's lifetime is the loom's to wait for.
                if until is not None and (time.monotonic() + until - time.time() > ceiling
                                          or not parsed.ids and opener.startswith("thread:")):
                    return ""
                parked = True
                time.sleep(1.0)
                continue
            if parked:
                # The cache went cold while parked; a keepalive would pay a
                # full rewrite for nothing. Hold on letters until the ceiling.
                parked = False
                if not opener.startswith("thread:"):
                    deadline = ceiling
        if (parsed.ids if _hand() else any(not letter.compact for letter in parsed.letters)):
            return _block(room, text, parsed)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if _hand():
                return ""
            if not parsed.ids and opener.startswith("thread:"):
                return ""
            # Hold again by blocking, never by waiting on in this call: the
            # shell ends a hook that outlives its timeout, and the body would
            # then leave while it still owes. Under the cache's hour this
            # turn is a cache read.
            return _json({"decision": "block", "reason": "cache keepalive, nothing new arrived: stop, unless the boundary"
                          " below shows work you already owed\n" + text})
        time.sleep(min(0.2, remaining))


def _refilling(home, shell: str) -> bool:
    """Parked, and the gauge has no fresh reading yet: keep waiting."""
    from .usage import reading
    return reading(home, shell) is None


_TAINT_TOOLS = {"WebFetch", "WebSearch"}


def _note_tool(room: Path, stdin_text: str) -> None:
    """A web tool taints. Read and Bash do not. A failed write raises.

    Bash can reach anything. Tainting every shell call would taint every
    strand, which is the same as having no labels. The sandbox owns that
    reach. The failure is logged by ``run``; a non-empty jack error log
    taints the strand, so the label fails closed while the work stays open.
    """
    if not stdin_text or not stdin_text.strip():
        return
    try:
        payload = json.loads(stdin_text)
    except json.JSONDecodeError:
        return
    if not isinstance(payload, dict):
        return
    tool = payload.get("tool_name")
    if tool not in _TAINT_TOOLS:
        return
    stem = mint(5)
    atomic_write(
        room / "port" / "out" / f"taint-{stem}.json",
        json.dumps({"tool": tool}, separators=(",", ":")) + "\n",
    )


# Every tool call re-reads the whole context. One body held ~180k for 441
# calls in a day, most of a five-hour window, and could not see it.
WEIGHT_STEP = 50_000


def _weight(room: Path, stdin_text: str, state: dict) -> str:
    """Once per 50k band: this body's context against what it booted with."""
    try:
        path = Path(json.loads(stdin_text).get("transcript_path") or "")
        size = path.stat().st_size
        with path.open("rb") as handle:
            head = handle.read(400_000).decode(errors="replace").splitlines()
            handle.seek(max(size - 200_000, 0))
            tail = handle.read().decode(errors="replace").splitlines()
    except (OSError, ValueError, AttributeError, TypeError):
        return ""
    boot, now = _context(head), _context(reversed(tail))
    band = now // WEIGHT_STEP
    if band < 2 or band == state.get("weight_band"):
        return ""
    _set_state(room, weight_band=band)
    return (f"context: {now // 1000}k tokens, re-read on every call; this body booted at "
            f"{boot // 1000}k. Molt at the next handover point.")


def _context(lines) -> int:
    for line in lines:
        try:
            usage = json.loads(line)["message"]["usage"]
        except (ValueError, KeyError, TypeError):
            continue
        return sum(int(usage.get(key) or 0) for key in
                   ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    return 0


def execute(event: str, room: Path, stdin_text: str) -> str:
    """Return the hook's stdout. Raise on trouble; ``run`` turns that into fail-open."""
    room = Path(room)
    if event not in {"start", "post", "stop"}:
        raise ValueError(f"unknown jack event {event!r}")
    if event == "post":
        _set_state(room, stop_blocks=0)
        _note_tool(room, stdin_text)
    text = _boundary_text(room)
    parsed = parse_boundary(text)
    opened = [[line.split()[1], line.split()[2], line.rpartition(" · owed ")[2]]
              for line in text.splitlines() if line.startswith("opened: ")]
    plan = next((line.partition(" · plan ")[2] for line in text.splitlines()
                 if line.startswith("status:")), "")
    if event == "start":
        if not parsed.ids and not opened and not plan:
            return ""
        if parsed.ids:
            _shown(room, parsed)
        _set_state(room, opened=opened, plan=plan)
        return text if text.endswith("\n") else text + "\n"
    if event == "post":
        state = _state(room)
        opened_changed = opened != state.get("opened", [])
        plan_changed = plan != state.get("plan", "")
        settled_changed = parsed.settled is not None and parsed.settled != state.get("settled")
        weight = _weight(room, stdin_text, state)
        if not parsed.ids and not settled_changed and not opened_changed and not plan_changed:
            if not weight:
                return ""
            return _json({"hookSpecificOutput": {
                "hookEventName": "PostToolUse", "additionalContext": weight}})
        if weight:
            text = weight + "\n" + text
        if settled_changed:
            _set_state(room, settled=parsed.settled)
        if parsed.ids:
            _shown(room, parsed)
        _set_state(room, opened=opened, plan=plan)
        return _json({"hookSpecificOutput": {
            "hookEventName": "PostToolUse", "additionalContext": text}})
    if _state(room).get("molted") or parsed.settled is not None:
        return ""
    if parsed.ids and (_hand() or not _state(room).get("stop_blocks") and not _dry(room)):
        return _block(room, text, parsed)
    return _poll(room)


def _dry(room: Path) -> bool:
    from .usage import dry_until
    shell = os.environ.get("BRNRD_BODY_SHELL")
    return bool(shell) and dry_until(home_of_room(room), shell) is not None


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

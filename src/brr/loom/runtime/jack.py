"""The jack. It reads boundary.md and stdin, never the ledger. It fails open."""

from __future__ import annotations

import fnmatch
import json
import os
import runpy
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


def _wait(room: Path) -> float:
    path = room / "port" / "wait"
    if not path.is_file():
        return 3600.0
    return float(path.read_text().strip())


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
    from .usage import below_floor
    home = home_of_room(room)
    # Ask for a reading no older than this wait: the cached one may predate it.
    after = time.time()

    def empty() -> bool:
        shell = os.environ.get("BRNRD_BODY_SHELL")
        return bool(not _hand() and shell and below_floor(home, shell, after))

    if empty():
        return ""
    deadline = time.monotonic() + _wait(room)
    while True:
        if _state(room).get("molted") or empty():
            return ""
        text = _boundary_text(room)
        parsed = parse_boundary(text)
        if parsed.settled is not None:
            return ""
        if (parsed.ids if _hand() else any(not letter.compact for letter in parsed.letters)):
            return _block(room, text, parsed)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if _hand():
                return ""
            if empty():
                return ""
            from .lifecycle import read_thread
            thread = os.environ["BRNRD_THREAD"]
            opener = str(read_thread(home, thread).get("opened-by", ""))
            if not parsed.ids and opener.startswith("thread:"):
                return ""
            # Hold again by blocking, never by waiting on in this call: the
            # shell ends a hook that outlives its timeout, and the body would
            # then leave while it still owes. One turn per wait is the price.
            return _json({"decision": "block", "reason": "nothing new; holding\n" + text})
        time.sleep(min(0.2, remaining))


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


# The keys of a tool call's input that name what it acts on: a file, a
# command, an address, a query, a search. A glob in a rule's on: matches any.
_ARGUMENT_KEYS = ("file_path", "notebook_path", "command", "url", "query", "pattern", "path")
_PATH_KEYS = {"file_path", "notebook_path", "path"}
RULES = "memory/rules"
RULE_GATE = "core/immune.d/30-rules"
RULE_ERRORS = "rule-errors.log"


def _arguments(payload: dict) -> list[str]:
    """What the call acts on, as strings; a path under the call's cwd also as relative."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return []
    cwd = payload.get("cwd")
    values: list[str] = []
    for key in _ARGUMENT_KEYS:
        value = tool_input.get(key)
        if not isinstance(value, str) or not value:
            continue
        values.append(value)
        if key in _PATH_KEYS and isinstance(cwd, str) and cwd and os.path.isabs(value):
            rel = os.path.relpath(value, cwd)
            if rel != "." and not rel.startswith(".."):
                values.append(rel)
    return values


def _matched_on(on: list[str], tool: str, arguments: list[str]) -> str | None:
    """The first ``<Tool> <glob>`` that names this tool (or ``*``) and globs an argument."""
    for item in on:
        name, _, glob = item.strip(" ").partition(" ")
        glob = glob.strip(" ")
        if name not in ("*", tool):
            continue
        if any(fnmatch.fnmatchcase(value, glob) for value in arguments):
            return item
    return None


def _log_rule_error(room: Path, line: str) -> None:
    """A broken rule is the self's own draft, not outside input: its own log, which taints nothing."""
    path = room / "port" / RULE_ERRORS
    try:
        with path.open("a") as handle:
            handle.write(line.rstrip("\n") + "\n")
    except OSError:
        return


def _rule_notes(room: Path, stdin_text: str) -> str:
    """Notes the self left for this act: each matching ``on:`` rule's text, once per body.

    Rules are read from the body's own self checkout through the self's gate
    (core/immune.d/30-rules), so the jack accepts exactly what main accepts.
    A rule that does not parse is logged and skipped. Never a block.
    """
    folder = room / "self" / RULES
    if not folder.is_dir() or not stdin_text or not stdin_text.strip():
        return ""
    try:
        payload = json.loads(stdin_text)
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict) or not isinstance(payload.get("tool_name"), str):
        return ""
    paths = sorted(path for path in folder.iterdir() if path.name != ".gitkeep")
    if not paths:
        return ""
    gate = room / "self" / RULE_GATE
    try:
        parse_rule = runpy.run_path(str(gate), run_name="rule_gate")["parse_rule"]
    except Exception as exc:
        _log_rule_error(room, f"{RULE_GATE}: unusable, no on: rule is read: {exc!r}")
        return ""
    tool, arguments = payload["tool_name"], _arguments(payload)
    shown = list(_state(room).get("rules_shown") or [])
    notes: list[str] = []
    for path in paths:
        rel = f"{RULES}/{path.name}"
        try:
            if path.is_symlink() or not path.is_file() or path.suffix != ".md":
                raise ValueError("not a rule file")
            content = path.read_bytes().decode("utf-8", "surrogateescape")
            values, body = parse_rule(content, path.stem)
        except Exception as exc:
            _log_rule_error(room, f"{rel}: skipped: {exc}")
            continue
        on = values.get("on")
        if not on or path.stem in shown:
            continue
        item = _matched_on(on, tool, arguments)
        if item is None:
            continue
        shown.append(path.stem)
        notes.append(f"A note you left yourself ({rel}, on: {item}):\n{body}")
    if notes:
        _set_state(room, rules_shown=shown)
    return "".join(notes)


def execute(event: str, room: Path, stdin_text: str) -> str:
    """Return the hook's stdout. Raise on trouble; ``run`` turns that into fail-open."""
    room = Path(room)
    if event not in {"start", "post", "stop"}:
        raise ValueError(f"unknown jack event {event!r}")
    notes = ""
    if event == "post":
        _set_state(room, stop_blocks=0)
        _note_tool(room, stdin_text)
        try:
            notes = _rule_notes(room, stdin_text)
        except Exception:
            # Fail open on the rules alone: the letters still go out, and
            # jack-errors.log (which taints) stays for the jack's own faults.
            _log_rule_error(room, traceback.format_exc())
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
        if not parsed.ids and not settled_changed and not opened_changed and not plan_changed:
            if not notes:
                return ""
            return _json({"hookSpecificOutput": {
                "hookEventName": "PostToolUse", "additionalContext": notes}})
        if settled_changed:
            _set_state(room, settled=parsed.settled)
        if parsed.ids:
            _shown(room, parsed)
        _set_state(room, opened=opened, plan=plan)
        context = text if not notes else (text if text.endswith("\n") else text + "\n") + notes
        return _json({"hookSpecificOutput": {
            "hookEventName": "PostToolUse", "additionalContext": context}})
    if _state(room).get("molted") or parsed.settled is not None:
        return ""
    if parsed.ids and (_hand() or not _state(room).get("stop_blocks")):
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

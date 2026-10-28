"""The jack. It reads boundary.md and stdin, and the ledger only through
``reap.left`` for its own room. It fails open."""

from __future__ import annotations

import json
import math
import os
import re
import time
import traceback
from pathlib import Path

from brr.pause import read_paused_record

from .home import atomic_write, home_of_room, mint
from .pausing import describe
from .waits import GRAMMAR
from .port import Boundary, answered, parse_boundary

_HEADER_LINE = re.compile(r"<!-- port (\S+) gen (\d+) · owed[^>]*-->")
_ITEM_LINE = re.compile(r"^- (\S+) · from \S+ · reply \S+")
from . import tooltrace


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


# How long one stop hook may run: long enough to sleep through an exhausted
# five-hour window instead of leaving. The body's adapter pins the hook
# timeout from the same number, in BRNRD_STOP_TIMEOUT.
PARK = 6 * 3600.0
MARGIN = 90.0
#: Written by the loom into a held body's port when more bodies are live than
#: ``max_held`` allows; the stop hook then lets the body leave.
DISPLACED = "displaced"


def _cache_access(stdin_text: str) -> str:
    """Claude reports the effective TTL, including settings/credit overrides.

    Authentication alone cannot tell whether a subscription is using paid
    credits. Read the CLI's own cache-write buckets rather than guessing.
    """
    if os.environ.get('BRNRD_BODY_SHELL') != 'claude':
        return ''
    try:
        path = Path(json.loads(stdin_text)['transcript_path'])
        with path.open('rb') as handle:
            handle.seek(max(path.stat().st_size - 200_000, 0))
            lines = handle.read().decode(errors='replace').splitlines()
        for line in reversed(lines):
            try:
                creation = json.loads(line)['message']['usage']['cache_creation']
                if creation.get('ephemeral_5m_input_tokens', 0) > 0:
                    return '5m'
                if creation.get('ephemeral_1h_input_tokens', 0) > 0:
                    return '1h'
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return ''


def _wait(room: Path, stdin_text: str = '') -> float | None:
    path = room / "port" / "wait"
    if path.is_file():
        return float(path.read_text().strip())
    from .adapters import resolve
    shell = os.environ.get("BRNRD_BODY_SHELL")
    if not shell:
        return None
    try:
        # Absent file: the shell declares no lifetime. A home may override.
        lifetime = float((resolve(home_of_room(room), shell) / "cache").read_text().strip())
    except (OSError, ValueError, OverflowError):
        return None
    if shell == "claude" and _cache_access(stdin_text) == "5m":
        lifetime = 300.0
    # A lifetime too short to leave the safety margin cannot buy a hold.
    return lifetime - 300 if math.isfinite(lifetime) and lifetime > 300 else None


def _stop_timeout() -> float:
    return float(os.environ.get("BRNRD_STOP_TIMEOUT") or PARK + 60)


_AGE = re.compile(r" \d+[smhd](?= ·|$)")


def _ageless(line: str) -> str:
    """A thread line without its ages: an hour passing on an idle child or a PR's push is not news."""
    return _AGE.sub("", line)


def _slot(key: str) -> str:
    if key.startswith("opened: "):
        return " ".join(key.split()[:2])
    return "idle:" if key.startswith("idle: ") else key


def _news(text: str, state: dict, plan_changed: bool) -> tuple[str, list[str], list[str]]:
    """The boundary minus what an earlier boundary already said: (news, said, told).

    The wake shows everything; a post shows a line only when it is new, the
    status line only when the plan moved, and an owed letter only the first
    time. A live boundary that repeated 54 rows and every owed letter cost
    ~1.7k tokens a call (wake-vision). The port header names the letters
    shown here; one ``owed:`` line names the ones still owed from before.
    """
    header = _HEADER_LINE.search(text)
    if header is None:
        return "", [], []
    units: list[list[str]] = []
    for line in text[header.end():].splitlines():
        if line.startswith("  ") and units:
            units[-1].append(line)
        elif line.strip() and line != "owed:":
            units.append([line])
    before = set(state.get("said", []))
    known = set(state.get("told", []))
    said: list[str] = []
    told: list[str] = []
    body: list[str] = []
    letters: list[str] = []
    skipped: list[str] = []
    for unit in units:
        # Past its allocation the warning comes at every post (``over``), so it is never said nor gone.
        if unit[0].startswith("budget: "):
            continue
        item = _ITEM_LINE.match(unit[0])
        if item:
            told.append(item.group(1))
            if item.group(1) in known:
                skipped.append(item.group(1))
            else:
                letters.extend(unit)
            continue
        status = unit[0].startswith("status:")
        key = ("status: · plan " + unit[0].partition(" · plan ")[2] if status
               else _ageless(unit[0]) if unit[0].startswith("opened: ") else "\n".join(unit))
        said.append(key)
        if (plan_changed if status else key not in before):
            body.extend(unit)
    # A row that left (a child settled, a line cleared) is news too; the status line moves, it never leaves.
    # A thread line that changed is said anew, not gone: its slot (``opened: <thread>``, ``idle:``) is still here.
    slots = {_slot(key) for key in said}
    body.extend("gone: " + key.split("\n")[0] for key in state.get("said", [])
                if key not in said and not key.startswith("status:") and _slot(key) not in slots)
    if not body and not letters:
        return "", said, told
    shown = [item.group(1) for line in letters if (item := _ITEM_LINE.match(line))]
    owed = (["owed: " + " ".join(skipped) + " (shown before)"] if skipped else []) \
        + (["owed:"] if letters else [])
    head = f"<!-- port {header.group(1)} gen {header.group(2)} · owed {' '.join(shown)} -->"
    return "\n".join([head, *body, *owed, *letters, ""]), said, told


def _stop_news(room: Path, text: str, parsed: Boundary) -> str:
    """What a stop shows: the boundary minus what the wake or a call already said.

    A stop that blocked, a letter that woke a held body and a keepalive each
    repeated the whole boundary (~3 KB with 13 thread rows) though the body
    had read all of it; across held strands that was most of the jack's
    spend. A body that has seen no boundary yet still gets it whole. Letters
    already shown come back as their ids on one ``owed:`` line.
    """
    state = _state(room)
    if "said" not in state:
        return text
    plan = next((line.partition(" · plan ")[2] for line in text.splitlines()
                 if line.startswith("status:")), "")
    news, said, told = _news(text, state, plan != state.get("plan", ""))
    _set_state(room, said=said, told=told, plan=plan)
    header = _HEADER_LINE.search(text)
    if not news and parsed.ids and header:
        news = (f"<!-- port {header.group(1)} gen {header.group(2)} · owed  -->\n"
                f"owed: {' '.join(parsed.ids)} (shown before)\n")
    return news


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


LEFT_EVERY = 60.0


def _left(room: Path, fresh: bool) -> str:
    """``left in room:`` from ``reap.left`` on this room only, or nothing when it is clean.

    It reads the ledger and fetches, so a post reuses a read younger than
    ``LEFT_EVERY``; a stop reads again. A failure is logged and shows nothing:
    a broken check must not hold every stop.
    """
    state = _state(room)
    if fresh or time.time() - float(state.get("left_at") or 0) >= LEFT_EVERY:
        from .reap import left
        try:
            lines = left(home_of_room(room), room.name)
        except Exception:
            try:
                with (room / "port" / "left-errors.log").open("a") as handle:
                    handle.write(traceback.format_exc())
            except OSError:
                pass
            lines = []
        _set_state(room, left=lines, left_at=time.time())
    else:
        lines = list(state.get("left") or ())
    return "".join(["left in room: only this room holds these; the stop waits until each is cleared\n",
                    *(f"- {line}\n" for line in lines)]) if lines else ""


def _json(payload: dict) -> str:
    return json.dumps(payload, separators=(",", ":")) + "\n"


def _hand() -> bool:
    return os.environ.get("BRNRD_BODY_JACK") == "hand"


def _block(room: Path, text: str, parsed: Boundary) -> str:
    count = int(_state(room).get("stop_blocks") or 0)
    if _hand() and count >= 3:
        return ""
    if parsed.ids:
        _shown(room, parsed)
    _working(room, stop_blocks=count + 1)
    return _json({"decision": "block", "reason": text})


def _working(room: Path, **state) -> None:
    """A stop that blocks gives the body work: it is no longer held or displaced."""
    _set_state(room, held_at=None, **state)
    (room / "port" / DISPLACED).unlink(missing_ok=True)


def _poll(room: Path, stdin_text: str = '') -> str:
    from .usage import dry_until
    home = home_of_room(room)
    shell = os.environ.get("BRNRD_BODY_SHELL")
    begun = time.monotonic()
    # Leave before the shell kills this hook: a killed hook ends the body.
    ceiling = begun + max(_stop_timeout() - MARGIN, 0.0)
    wait = _wait(room, stdin_text)
    deadline = ceiling if wait is None else min(begun + wait, ceiling)
    parked = False
    # Held since the first stop with nothing to do; a keepalive turn keeps the
    # mark, real work clears it. The loom displaces the longest held first.
    if not _state(room).get("held_at"):
        _set_state(room, held_at=time.time())
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
                if until is not None and time.monotonic() + until - time.time() > ceiling:
                    return ""
                parked = True
                time.sleep(1.0)
                continue
            if parked:
                # The cache went cold while parked; a keepalive would pay a
                # full rewrite for nothing. Hold on letters until the ceiling.
                parked = False
                deadline = ceiling
        done = answered(room) if parsed.ids else set()
        if (set(parsed.ids) - done if _hand() else
                any(not letter.compact and letter.id not in done for letter in parsed.letters)):
            return _block(room, _stop_news(room, text, parsed), parsed)
        if (room / "port" / DISPLACED).exists():
            # Over the loom's max_held: leave warm memory for a body with work.
            # The next letter to this thread starts a fresh one.
            return ""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if _hand():
                return ""
            # Hold again by blocking, never by waiting on in this call: the
            # shell ends a hook that outlives its timeout, and the body would
            # then leave while it still owes. With no cache lifetime this
            # happens only at the hook ceiling.
            return _json({"decision": "block", "reason": "cache keepalive, nothing new arrived: stop, unless the boundary"
                          " below shows work you already owed\n" + _stop_news(room, text, parsed)})
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


# A delegate's report in a letter head; the whole report stays in the room.
DELEGATE_HEAD = 800


def _delegate(room: Path, stdin_text: str) -> None:
    """A background delegate finished: a letter from this strand to itself.

    The harness's own task notification cannot cross a held stop hook, so the
    report waited for some other letter (mock 3: done 20:45, read 21:23). The
    letter carries ``at`` now: a thread writes to itself only on a timer, and
    the loom makes it due in the tick that drains it. A foreground delegate
    already answered in its tool result and is not in ``background_tasks``.
    """
    payload = json.loads(stdin_text)
    agent = str(payload.get("agent_id") or "")
    if not agent or agent not in {str(task.get("id")) for task in payload.get("background_tasks") or ()
                                  if isinstance(task, dict)}:
        return
    report = str(payload.get("last_assistant_message") or "").strip() or "(no final message)"
    path = room / "delegates" / f"{agent}.md"
    atomic_write(path, report + "\n")
    head = report if len(report) <= DELEGATE_HEAD else report[:DELEGATE_HEAD - 1].rstrip() + "…"
    strand, stem = room.name, mint(5)
    thread = os.environ["BRNRD_THREAD"]
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    atomic_write(room / "port" / "out" / f"{stem}.md", (
        "---\n"
        "kind: letter\n"
        f"id: {strand}/{stem}\n"
        f"to: thread:{thread}\n"
        f"from: {strand}\n"
        f"at: {at}\n"
        "---\n"
        f"delegate {payload.get('agent_type') or 'agent'} {agent} finished; full report: {path}\n"
        f"{head}\n"
    ))


def _delegate_call(stdin_text: str) -> bool:
    """A delegate's tool call runs the room's hooks too (2.1.303: ``agent_id``).

    The boundary is the body's: a delegate that sees it pays ~1 KB a call
    and may act on it (a haiku delegate reported it as injected instructions),
    and marking letters shown there hides them from the body. Only the taint
    counts: a delegate's web fetch is the strand's.
    """
    try:
        payload = json.loads(stdin_text) if stdin_text and stdin_text.strip() else {}
    except json.JSONDecodeError:
        return False
    return isinstance(payload, dict) and bool(payload.get("agent_id"))


def _delegates(room: Path, stdin_text: str, seen: bool) -> None:
    """A delegate mid-work, one file each, its mtime the last call: a pause would stop its call.

    One file per delegate, not a key in jack-state: delegates' hooks run
    alongside the body's, and that file has one writer at a time.
    """
    agent = str(json.loads(stdin_text).get("agent_id") or "")
    if not agent or "/" in agent:
        return
    path = room / "port" / "live-delegates" / agent
    if seen:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    else:
        path.unlink(missing_ok=True)


def _hook_event(stdin_text: str) -> str:
    """A failed call (a paused one too) runs PostToolUseFailure, answered in its own name."""
    try:
        payload = json.loads(stdin_text) if stdin_text and stdin_text.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    failed = isinstance(payload, dict) and payload.get("hook_event_name") == "PostToolUseFailure"
    return "PostToolUseFailure" if failed else "PostToolUse"


def _guards(room: Path):
    """Guards fail apart from the jack: a broken guard must not cost the
    boundary, nor taint the strand through jack-errors.log."""
    from .guards import for_room
    try:
        return for_room(room, os.environ.get("BRNRD_THREAD", ""))
    except Exception:
        try:
            with (room / "port" / "guard-errors.log").open("a") as handle:
                handle.write(traceback.format_exc())
        except OSError:
            pass
        return [], [f"all: guards unreadable, see {room / 'port' / 'guard-errors.log'}"]


def _guard_warnings(room: Path, at: str, stdin_text: str) -> tuple[str, bool]:
    """A call that trips a guard hears the guard's own words, and whether a
    ``refuse`` match among them denies it. Warn unless the guard says refuse."""
    from .guards import trips
    try:
        payload = json.loads(stdin_text) if stdin_text and stdin_text.strip() else {}
    except json.JSONDecodeError:
        return "", False
    if not isinstance(payload, dict) or not payload.get("tool_name"):
        return "", False
    guards, _errors = _guards(room)
    hits = trips(guards, room, at, payload)
    return "\n".join(label for label, _ in hits), any(refuse for _, refuse in hits)


def _guard_miss(room: Path) -> str:
    """A broken path guard, as one section of the stop's block."""
    from .guards import violations
    guards, _errors = _guards(room)
    lines = violations([g for g in guards if g.paths], room)
    return ("a path guard is broken; undo the change, or ask the person who set it"
            " (send-self, publish and settle refuse until then):\n" + "\n".join(lines)) if lines else ""


def _kept_miss(room: Path) -> str:
    """Uncommitted writes in a kept folder since this body began. A check that cannot run never costs the stop."""
    from .library import written_since
    try:
        since = (room / "port" / "library.json").stat().st_mtime
        lines = written_since(room, since)
    except Exception:
        return ""
    return ((
        "uncommitted changes in a kept folder since this body began. Commit what you wrote, "
        "only those paths: `python -m brr.loom.runtime lib commit <name> -m \"<what and why>\" <path>...`. "
        "Leave what is the person's; the loom reports it after an hour:\n") + "\n".join(lines)) if lines else ""


def _known_miss(room: Path) -> str:
    """A known folder this body touched: a nudge to tidy or note it, once per folder (library.known_nudge)."""
    from .library import known_nudge
    try:
        return known_nudge(room)
    except Exception:
        return ""


def _touch(room: Path, stdin_text: str) -> None:
    from .library import touch
    try:
        touch(room, stdin_text)
    except Exception:  # noqa: BLE001 - a lost touch costs a nudge, never the hook
        return


def _lib_changes(room: Path) -> str:
    from .library import changes
    try:
        return changes(room)
    except Exception:  # noqa: BLE001 - a missed line costs a notice, never the hook
        return ""


def execute(event: str, room: Path, stdin_text: str) -> str:
    """Return the hook's stdout. Raise on trouble; ``run`` turns that into fail-open."""
    room = Path(room)
    if event not in {"start", "pre", "post", "stop", "subagent-stop"}:
        raise ValueError(f"unknown jack event {event!r}")
    if event == "subagent-stop":
        _delegates(room, stdin_text, False)
        _delegate(room, stdin_text)
        return ""
    if event == "pre":
        # Not a boundary: letters wait for the call's result. A delegate's
        # call is guarded too, since the act is the strand's.
        said, refuse = _guard_warnings(room, "pre", stdin_text)
        tooltrace.note(room, stdin_text, "pre")
        if refuse:
            # A refused call gets no post hook; its end is written here.
            tooltrace.note(room, stdin_text, "refused")
            return _json({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": "refused by a guard:\n" + said}})
        return _json({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "additionalContext": said}}) if said else ""
    guarded = _guard_warnings(room, "post", stdin_text)[0] if event == "post" else ""
    if event == "post":
        tooltrace.note(room, stdin_text, "post")
        _touch(room, stdin_text)
        guarded = "\n".join(filter(None, (guarded, _lib_changes(room))))
        _note_tool(room, stdin_text)
        if _delegate_call(stdin_text):
            _delegates(room, stdin_text, True)
            return _json({"hookSpecificOutput": {"hookEventName": _hook_event(stdin_text),
                                                 "additionalContext": guarded}}) if guarded else ""
        # A call begun before this is not the body's current one (pausing.py):
        # a background call returns at once, so a boundary always follows it.
        _set_state(room, stop_blocks=0, boundary_at=time.time(), held_at=None)
        (room / "port" / DISPLACED).unlink(missing_ok=True)
    elif event == "start":
        _set_state(room, boundary_at=time.time())
    text = _boundary_text(room)
    parsed = parse_boundary(text)
    opened = [[line.split()[1], line.split()[2], _ageless(line.rpartition(" · owed ")[2])]
              for line in text.splitlines() if line.startswith("opened: ")]
    plan = next((line.partition(" · plan ")[2] for line in text.splitlines()
                 if line.startswith("status:")), "")
    face = next((line for line in text.splitlines() if line.startswith("face? ")), "")
    moved = next((line for line in text.splitlines() if line.startswith("moved: ")), "")
    if event == "start":
        broken = _guards(room)[1]
        if broken:
            text = "".join(f"guards: skipped {line}\n" for line in broken) + text
        left = _left(room, True)
        text = left + text
        if not parsed.ids and not opened and not plan and not broken and not left:
            return ""
        if parsed.ids:
            _shown(room, parsed)
        _set_state(room, opened=opened, plan=plan, face=face, moved=moved,
                   **dict(zip(("said", "told"), _news(text, {}, True)[1:])))
        return text if text.endswith("\n") else text + "\n"
    if event == "post":
        state = _state(room)
        opened_changed = opened != state.get("opened", [])
        plan_changed = plan != state.get("plan", "")
        settled_changed = parsed.settled is not None and parsed.settled != state.get("settled")
        # A new proposal is shown once, at the next post; it never wakes a held body.
        face_changed = bool(face) and face != state.get("face", "")
        # The loom's code moved under a seated body: said once, at the next post.
        moved_changed = bool(moved) and moved != state.get("moved", "")
        weight = _weight(room, stdin_text, state)
        paused = describe(read_paused_record(room / "port"))
        hook = _hook_event(stdin_text)
        # Past its allocation a strand is told at every boundary, not once.
        over = next((line for line in text.splitlines() if line.startswith("budget: ")), "")
        left = _left(room, False).rstrip("\n")
        # A left in room is news when it changes; the stop still holds on it every time.
        if left == state.get("left_said", ""):
            left = ""
        else:
            _set_state(room, left_said=left)
        news, said, told = _news(text, state, plan_changed)
        _set_state(room, said=said, told=told)
        if not parsed.ids and not settled_changed and not opened_changed and not plan_changed \
                and not face_changed and not moved_changed:
            news = ""
        if not news:
            if parsed.ids:
                _shown(room, parsed)
            _set_state(room, opened=opened, plan=plan, face=face, moved=moved)
            if parsed.settled is not None:
                _set_state(room, settled=parsed.settled)
            if not weight and not paused and not over and not guarded and not left:
                return ""
            return _json({"hookSpecificOutput": {"hookEventName": hook, "additionalContext": "\n".join(
                filter(None, (guarded, paused, weight, over, left)))}})
        text = news
        if over:
            text = over + "\n" + text
        if left:
            text = left + "\n" + text
        if guarded:
            text = guarded + "\n" + text
        if weight:
            text = weight + "\n" + text
        if paused:
            text = paused + "\n" + text
        if settled_changed:
            _set_state(room, settled=parsed.settled)
        if parsed.ids:
            _shown(room, parsed)
        _set_state(room, opened=opened, plan=plan, face=face, moved=moved)
        return _json({"hookSpecificOutput": {
            "hookEventName": hook, "additionalContext": text}})
    if _state(room).get("molted") or parsed.settled is not None:
        return ""
    refused = _stop_block(room, text, plan)
    if refused:
        return refused
    return _poll(room, stdin_text)


def _letter_miss(room: Path, parsed: Boundary, done: set[str]) -> str:
    thread = os.environ.get("BRNRD_THREAD") or "<thread>"
    rows = []
    for letter in parsed.letters:
        if letter.id in done:
            continue
        rows.append(f"- {letter.id}: brnrd runtime send --re {letter.id} --to thread:{thread} --note \"<what you did>\""
                    if letter.reply == "-" else
                    f"- {letter.id}: brnrd runtime send --re {letter.id} --to {letter.reply} \"<answer>\"")
    return "owed letters, each with its reply (a reply or --note already sent counts, drained or not):\n" + "\n".join(rows) if rows else ""


STOP = "the stop holds on {n}; do each, then stop. This list comes back whole at every stop until it is empty."


def _stop_block(room: Path, text: str, plan: str) -> str:
    """Everything the stop holds on, in one block (stop-list).

    Each miss is a section with its exact command: owed letters, a left in
    room, wishes whose left moved, an end not yet said, a broken guard, a
    kept folder's writes, a known folder touched. The block comes back whole at
    every stop after a call until it is empty. A stop again with no call and
    the same misses passes: a body that has read the list and cannot clear it
    is never trapped; the wishes asked about become ``left.asked`` facts then,
    and the wake shows them stale.
    """
    if _dry(room):
        # A turn without fuel fails; the hold sleeps instead.
        return ""
    text, lefts = _settled_render(room, text)
    parsed = parse_boundary(text)
    if parsed.settled is not None:
        return ""
    sections = [
        _letter_miss(room, parsed, answered(room)),
        _left(room, True).rstrip("\n"),
        LEFT.format(named=" · ".join(f"{ident} ({why})" for ident, why in lefts)) if lefts else "",
        _end_miss(room, plan),
        _guard_miss(room),
        _kept_miss(room),
    ]
    sections = [section for section in sections if section]
    state = _state(room)
    count = int(state.get("stop_blocks") or 0)
    seen = "\n\n".join(sections)
    if (count >= 3) if _hand() else (count and seen == state.get("stop_said")):
        # Stopped again with nothing done since the same list: let it through.
        _asked(room, parsed, [ident for ident, _ in lefts])
        return ""
    known = _known_miss(room)
    if not sections and not known:
        return ""
    sections = [*sections, known] if known else sections
    if parsed.ids:
        _shown(room, parsed)
    _working(room, stop_blocks=count + 1, stop_said=seen)
    head = STOP.format(n=f"{len(sections)} thing{'s' * (len(sections) > 1)}")
    news = _stop_news(room, text, parsed) if parsed.ids else ""
    return _json({"decision": "block", "reason": "\n\n".join([head, *sections, *([news] if news else [])])})


END = """work is left on this thread (plan {plan}) and this body has not said how it ends. Do one:
- a result: brnrd runtime wish <w-id> --result <kind>:<ref> --line "<what it is>" [--deliver]
- an ask to a person: brnrd runtime ask "<question>" --for <w-id> --rec "<your call>"
- a park with its reason: brnrd runtime wish <w-id> --read "waiting: <on what>" --wait <condition>
- done: brnrd runtime settle {thread} --why "<what it became>"
`brnrd runtime wishes` lists the w-ids, and `--wait` takes {grammar}.
The jack then holds you, and the loom's letter wakes you when the wait comes true; never a sleep loop."""


LEFT = """what is left on a wish moved since it was last said: {named}
Restate each one's whole list:
- brnrd runtime wish <w-id> --left "<step>" --left "<step>"   (--left same when nothing moved)
- or a park: wish <w-id> --read "waiting: <on what>"; a delivery: --result <kind>:<ref> --line "<what>" --deliver"""

# Long enough for the loom to render a fact written just before the stop.
LEFT_GRACE = 3.0


def _settled_render(room: Path, text: str) -> tuple[str, list[tuple[str, str]]]:
    """The boundary once the loom has rendered this strand's last facts, and the
    wishes its ``left?`` line still owes. A loom too slow to render within the
    grace cannot be trusted to ask: the left fails open, the wake shows it stale."""
    if _hand() or not text or "\nleft? " not in "\n" + text:
        return text, []
    stop = time.monotonic() + LEFT_GRACE
    while _rendered_before_ledger(room):
        if time.monotonic() >= stop:
            return _boundary_text(room), []
        time.sleep(0.2)
    text = _boundary_text(room)
    line = next((x for x in text.splitlines() if x.startswith("left? ")), "")
    pending = set()
    for path in (room / "port" / "out").glob("asked-*.md"):
        for listed in re.findall(r"^wishes: (.*)$", path.read_text(), re.M):
            pending.update(listed.split())
    return text, [(ident, why) for ident, why in re.findall(r"(w-[0-9a-z]+) \(([^)]*)\)", line)
                  if ident not in pending]


def _asked(room: Path, parsed: Boundary, owed: list[str]) -> None:
    """A stop let through with lefts still owed: a ``left.asked`` fact through the
    port, after which the line drops them and the wake shows them stale."""
    if not owed:
        return
    stem = mint(5)
    atomic_write(room / "port" / "out" / f"asked-{stem}.md", (
        "---\n"
        "kind: left-asked\n"
        f"id: {parsed.strand or room.name}/{stem}\n"
        f"wishes: {' '.join(owed)}\n"
        "---\n"))


def _rendered_before_ledger(room: Path) -> bool:
    """A fact this strand wrote after the boundary was rendered (a left, a park)
    has not reached it yet. A stat, never a read of the ledger."""
    try:
        rendered = (room / "port" / "in" / "boundary.md").stat().st_mtime
        home = home_of_room(room)
    except (OSError, ValueError):
        return False
    files = (home.root / "ledger" / "facts").glob(f"*/{room.name}.jsonl")
    return any(path.stat().st_mtime > rendered for path in files)


def declare(room: Path, how: str) -> None:
    """A verb that says how this body ends marks its room; the next start clears it."""
    _set_state(room, declared=how)


def _end_miss(room: Path, plan: str) -> str:
    """Wishes still open and this body has not declared its end. The plan counts
    wish stamps, never Plan rows."""
    done, _, total = plan.partition(" ")[0].partition("/")
    if not (done.isdigit() and total.isdigit() and int(done) < int(total)) or _state(room).get("declared"):
        return ""
    thread = os.environ.get("BRNRD_THREAD") or "<thread>"
    return END.format(plan=f"{done}/{total}", thread=thread, grammar=GRAMMAR)


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

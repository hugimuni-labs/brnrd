"""The jack. It reads boundary.md and stdin, never the ledger. It fails open."""

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
from .port import Boundary, parse_boundary
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
        if (parsed.ids if _hand() else any(not letter.compact for letter in parsed.letters)):
            return _block(room, text, parsed)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if _hand():
                return ""
            # Hold again by blocking, never by waiting on in this call: the
            # shell ends a hook that outlives its timeout, and the body would
            # then leave while it still owes. With no cache lifetime this
            # happens only at the hook ceiling.
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


def _guard_stop(room: Path) -> str:
    """A broken path guard blocks the stop once per distinct set of breaks.

    The same breaks a second time let the stop through: a body that has read
    the guard and cannot fix it is not trapped in a loop. send-self, publish
    and settle still refuse.
    """
    from .guards import violations
    guards, _errors = _guards(room)
    lines = violations([g for g in guards if g.paths], room)
    if not lines:
        if _state(room).get("guard_stop"):
            _set_state(room, guard_stop="")
        return ""
    seen = "\n".join(lines)
    if _state(room).get("guard_stop") == seen:
        return ""
    _set_state(room, guard_stop=seen)
    return _json({"decision": "block", "reason": "a path guard is broken; undo the change, or ask the person"
                  " who set it (send-self, publish and settle refuse until then):\n" + seen})


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
        _note_tool(room, stdin_text)
        if _delegate_call(stdin_text):
            _delegates(room, stdin_text, True)
            return _json({"hookSpecificOutput": {"hookEventName": _hook_event(stdin_text),
                                                 "additionalContext": guarded}}) if guarded else ""
        # A call begun before this is not the body's current one (pausing.py):
        # a background call returns at once, so a boundary always follows it.
        _set_state(room, stop_blocks=0, boundary_at=time.time())
    elif event == "start":
        _set_state(room, boundary_at=time.time())
    text = _boundary_text(room)
    parsed = parse_boundary(text)
    opened = [[line.split()[1], line.split()[2], line.rpartition(" · owed ")[2]]
              for line in text.splitlines() if line.startswith("opened: ")]
    plan = next((line.partition(" · plan ")[2] for line in text.splitlines()
                 if line.startswith("status:")), "")
    face = next((line for line in text.splitlines() if line.startswith("face? ")), "")
    moved = next((line for line in text.splitlines() if line.startswith("moved: ")), "")
    if event == "start":
        broken = _guards(room)[1]
        if broken:
            text = "".join(f"guards: skipped {line}\n" for line in broken) + text
        if not parsed.ids and not opened and not plan and not broken:
            return ""
        if parsed.ids:
            _shown(room, parsed)
        _set_state(room, opened=opened, plan=plan, face=face, moved=moved)
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
        if not parsed.ids and not settled_changed and not opened_changed and not plan_changed \
                and not face_changed and not moved_changed:
            if not weight and not paused and not over and not guarded:
                return ""
            return _json({"hookSpecificOutput": {
                "hookEventName": hook, "additionalContext": "\n".join(filter(None, (guarded, paused, weight, over)))}})
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
    blocked = _guard_stop(room)
    if blocked:
        return blocked
    if parsed.ids and (_hand() or not _state(room).get("stop_blocks") and not _dry(room)):
        return _block(room, text, parsed)
    refused = _left_owed(room, text) or _undeclared(room, plan)
    if refused:
        return refused
    return _poll(room, stdin_text)


END = """work is left on this thread (plan {plan}) and this body has not said how it ends. Do one, then stop:
- a result: brnrd runtime wish <w-id> --result <kind>:<ref> --line "<what it is>" [--deliver]
- an ask to a person: brnrd runtime ask "<question>" --for <w-id> --rec "<your call>"
- a park with its reason: brnrd runtime wish <w-id> --read "waiting: <on what>"
- done: brnrd runtime settle {thread} --why "<what it became>"
`brnrd runtime wishes` lists the w-ids. The jack then holds you while letters may come."""


LEFT = """what is left on a wish moved since it was last said: {named}
Restate each one's whole list, then stop:
- brnrd runtime wish <w-id> --left "<step>" --left "<step>"   (--left same when nothing moved)
- or a park: wish <w-id> --read "waiting: <on what>"; a delivery: --result <kind>:<ref> --line "<what>" --deliver
This is asked once per wish: the next stop passes, and the wake shows an unanswered left as stale."""

# Long enough for the loom to render a left written just before the stop.
LEFT_GRACE = 3.0


def _left_owed(room: Path, text: str) -> str:
    """Refuse a stop while the boundary names wishes whose left moved. Nothing is
    remembered here: the loom derives the line from the ledger, and a refusal
    becomes a ``left.asked`` fact through the port, after which the line drops it."""
    if _hand() or _dry(room) or "\nleft? " not in "\n" + text:
        return ""
    stop = time.monotonic() + LEFT_GRACE
    while _rendered_before_ledger(room):
        if time.monotonic() >= stop:
            # A loom too slow to render cannot be trusted to ask: fail open, and
            # the next wake shows the left as stale.
            return ""
        time.sleep(0.2)
    text = _boundary_text(room)
    line = next((x for x in text.splitlines() if x.startswith("left? ")), "")
    pending = set()
    for path in (room / "port" / "out").glob("asked-*.md"):
        for listed in re.findall(r"^wishes: (.*)$", path.read_text(), re.M):
            pending.update(listed.split())
    owed = [ident for ident in re.findall(r"(w-[0-9a-z]+) \(", line) if ident not in pending]
    if not owed:
        return ""
    parsed = parse_boundary(text)
    stem = mint(5)
    atomic_write(room / "port" / "out" / f"asked-{stem}.md", (
        "---\n"
        "kind: left-asked\n"
        f"id: {parsed.strand or room.name}/{stem}\n"
        f"wishes: {' '.join(owed)}\n"
        "---\n"))
    return _json({"decision": "block", "reason": LEFT.format(named=line.removeprefix("left? "))})


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


def _undeclared(room: Path, plan: str) -> str:
    """The first stop with wishes still open is refused once unless this body
    declared its end. The plan counts wish stamps, never Plan rows."""
    done, _, total = plan.partition(" ")[0].partition("/")
    if _hand() or not (done.isdigit() and total.isdigit() and int(done) < int(total)) or _dry(room):
        return ""
    state = _state(room)
    if state.get("declared") or state.get("end_refused"):
        return ""
    _set_state(room, end_refused=True)
    thread = os.environ.get("BRNRD_THREAD") or "<thread>"
    return _json({"decision": "block", "reason": END.format(plan=f"{done}/{total}", thread=thread)})


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

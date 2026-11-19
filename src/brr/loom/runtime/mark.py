"""The mark: what a call that reaches outside the room says about itself (threads/judged-acts).

What reaches outside is not read from a command's text: the engine's sandbox
(the wall) holds a shell call to the room, ``$TMPDIR`` and caches, with no
network, and whatever it refuses is outside. A call crosses only when the pre-tool
hook lifts the wall for it, and the hook lifts it for a mark:

    BRNRD_SAY="reach · what · for · back" <command>

reach is ``look``, ``change`` or ``final``; what is the thing touched; for is
the letter or standing sentence it serves; back is how it is put back. Only
reach is always typed: a look's what is the command and its back is nothing, and
for defaults to the letters the body owes at the call (recorded as inferred). A
change names its thing and its way back; a final act has none, which is what
makes it final. The same variable is the three doors' sentence (audit.passage),
so the door process reads the string the hook read.

The loom's own verbs cross without a mark: they write the home by design, and
the ones that leave (a send to a person, a publish) judge themselves at their
door (:func:`door_refusal`). A final mark needs an owner letter or a dashboard
confirmation (:func:`rule`). Reads are never acts. A file tool writing outside
the room is refused; web tools can only look and mark themselves; an MCP tool
takes a one-shot mark from ``brnrd runtime mark`` given just before it.

Nothing here is enforced unless the body runs behind a wall (``BRNRD_WALL``,
set by a body's run script when it wrote the sandbox): an engine with no wall
cannot tell a crossing from staying in, so there a mark is recorded when given
and never required.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import asdict, dataclass, replace
from pathlib import Path

SAY = "BRNRD_SAY"
WALL = "BRNRD_WALL"
MARKS = "BRNRD_MARKS"
REACHES = ("look", "change", "final")
_FINAL = re.compile(r"cannot\s+be\s+undone|irreversib|undoable|can'?t\s+be\s+undone", re.I)
_SEP = re.compile(r"\s*[·|]\s*")
_NONE = {"", "-", "none", "no", "nothing", "n/a"}
FILE_WRITES = {"Edit", "MultiEdit", "Write", "NotebookEdit"}
LOOKS = {"WebFetch": "url", "WebSearch": "query"}
ONE_SHOT = "mark.json"

FORM = ('mark it: prefix the command with BRNRD_SAY="reach · what · for · back": '
        'reach is look, change or final; a look needs only the word (BRNRD_SAY=look); '
        'a change names the thing and how it is put back; a final act names the thing '
        'and the letter it serves')


@dataclass(frozen=True)
class Mark:
    reach: str
    what: str
    for_: str
    back: str
    inferred: tuple[str, ...] = ()

    def record(self, crossed: bool) -> dict:
        data = asdict(self)
        data["for"] = data.pop("for_")
        data["inferred"] = list(self.inferred)
        data["crossed"] = crossed
        return data


def parse(text: str | None, *, what: str = "", owed: tuple[str, ...] = ()) -> tuple[Mark | None, str]:
    """``(mark, "")``, or ``(None, why)`` for a sentence that is no mark. Never raises."""
    if text is None or not text.strip():
        return None, "no mark"
    fields = _SEP.split(text.strip(), maxsplit=3)
    head = fields[0].strip()
    word = head.split()[0].lower() if head.split() else ""
    reach = word if word in REACHES else "final" if _FINAL.search(head) else ""
    if not reach:
        return None, f"a mark opens with its reach (look, change or final), not {head[:40]!r}"
    fields += [""] * (4 - len(fields))
    _, given_what, given_for, back = (f.strip() for f in fields)
    inferred = []
    if not given_what:
        if reach != "look":
            return None, f"a {reach} mark names the thing it touches"
        given_what, inferred = what, inferred + ["what"]
    if not given_for:
        if reach != "final":
            given_for, inferred = " ".join(owed), inferred + ["for"]
    if reach == "change" and back.lower() in _NONE:
        return None, "a change says how it is put back; if it cannot be, it is final"
    if reach == "final" and back.lower() not in _NONE:
        return None, f"a final act has no way back; if {back!r} puts it back, it is a change"
    if reach != "change":
        back = ""
    return Mark(reach, given_what, given_for, back, tuple(inferred)), ""


def program(command: str) -> str:
    """The first program a shell command runs, by name only: what an inferred look records.

    The journal holds digests, never a call's arguments (VALUES 5): a curl with a token in
    a header is a look, so its arguments stay out, and the row's digest ties it to the call."""
    try:
        words = shlex.split(command.split("<<", 1)[0], comments=True)
    except ValueError:
        words = command.split()
    skip = False
    for word in words:
        if skip:
            skip = False
            continue
        if word == "cd":
            skip = True
            continue
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", word, re.S) or word in ("&&", ";", "env", "||"):
            continue
        return Path(word).name[:60]
    return ""


def host(url: str) -> str:
    from urllib.parse import urlsplit
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


_ASSIGN = re.compile(r"(?:^|[;&|(\n]|\s)" + SAY + r"""=("(?:[^"\\]|\\.)*"|'[^']*'|[^\s;&|]*)""")


def shell_mark(command: str) -> str | None:
    """The BRNRD_SAY assignment a shell command carries, before any heredoc body; None when none."""
    head = command.split("<<", 1)[0]
    found = _ASSIGN.search(head)
    if not found:
        return None
    raw = found.group(1)
    try:
        words = shlex.split(raw)
    except ValueError:
        return raw
    return words[0] if words else ""


_PIPE_TAIL = re.compile(r"\A(?:tail|head)(?:\s+-n)?(?:\s+-?\d+)?\Z")
_QUIET = (">&1", ">/dev/null")


def _stages(command: str) -> list[tuple[str, bool]] | None:
    """The simple commands a shell line runs, as the shell splits them: ``(text, piped)`` each.

    Read by quotes, not by raw text: a ``;``, ``>``, ``<`` or backtick inside single quotes
    is a letter's words, and so are ``;``, ``>`` and ``<`` inside double quotes. None when
    the shell would run code of its own: a substitution (backtick or ``$(``) outside single
    quotes, ANSI-C or locale quoting (``$'…'``, ``$"…"``), a heredoc or other redirection
    than ``2>&1`` and ``>/dev/null``, a background ``&``, or a quote left open."""
    stages: list[tuple[str, bool]] = []
    cur: list[str] = []
    piped, quote, i, n = False, None, 0, len(command)

    def cut(next_piped: bool) -> None:
        nonlocal cur, piped
        stages.append(("".join(cur).strip(), piped))
        cur, piped = [], next_piped

    while i < n:
        c = command[i]
        if quote == "'":
            cur.append(c)
            quote = None if c == "'" else quote
            i += 1
            continue
        if c == "\\":
            cur.append(command[i:i + 2])
            i += 2
            continue
        if c == "`" or command.startswith("$(", i):
            return None
        if quote == '"':
            cur.append(c)
            quote = None if c == '"' else quote
            i += 1
            continue
        if c == "$" and command.startswith(("$'", '$"'), i):
            # ANSI-C and locale quoting read escapes this reader does not: never guess at them.
            return None
        if c in "'\"":
            quote = c
            cur.append(c)
            i += 1
            continue
        if command.startswith(("<(", ">("), i) or c == "<":
            return None
        if c == ">":
            if cur and cur[-1] == "2" and command.startswith(">&1", i):
                cur.pop()
                i += 3
                continue
            if command.startswith(">/dev/null", i):
                if cur and cur[-1] == "2":
                    cur.pop()
                i += len(">/dev/null")
                continue
            return None
        if command.startswith(("&&", "||"), i):
            cut(False)
            i += 2
            continue
        if c in ";\n":
            cut(False)
            i += 1
            continue
        if c == "|":
            cut(True)
            i += 1
            continue
        if c == "&":
            return None
        cur.append(c)
        i += 1
    if quote:
        return None
    cut(False)
    return stages


def loom_verbs_only(command: str) -> bool:
    """True when every command the shell would run is ``cd <dir>`` or one of the loom's own verbs,
    with the loom's env words, ``2>&1`` and a trailing ``| tail``/``| head`` allowed, and nothing
    that runs code of its own (:func:`_stages`). A letter's text in quotes is never read as shell."""
    stages = _stages(command) if command.strip() else None
    if stages is None:
        return False
    verbs = 0
    for n, (stage, piped) in enumerate(stages):
        if not stage:
            if piped or (n + 1 < len(stages) and stages[n + 1][1]):
                return False
            continue
        if piped:
            if not _PIPE_TAIL.match(stage):
                return False
            continue
        try:
            words = shlex.split(stage)
        except ValueError:
            return False
        # Only the loom's own env words: a PATH= or PYTHONPATH= could make "brnrd" another program.
        while words and re.fullmatch(r"BRNRD_[A-Z0-9_]*=.*", words[0], re.S):
            words = words[1:]
        if not words:
            continue
        if words[0] == "cd" and len(words) <= 2:
            continue
        if words[0] in _verb_paths() and words[1:2] == ["runtime"]:
            verbs += 1
            continue
        if re.fullmatch(r"python3?", words[0]) and words[1:3] == ["-m", "brr.loom.runtime"]:
            verbs += 1
            continue
        return False
    return verbs > 0


def _verb_paths() -> set[str]:
    """``brnrd`` as PATH finds it (the shim leads a body's PATH), or the shim by its full path."""
    paths = {"brnrd"}
    try:
        from . import entry
        paths.add(str(entry.shim()))
    except Exception:  # noqa: BLE001 - an install with no shim still has the bare word
        pass
    return paths


def _inside(path: str, room: Path) -> bool:
    if not path:
        return True
    target = Path(os.path.expanduser(path))
    if not target.is_absolute():
        return True  # relative to the room's own working tree
    target = Path(os.path.normpath(target))
    roots = [room]
    if os.environ.get("TMPDIR"):
        roots.append(Path(os.environ["TMPDIR"]))
    for root in roots:
        for form in {root, root.resolve()}:
            if target == form or form in target.parents:
                return True
    try:
        real = target.resolve()
    except OSError:
        return False
    return any(real == root.resolve() or root.resolve() in real.parents for root in roots)


@dataclass(frozen=True)
class Decision:
    """``pass`` (runs as asked), ``lift`` (runs with the wall lifted) or ``refuse``."""
    verdict: str
    mark: Mark | None = None
    why: str = ""
    judged: object = None  # grants.Judged, for a final mark: written by the hook once the call's verdict stands

    def record(self) -> dict | None:
        return self.mark.record(self.verdict == "lift") if self.mark else None


def _one_shot(room: Path) -> str | None:
    path = room / "port" / ONE_SHOT
    try:
        text = json.loads(path.read_text()).get("mark")
    except (OSError, ValueError, AttributeError):
        return None
    path.unlink(missing_ok=True)
    return text if isinstance(text, str) else None


def judge(payload: dict, room: Path, owed: tuple[str, ...] = (), walled: bool | None = None) -> Decision:
    """The pre-tool verdict for one call. ``walled`` defaults to the body's env (``BRNRD_WALL``)."""
    walled = os.environ.get(WALL) == "1" if walled is None else walled
    tool = str(payload.get("tool_name") or "")
    args = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    if tool == "Bash":
        command = str(args.get("command") or "")
        said = shell_mark(command)
        if said is not None:
            found = _ASSIGN.search(command.split("<<", 1)[0])
            mark, why = parse(said, what=program(command[found.end():] if found else command), owed=owed)
            if mark is None:
                return Decision("refuse" if walled else "pass", None, f"{why}. {FORM}")
            return Decision("lift" if walled else "pass", mark)
        if loom_verbs_only(command):
            return Decision("lift" if walled else "pass")
        if walled and args.get("dangerouslyDisableSandbox"):
            return Decision("refuse", None, "this call asks to leave the wall without a mark. " + FORM)
        return Decision("pass")
    if not walled:
        return Decision("pass")
    if tool in FILE_WRITES:
        path = str(args.get("file_path") or args.get("notebook_path") or "")
        if _inside(path, room):
            return Decision("pass")
        return Decision("refuse", None, f"{tool} writes outside the room ({path}). "
                        "Do it through a shell call with a mark: " + FORM)
    if tool in LOOKS:
        # The host for a fetch, nothing for a search: the query is the call's argument.
        what = host(str(args.get("url") or "")) if tool == "WebFetch" else ""
        return Decision("pass", Mark("look", what, " ".join(owed), "", ("what", "for")))
    if tool.startswith("mcp__"):
        said = _one_shot(room)
        mark, why = parse(said, what=tool, owed=owed)
        if mark is None:
            return Decision("refuse", None, f"{tool} reaches a service and has no place for a mark ({why}). "
                            'Give it one first: `brnrd runtime mark "reach · what · for · back"`, then call it again.')
        return Decision("pass", mark)
    return Decision("pass")


def rule(decision: Decision, room: Path, payload: dict | None = None) -> Decision:
    """Judge a final mark against an owner letter or a dashboard confirmation.

    Wherever a mark is given, walled or not: honouring it costs a call nothing it was not going to pay.
    Nothing is written here: the hook writes the verdict once the call's own verdict stands (jack.execute)."""
    if decision.mark is None or decision.mark.reach != "final" or decision.verdict == "refuse":
        return decision
    from .grants import judge as judge_final
    call = None
    if payload is not None:
        args = payload.get("tool_input") or {}
        if payload["tool_name"] == "Bash":
            command = args.get("command", "")
            found = _ASSIGN.search(command.split("<<", 1)[0])
            if found and not command[:found.start()].strip() and "\n" not in command[:found.start(1)]:
                call = {"command": command[found.end():].lstrip(" \t")}
        else:
            call = {"tool": payload["tool_name"], "input": args}
    try:
        judged = judge_final(room, decision.mark.what, decision.mark.for_, call=call)
    except Exception:
        if not (decision.mark.for_ in ("", "confirm") or decision.mark.for_.startswith("confirm:")):
            raise
        return Decision("refuse", decision.mark, "Dashboard confirmation is unavailable. The call was stopped.")
    if judged.granted:
        return replace(decision, why=judged.line, judged=judged)
    return Decision("refuse", decision.mark, judged.line, judged)


def wall_hit(payload: dict) -> bool:
    """A walled shell call that the wall stopped, by the OS's own words in its result."""
    if payload.get("tool_name") != "Bash" or os.environ.get(WALL) != "1":
        return False
    args = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    if args.get("dangerouslyDisableSandbox"):
        return False
    text = json.dumps(payload.get("tool_response", "")) + str(payload.get("error", ""))
    return bool(re.search(r"sandbox_violations|Operation not permitted|CONNECT tunnel failed|"
                          r"Could not resolve host|host is not on the allow list", text))


HIT = ("the wall held this call: it reaches outside the room (the network, or a write elsewhere). "
       "If it should, run it again with a mark: BRNRD_SAY=\"reach · what · for · back\" <command>; "
       "a look needs only BRNRD_SAY=look.")


def door_refusal(room: Path | str | None = None, look: bool = False, *, command=None) -> str | None:
    """At a door that leaves (a send to a person, a publish, a gh act): why it does not go, or None.

    Only behind the marks switch (``BRNRD_MARKS``, set at launch from ``doors = on``)."""
    if os.environ.get(MARKS) != "1":
        return None
    mark, why = parse(os.environ.get(SAY))
    if mark is not None and mark.reach == "look" and not look:
        return ("refused at the door: this act is never a look (a letter sent, a branch pushed, "
                "a forge act done): mark it change or final, naming the thing and the letter it serves")
    if mark is not None:
        if mark.reach != "final":
            return None
        room = room or os.environ.get("BRNRD_ROOM")
        if not room:
            return "refused at the door: a final act needs its room to find his letter (BRNRD_ROOM is unset)"
        if (not mark.for_ or mark.for_ == "confirm" or mark.for_.startswith("confirm:")):
            if look:
                return None
            import sys
            from .confirm import at_door
            try:
                return at_door(Path(room), mark, command or ["brnrd", *sys.argv[1:]])
            except Exception:
                return "Dashboard confirmation is unavailable. The call was stopped."
        from .grants import final
        granted, line = final(Path(room), mark.what, mark.for_)
        return None if granted else "refused at the door: " + line
    return (f"refused at the door: {why}. This act leaves the room: "
            'prefix it with BRNRD_SAY="reach · what · for · back" (e.g. '
            'BRNRD_SAY="final · letter to Ada · l-12"), then run it again.')


def give(room: Path, text: str) -> str:
    """``brnrd runtime mark "…"``: a one-shot mark for the next MCP call. Returns what was kept."""
    mark, why = parse(text)
    if mark is None:
        raise ValueError(f"{why}. {FORM}")
    path = room / "port" / ONE_SHOT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mark": text}) + "\n")
    return f"kept for the next MCP call: {mark.reach} · {mark.what}"


def wall_available() -> bool:
    """Whether the engine's sandbox can run here: Seatbelt on macOS, bubblewrap and socat on Linux."""
    import shutil
    import sys
    if sys.platform == "darwin":
        return Path("/usr/bin/sandbox-exec").exists()
    if sys.platform.startswith("linux"):
        return bool(shutil.which("bwrap") and shutil.which("socat"))
    return False


def sandbox(room: Path) -> dict:
    """Claude Code's sandbox block for a body in ``room``: the room, its scratch and caches are
    writable, every read is allowed, no host is reachable, localhost is (the audit socket).
    A cache is a copy by definition: writing one changes nothing a person holds."""
    home = Path.home()
    return {"enabled": True, "failIfUnavailable": False, "allowUnsandboxedCommands": True,
            "filesystem": {"allowWrite": [str(room), str(home / ".cache"), str(home / "Library" / "Caches"),
                                          str(home / ".npm")]},
            "network": {"allowedDomains": [], "strictAllowlist": True, "allowLocalBinding": True}}


def main(argv: list[str] | None = None) -> int:
    """``mark "reach · what · for · back"``: kept for the next MCP call of this room.
    ``mark --check``: whether this process's BRNRD_SAY lets it through a door; exit 3 when not."""
    import sys
    from .refusals import Parser, refuse
    parser = Parser(verb="mark", description="A one-shot mark for the next MCP tool call.")
    parser.add_argument("mark", nargs="?")
    parser.add_argument("--room", default=os.environ.get("BRNRD_ROOM"))
    parser.add_argument("--check", action="store_true", help="a door's question, answered by exit code")
    parser.add_argument("--look", action="store_true", help="with --check: a look passes (a wall, not a door)")
    args = parser.parse_args(argv)
    if args.check:
        refusal = door_refusal(args.room, look=args.look)
        if refusal:
            sys.stderr.write(refusal + "\n")
            return 3
        return 0
    if not args.room or not args.mark:
        return refuse("mark", ValueError("mark needs the mark, and --room or BRNRD_ROOM from the loom"))
    try:
        print(give(Path(args.room), args.mark))
    except ValueError as exc:
        return refuse("mark", exc)
    return 0

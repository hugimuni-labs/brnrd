"""``python -m brr.loom.runtime <loom|send|reincarnate|hold|jack|attention>``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .home import Home, home_of_room, mint, resolve_home, strand_of_room
from .jack import run as run_jack
from .ledger import read_facts
from .loom import pending_bodies, run as run_loom
from .port import PortError, write_reincarnate, write_send
from .spend import parse_tokens
from .project import fold, is_notice
from .selfrepo import SelfError
from .refusals import Parser, refuse


def _room(arg: str | None) -> Path:
    raw = arg or os.environ.get("BRNRD_ROOM")
    if not raw:
        raise SystemExit("no --room and BRNRD_ROOM is unset")
    return Path(raw)


def _loom(argv: list[str]) -> int:
    parser = Parser(verb="loom", prog="python -m brr.loom.runtime loom")
    parser.add_argument("--home")
    parser.add_argument("--install", default=None)
    parser.add_argument("--adapter", default=None)
    parser.add_argument("--core", default="haiku")
    parser.add_argument("--tick", type=float, default=0.2)
    if argv[:1] == ["check"]:
        from .check import run
        return run(argv[1:])
    args = parser.parse_args(argv)
    args.home, _ = resolve_home(args.home)
    adapter = args.adapter
    core = args.core
    # The service argv carries no --adapter. The home's body is the choice.
    # An explicit --adapter keeps the old hand path (tests pass fake).
    if adapter is None:
        from .config import fold_config
        from .service import _require_body
        # A home an appending writer left with a key set twice loads once folded.
        fold_config(Path(args.home).expanduser().resolve())
        choice = _require_body(Path(args.home).expanduser().resolve())
        from .adapters import parse_body
        adapter, core = parse_body(choice)
    run_loom(
        args.home, adapter=adapter, core=core, tick=args.tick,
        install=args.install,
    )
    return 0


def _attention(argv: list[str]) -> int:
    parser = Parser(verb="attention", prog="python -m brr.loom.runtime attention")
    parser.add_argument("--home")
    parser.add_argument("--room")
    parser.add_argument("--install", default=None)
    parser.add_argument("--note", metavar="LETTER_ID")
    parser.add_argument("--why")
    args = parser.parse_args(argv)
    from .home import home_for
    home = home_for(args.home, args.room)
    if args.install is not None:
        home = Home(home.root, install=args.install)
    if args.note:
        if not args.why or not args.why.strip():
            parser.error("--note requires --why")
        from brr.daemon2.facts import Fact
        from .ledger import append
        from .home import mint
        from .owners import letter_owners
        from .lifecycle import main_snapshot
        if args.note not in letter_owners(read_facts(home), main_snapshot(home)):
            parser.error("--note names no accepted letter")
        append(home, Fact(kind="note", by="person:operator", id="operator-note:" + mint(12),
                          data={"re": args.note, "why": args.why}))
    from .usage import fuel, shells
    for shell in shells(home):
        line = fuel(home, shell)
        if line:
            print(line)
    # No state of its own: the loom's notices that no body has settled yet.
    state = fold(read_facts(home))
    from datetime import datetime, timezone
    for strand, fact in pending_bodies(state.accepted).items():
        if fact.kind == "body.parked" and strand in {s for s, _g in state.holder.values()}:
            d = fact.data
            thread = next(t for t, (s, _g) in state.holder.items() if s == strand)
            until = datetime.fromtimestamp(d["until"], timezone.utc).isoformat()
            left = d.get("left_pct", 0)
            sys.stdout.write(f"parked · {thread} · {d['shell']} {d['window']} {left:g}% · until {until}\n")
    from .owners import letter_owners
    from .lifecycle import main_snapshot
    for ident, fate in letter_owners(read_facts(home), main_snapshot(home)).items():
        if fate.owner == 'operator' or (is_notice(fate.letter) and not fate.done):
            sys.stdout.write(f"{ident} · {fate.owner}:{fate.thread or '-'} · {fate.reason}\n")
    return 0


class _Wish(argparse.Action):
    """``--wish`` opens a group; ``--says``, ``--ref`` and ``--answered`` after it bind to it.

    A ``--ref`` before any ``--wish`` stays the clean letter's ref.
    """

    def __call__(self, parser, namespace, value, option=None):
        if namespace.wish is None:
            namespace.wish = []
        groups = namespace.wish
        if self.dest == "wish":
            groups.append({"value": value, "says": [], "refs": [], "answered": False})
        elif self.dest == "ref" and not groups:
            namespace.ref = (namespace.ref or []) + [value]
        elif not groups:
            parser.error(f"{option} binds to the --wish before it")
        elif self.dest == "answered":
            groups[-1]["answered"] = True
        else:
            groups[-1][self.dest + ("" if self.dest == "says" else "s")].append(value)


def _send(argv: list[str]) -> int:
    parser = Parser(verb="send", prog="python -m brr.loom.runtime send")
    parser.add_argument("--room")
    parser.add_argument("--to", required=True)
    parser.add_argument("--re")
    parser.add_argument("--note")
    parser.add_argument("--wish", action=_Wish, default=None, help='w-id or new:"<line>"')
    parser.add_argument("--says", action=_Wish, help='"quote" or <letter id>:"quote"; never refused')
    parser.add_argument("--answered", action=_Wish, nargs=0, help="this send's words are the receipt")
    parser.add_argument("--chatter", action="store_true", help="cites the --re letter as no wish")
    parser.add_argument("--for", dest="ask_for", metavar="W-ID",
                        help="a question to a person files an ask under this wish")
    parser.add_argument("--chatter-of", dest="chatter_of", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--reopen", metavar="TEXT")
    parser.add_argument("--at", help="YYYY-MM-DDTHH:MM:SSZ, +<n>m, +<n>h, or +<n>d")
    parser.add_argument("--body", dest="body_choice", metavar="SHELL[/CORE]")
    parser.add_argument("--repo", action="append", default=[])
    parser.add_argument("--budget", metavar="TOKENS",
                        help="weighted tokens, e.g. 2m: a new thread's allocation, or the opener's raise")
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--kind", default=None)
    parser.add_argument("--ref", action=_Wish, default=None, help="after --wish: kind:ref; before: a clean ref")
    parser.add_argument("body", nargs="?")
    # --chatter=<id>,… names the letters; a bare --chatter never eats the body after it.
    args = parser.parse_args([part for arg in argv for part in (
        ["--chatter-of", arg.partition("=")[2]] if arg.startswith("--chatter=") else [arg])])
    chatter = (None if args.chatter_of is None and not args.chatter
               else [i for i in (args.chatter_of or "").split(",") if i.strip()])
    echo: list[str] = []
    room = _room(args.room)
    try:
        if args.at is not None and args.reopen is not None:
            raise PortError("send: --at cannot be used with --reopen")
        if args.reopen is not None:
            from .home import thread_of
            from .lifecycle import land_status
            if args.body is not None or args.note is not None or args.body_choice is not None or args.repo:
                raise PortError("send: --reopen supplies the letter text; drop the body and --note")
            land_status(room, thread_of(args.to), "open", f"reopen {thread_of(args.to)}")
        letter_id = write_send(
            room, to=args.to, sender=strand_of_room(room), body=args.reopen if args.reopen is not None else args.body or "",
            re=args.re, note=args.note, clean=args.clean, kind=args.kind,
            refs=tuple(args.ref or ()), body_choice=args.body_choice, repo_sources=args.repo, at=args.at,
            wish=args.wish, budget=None if args.budget is None else parse_tokens(args.budget),
            chatter=chatter, ask_for=args.ask_for,
            echo=echo, reopen=args.reopen is not None,
        )
    except (PortError, SelfError, ValueError, OSError) as exc:
        return refuse("send", exc)
    sys.stdout.write(letter_id + "\n")
    # stdout stays the letter id; the graph this send wrote goes beside it.
    sys.stderr.write("".join(line + "\n" for line in echo))
    return 0


def _reincarnate(argv: list[str]) -> int:
    """``reincarnate``: hand the seat on. The stakes and the README's facts come
    first, on stderr, so the choice is an informed one."""
    parser = Parser(verb="reincarnate", prog="python -m brr.loom.runtime reincarnate")
    parser.add_argument("--room")
    parser.add_argument("--why", required=True)
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--cites", metavar="LETTER_ID", help="the letter on this thread it answers: its receipt")
    args = parser.parse_args(argv)
    room = _room(args.room)
    try:
        if not args.fresh:
            sys.stderr.write(_stakes(room))
        fact_id = write_reincarnate(room, args.why, fresh=args.fresh, cites=args.cites)
    except (PortError, SelfError, ValueError, OSError) as exc:
        return refuse("reincarnate", exc)
    sys.stdout.write(fact_id + "\n")
    return 0


def _stakes(room) -> str:
    """What the successor boots into, said once more before the seat goes."""
    from .jack import _state
    from .ledger import read_facts
    from .project import sender_threads
    from .reincarnate import STAKES, readme_facts
    try:
        facts = read_facts(home_of_room(room))
        thread = sender_threads(facts).get(strand_of_room(room)) or ""
        said = readme_facts(room, thread, facts, _state(room).get("readme_base") or "") if thread else ""
    except Exception:  # noqa: BLE001 - facts inform the reincarnation; they never block it
        said = ""
    return f"{STAKES}\n" + (f"{said}\n" if said else "")


#: Words that name no particular thing: a why made only of these is a bare hold.
_GENERIC = set("""a an the it its this that my our of on in for to and or but with is are be still
just some all everything nothing stuff things context warm cache cached live work working busy
ongoing progress current state needed need needs mid way midway mid-way hold holding keep keeping
going reasoning thread here now yet more later continue continuing wip tbd n/a na none""".split())


def bare(why: str) -> bool:
    """A hold's why that names nothing that would die: fewer than three words of substance."""
    words = [w.strip(".,;:!?()'\"`").lower() for w in why.split()]
    return sum(1 for w in words if w and w not in _GENERIC and len(w) > 1) < 3


def _hold(argv: list[str]) -> int:
    """``hold``: keep the seat past the context mark, naming what would die if it went.
    A bare why is refused; the hold is a fact, so the whys can be read back later."""
    from .jack import _HEADER_LINE, _boundary_text, _set_state, _state
    from .ledger import append
    from .reincarnate import contexts
    from brr.daemon2.facts import Fact
    parser = Parser(verb="hold", prog="python -m brr.loom.runtime hold")
    parser.add_argument("--room")
    parser.add_argument("--why", required=True)
    args = parser.parse_args(argv)
    why = " ".join(args.why.split())
    if bare(why):
        return refuse("hold", "hold: name what would die, by name (a decision mid-way, a person's "
                              "conversation, a reasoning not yet written); a generic why is no hold. "
                              "If nothing would, reincarnate --why \"<what the successor picks up>\".")
    try:
        room = _room(args.room)
        state = _state(room)
        path = state.get("transcript") or ""
        context = (contexts(json.dumps({"transcript_path": path}), 0)[1] if path else 0) \
            or int(state.get("context") or 0)
        header = _HEADER_LINE.search(_boundary_text(room))
        strand = strand_of_room(room)
        fact = Fact(kind="hold", by=f"strand:{strand}", id="hold:" + mint(12),
                    data={"strand": strand, "gen": int(header.group(2)) if header else None,
                          "why": why, "context": context})
        append(home_of_room(room), fact)
        # The mark's ask stays quiet until the context grows past this hold.
        _set_state(room, mark_at=context)
    except (PortError, SelfError, ValueError, OSError) as exc:
        return refuse("hold", exc)
    sys.stdout.write(fact.id + "\n")
    return 0


def _settle(argv: list[str]) -> int:
    from .lifecycle import settle
    parser = Parser(verb="settle", prog="python -m brr.loom.runtime settle")
    parser.add_argument("thread")
    parser.add_argument("--room")
    parser.add_argument("--why", required=True)
    parser.add_argument("--retire", action="store_true")
    parser.add_argument("--hand-to")
    parser.add_argument("--word", metavar="LETTER_ID")
    parser.add_argument("--cites", metavar="LETTER_ID", help="the letter on this thread it answers: its receipt")
    args = parser.parse_args(argv)
    try:
        room = _room(args.room)
        settle(room, args.thread, args.why, args.retire, args.hand_to, args.word, args.cites)
    except (PortError, SelfError, ValueError, OSError) as exc:
        return refuse("settle", exc)
    from .jack import declare
    declare(room, "settle")
    return 0


def _jack(argv: list[str]) -> int:
    parser = Parser(verb="jack", prog="python -m brr.loom.runtime jack")
    parser.add_argument("--event", required=True, choices=("start", "pre", "post", "stop", "subagent-stop"))
    parser.add_argument("--room", required=True)
    args = parser.parse_args(argv)
    stdin_text = "" if sys.stdin.isatty() else sys.stdin.read()
    _code, out = run_jack(args.event, Path(args.room), stdin_text)
    sys.stdout.write(out)
    return 0


def _paused(verb: str, act, argv: list[str]) -> int:
    """``resume``/``drop``: the body's answer to a ``paused:`` line (pausing.py)."""
    from brr.pause import describe_paused
    parser = Parser(verb=verb, prog=f"python -m brr.loom.runtime {verb}")
    parser.add_argument("--room")
    args = parser.parse_args(argv)
    done = act(_room(args.room) / "port")
    sys.stdout.write(f"{verb}: {describe_paused(done)}\n" if done else "nothing paused\n")
    return 0


def _resume(argv: list[str]) -> int:
    from brr.pause import resume_pids
    return _paused("resume", resume_pids, argv)


def _drop(argv: list[str]) -> int:
    from brr.pause import drop_pids
    return _paused("drop", drop_pids, argv)

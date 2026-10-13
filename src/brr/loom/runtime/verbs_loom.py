"""``python -m brr.loom.runtime <loom|send|molt|jack|attention>``."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .home import Home, strand_of_room
from .jack import run as run_jack
from .ledger import read_facts
from .loom import pending_bodies, run as run_loom
from .port import PortError, write_molt, write_send
from .project import fold, is_notice
from .selfrepo import SelfError


def _room(arg: str | None) -> Path:
    raw = arg or os.environ.get("BRNRD_ROOM")
    if not raw:
        raise SystemExit("no --room and BRNRD_ROOM is unset")
    return Path(raw)


def _loom(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m brr.loom.runtime loom")
    parser.add_argument("--home", required=True)
    parser.add_argument("--install", default=None)
    parser.add_argument("--adapter", default=None)
    parser.add_argument("--core", default="haiku")
    parser.add_argument("--tick", type=float, default=0.2)
    if argv[:1] == ["check"]:
        from .check import run
        return run(argv[1:])
    args = parser.parse_args(argv)
    adapter = args.adapter
    core = args.core
    # The service argv carries no --adapter. The home's body is the choice.
    # An explicit --adapter keeps the old hand path (tests pass fake).
    if adapter is None:
        from .service import _require_body
        choice = _require_body(Path(args.home).expanduser().resolve())
        adapter, _, core = choice.partition("/")
        adapter, core = adapter.strip(), core.strip()
    run_loom(
        args.home, adapter=adapter, core=core, tick=args.tick,
        install=args.install,
    )
    return 0


def _attention(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m brr.loom.runtime attention")
    parser.add_argument("--home", required=True)
    parser.add_argument("--install", default=None)
    parser.add_argument("--note", metavar="LETTER_ID")
    parser.add_argument("--why")
    args = parser.parse_args(argv)
    home = Home(args.home, install=args.install)
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
    # No state of its own: the loom's notices that no body has settled yet.
    state = fold(read_facts(Home(args.home, install=args.install)))
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


def _send(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m brr.loom.runtime send")
    parser.add_argument("--room")
    parser.add_argument("--to", required=True)
    parser.add_argument("--re")
    parser.add_argument("--note")
    parser.add_argument("--reopen", metavar="TEXT")
    parser.add_argument("--body", dest="body_choice", metavar="SHELL/CORE")
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--kind", default=None)
    parser.add_argument("--ref", action="append", default=None)
    parser.add_argument("body", nargs="?")
    args = parser.parse_args(argv)
    room = _room(args.room)
    try:
        if args.reopen is not None:
            from .home import thread_of
            from .lifecycle import land_status
            if args.body is not None or args.note is not None or args.body_choice is not None:
                raise PortError("send: --reopen supplies the letter text; drop the body and --note")
            land_status(room, thread_of(args.to), "open", f"reopen {thread_of(args.to)}")
        letter_id = write_send(
            room, to=args.to, sender=strand_of_room(room), body=args.reopen if args.reopen is not None else args.body or "",
            re=args.re, note=args.note, clean=args.clean, kind=args.kind,
            refs=tuple(args.ref or ()), body_choice=args.body_choice,
        )
    except (PortError, SelfError, ValueError, OSError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    sys.stdout.write(letter_id + "\n")
    return 0


def _molt(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m brr.loom.runtime molt")
    parser.add_argument("--room")
    parser.add_argument("--why", required=True)
    args = parser.parse_args(argv)
    room = _room(args.room)
    try:
        fact_id = write_molt(room, args.why)
    except (PortError, SelfError, ValueError, OSError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    sys.stdout.write(fact_id + "\n")
    return 0


def _settle(argv: list[str]) -> int:
    from .lifecycle import settle
    parser = argparse.ArgumentParser(prog="python -m brr.loom.runtime settle")
    parser.add_argument("thread")
    parser.add_argument("--room")
    parser.add_argument("--why", required=True)
    parser.add_argument("--retire", action="store_true")
    args = parser.parse_args(argv)
    try:
        settle(_room(args.room), args.thread, args.why, args.retire)
    except (PortError, SelfError, ValueError, OSError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    return 0


def _jack(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m brr.loom.runtime jack")
    parser.add_argument("--event", required=True, choices=("start", "post", "stop"))
    parser.add_argument("--room", required=True)
    args = parser.parse_args(argv)
    stdin_text = "" if sys.stdin.isatty() else sys.stdin.read()
    _code, out = run_jack(args.event, Path(args.room), stdin_text)
    sys.stdout.write(out)
    return 0

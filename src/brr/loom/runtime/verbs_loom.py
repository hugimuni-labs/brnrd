"""``python -m brr.loom.runtime <loom|send|molt|jack|attention>``."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .attention import clear_letter, view
from .home import Home, strand_of_room
from .jack import run as run_jack
from .ledger import read_facts
from .loom import run as run_loom
from .port import PortError, write_molt, write_send


def _room(arg: str | None) -> Path:
    raw = arg or os.environ.get("BRNRD_ROOM")
    if not raw:
        raise SystemExit("no --room and BRNRD_ROOM is unset")
    return Path(raw)


def _loom(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m brr.loom.runtime loom")
    parser.add_argument("--home", required=True)
    parser.add_argument("--install", default=None)
    parser.add_argument("--adapter", default="fake", choices=("fake", "claude"))
    parser.add_argument("--core", default="haiku")
    parser.add_argument("--tick", type=float, default=0.2)
    args = parser.parse_args(argv)
    run_loom(
        args.home, adapter=args.adapter, core=args.core, tick=args.tick,
        install=args.install,
    )
    return 0


def _attention(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m brr.loom.runtime attention")
    parser.add_argument("--home", required=True)
    parser.add_argument("--install", default=None)
    parser.add_argument("--clear", default=None)
    args = parser.parse_args(argv)
    home = Home(args.home, install=args.install)
    if args.clear:
        try:
            clear_letter(home, args.clear)
        except ValueError as exc:
            sys.stderr.write(f"{exc}\n")
            return 1
    for row in view(read_facts(home)):
        sys.stdout.write(row.render() + "\n")
    return 0


def _send(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m brr.loom.runtime send")
    parser.add_argument("--room")
    parser.add_argument("--to", required=True)
    parser.add_argument("--re")
    parser.add_argument("--note")
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--kind", default=None)
    parser.add_argument("--ref", action="append", default=None)
    parser.add_argument("body", nargs="?")
    args = parser.parse_args(argv)
    room = _room(args.room)
    try:
        letter_id = write_send(
            room, to=args.to, sender=strand_of_room(room), body=args.body or "",
            re=args.re, note=args.note, clean=args.clean, kind=args.kind,
            refs=tuple(args.ref or ()),
        )
    except (PortError, ValueError) as exc:
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
    except (PortError, ValueError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    sys.stdout.write(fact_id + "\n")
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

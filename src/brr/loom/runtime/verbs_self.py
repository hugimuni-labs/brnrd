"""Parsers for seeding a self and running its own wake recipe."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from brr.loom.runtime.selfrepo import SelfError, init_self, run_wake
from .home import resolve_home


def cmd_init(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m brr.loom.runtime init")
    parser.add_argument("--home")
    parser.add_argument("--person", default=None)
    parser.add_argument("--remote", default=None)
    args = parser.parse_args(argv)
    home, _ = resolve_home(args.home)
    try:
        print(init_self(home, person=args.person, remote=args.remote))
    except SelfError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def cmd_wake(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m brr.loom.runtime wake")
    parser.add_argument("--room", required=True)
    parser.add_argument("--thread", required=True)
    parser.add_argument("--owed", default=None)
    args = parser.parse_args(argv)
    try:
        owed = Path(args.owed) if args.owed else None
        return run_wake(Path(args.room), args.thread, owed)
    except SelfError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def cmd_lib(argv: list[str]) -> int:
    from brr.loom.runtime import library
    parser = argparse.ArgumentParser(prog="python -m brr.loom.runtime lib")
    sub = parser.add_subparsers(dest="verb", required=True)
    add = sub.add_parser("add", help="name a folder or repo in the library")
    add.add_argument("name")
    add.add_argument("target", help="a folder (from / or ~) or a repo URL")
    add.add_argument("--kind", required=True, choices=library.KINDS)
    add.add_argument("--whose", required=True)
    add.add_argument("--for", dest="purpose", required=True)
    add.add_argument("--room", default=".", help="the self clone to commit in")
    drop = sub.add_parser("drop", help="remove a library page; the folder stays")
    drop.add_argument("name")
    drop.add_argument("--room", default=".")
    listing = sub.add_parser("list", help="every entry on main, one line each")
    listing.add_argument("--home")
    args = parser.parse_args(argv)
    try:
        if args.verb == "list":
            from .home import Home
            found, bad = library.entries(Home(resolve_home(args.home)[0]))
            here = library.machine()
            for entry in found:
                print(library.line(entry, here))
            for why in bad:
                print(f"library page unreadable: {why}")
            return 0
        room = Path(args.room).resolve()
        if args.verb == "add":
            dest = library.add(room, args.name, args.target, kind=args.kind,
                               whose=args.whose, purpose=args.purpose)
            print(f"wrote and committed {dest.relative_to(room)}; land it with "
                  f"`python -m brr.loom.runtime send-self --room .`")
        else:
            library.drop(room, args.name)
            print(f"dropped library/{args.name}.md (the folder is untouched); land it with send-self")
    except (library.LibraryError, SelfError) as exc:
        print(f"lib: {exc}", file=sys.stderr)
        return 1
    return 0

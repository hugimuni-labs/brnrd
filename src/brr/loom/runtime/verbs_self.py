"""Parsers for seeding a self and running its own wake recipe."""

from __future__ import annotations

import argparse
import os
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
    add.add_argument("--git-init", action="store_true",
                     help="kept, plain folder: run git init there (say so to the person first)")
    add.add_argument("--room", default=".", help="the self clone to commit in")
    commit = sub.add_parser("commit", help="commit the paths you wrote in a kept folder, nothing else")
    commit.add_argument("name")
    commit.add_argument("paths", nargs="+")
    commit.add_argument("-m", "--message", required=True)
    commit.add_argument("--home")
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
        if args.verb == "commit":
            from .home import Home
            found = {e.name: e for e in library.entries(Home(resolve_home(args.home)[0]))[0]}
            entry = found.get(args.name)
            if entry is None or entry.kind != "kept" or not entry.here(library.machine()):
                raise library.LibraryError(f"{args.name}: no kept folder of that name on this machine")
            sha = library.commit(Path(entry.path), args.message, args.paths)
            if os.environ.get("BRNRD_ROOM"):
                library.own_head(Path(os.environ["BRNRD_ROOM"]), entry.name, sha)
            print(f"{args.name}: committed {sha[:12]}")
            return 0
        room = Path(args.room).resolve()
        if args.verb == "add":
            dest = library.add(room, args.name, args.target, kind=args.kind,
                               whose=args.whose, purpose=args.purpose, git_init=args.git_init)
            print(f"wrote and committed {dest.relative_to(room)}; land it with "
                  f"`python -m brr.loom.runtime send-self --room .`")
            entry = library.parse(args.name, dest.read_text())
            if entry.kind == "kept" and entry.path:
                files, size = library.first_commit_size(Path(entry.path))
                print(f"{args.name}: {files} file(s), {size / 1024 ** 2:.1f} MB not yet committed there")
                if size > library.INIT_CAP:
                    print(f"{args.name}: over 1 GB; do not commit it whole: propose a narrower "
                          ".gitignore, or drop it and add it as known")
        else:
            library.drop(room, args.name)
            print(f"dropped library/{args.name}.md (the folder is untouched); land it with send-self")
    except (library.LibraryError, SelfError) as exc:
        print(f"lib: {exc}", file=sys.stderr)
        return 1
    return 0

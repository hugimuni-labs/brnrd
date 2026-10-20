"""Write one wish fact, or read the buckets, through the existing ledger."""

from __future__ import annotations

import argparse
import os
import re
import sys

from brr.daemon2.facts import Fact
from .home import Home, home_of_room, mint, resolve_home, strand_of_room
from .ledger import append, read_facts
from .wishes import wishes, wish_line


def _context(args):
    from .verbs_loom import _room
    from .lifecycle import own_thread
    if args.room or os.environ.get("BRNRD_ROOM"):
        if args.home:
            raise ValueError("--home is not allowed inside a strand room")
        room = _room(args.room)
        return home_of_room(room), "strand:" + strand_of_room(room), own_thread(room)
    return Home(resolve_home(args.home)[0]), "person:operator", None


def say_span(raw: str, facts) -> list:
    match = re.fullmatch(r"(.+?)(?:@(\d+)\+(\d+))?", raw)
    if not match:
        raise ValueError("--say expects L or L@off+len")
    ident, offset, length = match.groups()
    letters = {str(f.data.get("id", f.id)): f for f in facts if f.kind == "letter"}
    if ident not in letters:
        raise ValueError(f"--say: no letter {ident}")
    off, size = int(offset or 0), int(length) if length is not None else None
    if off + (size or 0) > len(letters[ident].data.get("body", "")):
        raise ValueError(f"--say: span past the end of {ident}")
    return [ident, off, size]


def wish_fact(facts, *, by, ident, says=(), line=None, thread=None, sender=None, parent=None):
    known = wishes(facts)
    current = known.get(ident)
    if parent is not None and parent not in known:
        raise ValueError(f"--parent: no wish {parent}")
    if ident == "new":
        ident = "w-" + mint(12)
        if not line or not line.strip():
            raise ValueError("wish new requires --line")
        if not thread:
            raise ValueError("wish new requires --thread outside a thread room")
    elif current is None:
        raise ValueError(f"no wish {ident}")
    data = {
        "wish": ident, "says": list(says),
        "from": sender or (current.sender if current else "person"),
        "line": line if line is not None else current.line,
        "thread": thread if thread is not None else current.thread,
    }
    if parent is not None:
        data["parent"] = parent
    return Fact(kind="wish", by=by, id=ident if current is None else "wish:" + mint(12), data=data)


def _arguments(parser):
    parser.add_argument("--home")
    parser.add_argument("--room")
    parser.add_argument("--thread")


def _wish_arguments(parser):
    _arguments(parser)
    parser.add_argument("id")
    parser.add_argument("--say", action="append", default=[])
    parser.add_argument("--line")
    parser.add_argument("--parent")
    parser.add_argument("--from", dest="sender", choices=("person", "resident"))
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--read")
    action.add_argument("--result")
    action.add_argument("--accept", choices=("word", "act", "implied"))
    action.add_argument("--drop")
    parser.add_argument("--deliver", action="store_true")
    parser.add_argument("--ref")


def cmd_wish(args):
    try:
        home, by, thread = _context(args)
        facts = read_facts(home)
        actions = any(x is not None for x in (args.read, args.result, args.accept, args.drop))
        if args.deliver and args.result is None:
            raise ValueError("--deliver requires --result")
        if args.ref is not None and args.accept is None:
            raise ValueError("--ref requires --accept")
        if actions:
            if args.id == "new" or args.id not in wishes(facts):
                raise ValueError(f"no wish {args.id}; create it with wish new first")
            if args.say or args.sender or args.thread or args.parent or (args.line is not None and args.result is None):
                raise ValueError("touch flags cannot accompany a reading, result or verdict")
            data = {"wish": args.id}
            if args.read is not None:
                kind = "wish.read"
                data.update(reading=args.read, by="person" if by.startswith("person:") else "resident")
            elif args.result is not None:
                result_kind, sep, ref = args.result.partition(":")
                if not sep or not result_kind or not ref or not args.line:
                    raise ValueError("--result requires kind:ref and --line")
                kind = "wish.result"
                data.update(kind=result_kind, ref=ref, line=args.line, delivered=args.deliver)
            else:
                kind = "wish.verdict"
                data.update({"as": "accepted" if args.accept else "dropped",
                             "evidence": args.accept or "word",
                             "ref": args.ref if args.accept else args.drop})
            fact = Fact(kind=kind, by=by, id=kind + ":" + mint(12), data=data)
        else:
            fact = wish_fact(facts, by=by, ident=args.id,
                             says=[say_span(s, facts) for s in args.say], line=args.line,
                             thread=args.thread or (thread if args.id == "new" else None), sender=args.sender,
                             parent=args.parent)
        target = fact.data["thread"] if "thread" in fact.data else wishes(facts)[args.id].thread
        if not home.routable(target):
            raise ValueError("wish: thread must exist on main")
        append(home, fact)
        print(fact.data["wish"])
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def cmd_wishes(args):
    try:
        home, _by, _thread = _context(args)
        buckets = {name: [] for name in ("in hand", "asks (yours)", "delivered", "heard", "done")}
        rows = sorted(wishes(read_facts(home)).values(), key=lambda w: (w.at, w.id), reverse=True)
        for w in rows:
            if (args.thread and w.thread != args.thread) or (not args.all and not w.open):
                continue
            bucket = ("done" if not w.open else "asks (yours)" if w.sender == "resident" else
                      "delivered" if w.state == "delivered" else "heard" if w.state == "heard" else "in hand")
            buckets[bucket].append(wish_line(w))
        for name, lines in buckets.items():
            if lines:
                print(name + "\n" + "\n".join(lines))
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def add_wishes_parsers(subparsers):
    parser = subparsers.add_parser("wish")
    _wish_arguments(parser)
    parser.set_defaults(func=cmd_wish)
    parser = subparsers.add_parser("wishes")
    _arguments(parser)
    parser.add_argument("--all", action="store_true")
    parser.set_defaults(func=cmd_wishes)


def _wish(argv):
    parser = argparse.ArgumentParser(prog="python -m brr.loom.runtime wish")
    _wish_arguments(parser)
    return cmd_wish(parser.parse_args(argv))


def _wishes(argv):
    parser = argparse.ArgumentParser(prog="python -m brr.loom.runtime wishes")
    _arguments(parser)
    parser.add_argument("--all", action="store_true")
    return cmd_wishes(parser.parse_args(argv))

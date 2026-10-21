"""Read wishes or write explicit wish facts through the existing ledger."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from brr.daemon2.facts import Fact
from .home import Home, home_of_room, mint, resolve_home, strand_of_room
from .ledger import append, read_facts
from .wishes import waits_on, waits_word, wishes, wish_line


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
    if data["from"] == "resident" and not (parent or (current and current.parent)):
        raise ValueError("an ask needs the wish it unblocks: ask --for <wish>")
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
        touches = args.say or any(x is not None for x in (args.line, args.sender, args.thread, args.parent))
        if args.id != "new" and not actions and not touches:
            current = wishes(facts).get(args.id)
            if current is None:
                raise ValueError(f"no wish {args.id}")
            print(wish_line(current))
            return 0
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


def _mailed(facts, room, to, marker):
    """A letter to ``to`` whose body starts with ``marker`` left this room:
    still in its port, or in the ledger. One pass over the facts the ask
    already read; a letter that only quotes the marker further in is not it."""
    from .port import PortError, parse_frontmatter
    out = Path(room) / "port" / "out"
    for path in sorted(out.glob("*.md")) if out.is_dir() else ():
        try:
            sent = parse_frontmatter(path.read_text())
        except PortError:
            continue
        if sent.get("to") == to and sent["body"].startswith(marker):
            return True
    return any(f.kind == "letter" and (f.data or {}).get("to") == to
               and str((f.data or {}).get("body", "")).startswith(marker) for f in facts)


def cmd_ask(args):
    """A resident asks a person: a wish from:resident under the wish it unblocks."""
    try:
        home, by, thread = _context(args)
        if thread is None:
            raise ValueError("ask: only a strand asks; run it inside a thread room")
        facts = read_facts(home)
        if args.rec is not None and not args.rec.strip():
            raise ValueError("ask: --rec is empty")
        # A retry of an ask that half-happened finishes it: the same open ask
        # (thread, parent, line) is reused, and mailed only if no mail for it left.
        same = next((w for w in wishes(facts).values() if w.open and w.sender == "resident"
                     and (w.thread, w.parent, w.line) == (thread, args.wish, args.what)), None)
        if same is None:
            fact = wish_fact(facts, by=by, ident="new", line=args.what, thread=thread,
                             sender="resident", parent=args.wish)
            append(home, fact)
            ident = fact.data["wish"]
        else:
            ident = same.id
            print(f"ask: reused {ident}, the open ask with this line for {args.wish}", file=sys.stderr)
        if args.rec is not None and (same is None or same.reading != args.rec):
            append(home, Fact(kind="wish.read", by=by, id="wish.read:" + mint(12),
                              data={"wish": ident, "reading": args.rec, "by": "resident"}))
        # A thread that waits on another must wake it: a wish alone is mail to nobody.
        from .lifecycle import main_snapshot
        from .port import write_send
        from .verbs_loom import _room
        facts = read_facts(home)
        head = waits_on(wishes(facts), main_snapshot(home)).get(ident, "")
        room = _room(args.room)
        marker = f"ask {ident} from thread:{thread}: "
        if head.startswith("thread:") and not _mailed(facts, room, head, marker):
            body = marker + args.what + "\n"
            if args.rec is not None:
                body += f"rec: {args.rec}\n"
            body += f"Answer it, or ask onward with: ask \"<what>\" --for {ident}\n"
            write_send(room, to=head, sender=strand_of_room(room), body=body)
        print(ident)
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _ask_arguments(parser):
    parser.add_argument("--room")
    parser.add_argument("--home")
    parser.add_argument("what")
    parser.add_argument("--for", dest="wish", required=True, help="the wish this ask unblocks")
    parser.add_argument("--rec", help="what you recommend; written as the ask's reading")


def cmd_wishes(args):
    try:
        home, _by, _thread = _context(args)
        buckets = {name: [] for name in ("in hand", "asks (yours)", "delivered", "heard", "done")}
        from .lifecycle import main_snapshot
        told = wishes(read_facts(home))
        heads = waits_on(told, main_snapshot(home))
        rows = sorted(told.values(), key=lambda w: (w.at, w.id), reverse=True)
        for w in rows:
            if (args.thread and w.thread != args.thread) or (not args.all and not w.open):
                continue
            bucket = ("done" if not w.open else "asks (yours)" if w.sender == "resident" else
                      "delivered" if w.state == "delivered" else "heard" if w.state == "heard" else "in hand")
            buckets[bucket].append(wish_line(w) + (waits_word(heads[w.id]) if w.id in heads else ""))
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


def _ask(argv):
    parser = argparse.ArgumentParser(prog="python -m brr.loom.runtime ask")
    _ask_arguments(parser)
    return cmd_ask(parser.parse_args(argv))


def _wishes(argv):
    parser = argparse.ArgumentParser(prog="python -m brr.loom.runtime wishes")
    _arguments(parser)
    parser.add_argument("--all", action="store_true")
    return cmd_wishes(parser.parse_args(argv))

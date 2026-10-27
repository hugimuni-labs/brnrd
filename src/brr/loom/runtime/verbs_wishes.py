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
from .waits import GRAMMAR
from .wishes import Say, waits_on, waits_word, wishes, wish_line


def _context(args):
    from .verbs_loom import _room
    from .lifecycle import own_thread
    if args.room or os.environ.get("BRNRD_ROOM"):
        if args.home:
            raise ValueError("--home is not allowed inside a strand room")
        room = _room(args.room)
        return home_of_room(room), "strand:" + strand_of_room(room), own_thread(room)
    return Home(resolve_home(args.home)[0]), "person:operator", None


def _declare(args, how: str) -> None:
    """This body said how it ends: the jack lets its first stop through."""
    from .jack import declare
    from .verbs_loom import _room
    declare(_room(args.room), how)


def say_span(raw: str, facts) -> Say:
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
    return Say(ident, off, size)


def wish_fact(facts, *, by, ident, says=(), line=None, thread=None, sender=None, parent=None, rec=None):
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
        "wish": ident, "says": [Say.read(say).row() for say in says],
        "from": sender or (current.sender if current else "person"),
        "line": line if line is not None else current.line,
        "thread": thread if thread is not None else current.thread,
    }
    if parent is not None:
        data["parent"] = parent
    if rec is not None:
        data["rec"] = rec
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
    parser.add_argument("--wait", help="what the park waits on: " + GRAMMAR)
    parser.add_argument("--until", help="when the park re-asks its thread if nothing resolved")
    parser.add_argument("--left", action="append", default=[],
                        help="a step still to do; repeat for each, the whole list (or: same)")


def cmd_wish(args):
    try:
        home, by, thread = _context(args)
        facts = read_facts(home)
        actions = any(x is not None for x in (args.read, args.result, args.accept, args.drop))
        left = _left_fact(args, facts, by) if args.left else None
        if left is not None and not actions:
            if args.say or any(x is not None for x in (args.line, args.sender, args.thread, args.parent)):
                raise ValueError("--left restates what is left; it takes no touch flags")
            append(home, left)
            _declare(args, "left")
            print(args.id)
            return 0
        if left is not None and args.result is None:
            raise ValueError("--left goes alone or with --result")
        if args.deliver and args.result is None:
            raise ValueError("--deliver requires --result")
        if args.ref is not None and args.accept is None:
            raise ValueError("--ref requires --accept")
        if (args.wait is not None or args.until is not None) and args.read is None:
            raise ValueError("--wait and --until go with --read \"waiting: …\"")
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
                if args.wait is not None and not args.read.startswith("waiting:"):
                    data["reading"] = "waiting: " + args.read
                if data["reading"].startswith("waiting:"):
                    data["wait"] = _wait(facts, args.id, args.wait, args.until)
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
        if fact.kind == "wish":
            from .lifecycle import CLOSED, read_thread, reopen_for
            if not by.startswith("strand:"):
                if read_thread(home, target).get("status") in CLOSED:
                    raise ValueError(f"wish: {target} is closed on main, and a wish placed there "
                                     "reopens it; place it from a strand room, which lands the reopen")
            else:
                from .verbs_loom import _room
                for reopened in reopen_for(_room(args.room), [fact]):
                    append(home, reopened)
        append(home, fact)
        if left is not None:
            append(home, left)
        # A park is a reading that says what it waits on, the one the board shows.
        if actions and kind == "wish.result" and by.startswith("strand:"):
            _declare(args, "result")
        elif actions and kind == "wish.read" and by.startswith("strand:") and "wait" in data:
            _declare(args, "park")
        print(fact.data["wish"])
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _wait(facts, ident, condition, until, default=None):
    """A park's ``wait``: its condition with the repo filled in, and its deadline."""
    from datetime import datetime, timezone
    from . import waits
    now = datetime.now(timezone.utc)
    found = wishes(facts)
    if condition is not None:
        condition = waits.canonical(condition, now, repo=lambda number: _repo(facts, number))
        if condition.startswith("wish:") and condition[5:] not in found:
            raise ValueError(f"--wait {condition}: no wish {condition[5:]}")
    return waits.park(found[ident], condition, until, now, found=found, default=default)


def _repo(facts, number):
    """The repo a bare ``#N`` names: a pull request opened on this ledger, else the loom's own origin."""
    if number is not None:
        repos = {f.data.get("repo") for f in facts if f.kind == "pr.opened"
                 and str(f.data.get("number")) == str(number)}
        if len(repos) == 1:
            return repos.pop()
    from .receipts import source_root
    from .selfrepo import git
    from .update import slug
    root = source_root()
    if not (root / ".git").exists():
        return None
    url = git(root, "remote", "get-url", "origin", check=False).stdout.strip()
    return slug(url) if url else None


def _left_fact(args, facts, by):
    """The whole list of what is left on a wish, latest wins; ``same`` restates the last one."""
    if not by.startswith("strand:"):
        raise ValueError("--left: only the body working a wish says what is left on it")
    current = wishes(facts).get(args.id)
    if current is None:
        raise ValueError(f"no wish {args.id}")
    steps = [" ".join(step.split()) for step in args.left]
    if any(not step for step in steps):
        raise ValueError("--left: a step is empty")
    if steps == ["none"]:
        raise ValueError("--left none: nothing left is a delivery: --result <kind>:<ref> --line … --deliver")
    strand = by.removeprefix("strand:")
    gen = max((int(f.data["gen"]) for f in facts if f.kind == "lease" and f.data.get("strand") == strand),
              default=0)
    data = {"wish": args.id, "steps": [] if steps == ["same"] else steps, "gen": gen}
    if steps == ["same"]:
        if current.left is None:
            raise ValueError(f"--left same: {args.id} has no left yet; write its steps")
        data["same"] = True
    return Fact(kind="wish.left", by=by, id="wish.left:" + mint(12), data=data)


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
        if args.default_at is not None and args.rec is None:
            raise ValueError("ask --default-at needs --rec: the default is what you recommend")
        if same is None:
            # The rec rides on the ask itself: a wish.read would read as the person's answer.
            fact = wish_fact(facts, by=by, ident="new", line=args.what, thread=thread,
                             sender="resident", parent=args.wish, rec=args.rec)
            from .lifecycle import reopen_for
            from .verbs_loom import _room
            # A body that settled its own thread and asks again holds open work there.
            for reopened in reopen_for(_room(args.room), [fact]):
                append(home, reopened)
            append(home, fact)
            ident = fact.data["wish"]
        else:
            ident = same.id
            print(f"ask: reused {ident}, the open ask with this line for {args.wish}", file=sys.stderr)
        if same is not None and args.rec is not None and same.rec != args.rec:
            append(home, wish_fact(facts, by=by, ident=ident, rec=args.rec))
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
            # The &ref is the mail's parent: a thread letter cites what it answers.
            body += f"Answer &{ident}, or ask onward with: ask \"<what>\" --for {ident}\n"
            write_send(room, to=head, sender=strand_of_room(room), body=body)
        if args.default_at is not None:
            # The asking wish parks on the ask; at its deadline the body is woken with its own rec.
            append(home, Fact(kind="wish.read", by=by, id="wish.read:" + mint(12), data={
                "wish": args.wish, "reading": f"waiting: ask {ident}: {args.what}", "by": "resident",
                "wait": _wait(facts, args.wish, "wish:" + ident, args.default_at, default=args.rec)}))
        _declare(args, "ask")
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
    parser.add_argument("--rec", help="what you recommend; kept on the ask as its rec, apart from the answer")
    parser.add_argument("--default-at", dest="default_at",
                        help="if nobody answers by then, you are woken to carry out your rec")


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

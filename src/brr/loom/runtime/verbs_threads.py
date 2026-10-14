"""The thread table has one command, in the runtime and the operator CLI."""

import argparse
import os
import sys


def add_threads_parser(subparsers) -> None:
    parser = subparsers.add_parser("threads")
    _threads_arguments(parser)
    parser.set_defaults(func=cmd_threads)


def _threads_arguments(parser) -> None:
    parser.add_argument("--home")
    parser.add_argument("--room")
    parser.add_argument("--due", action="store_true", help="list letters waiting for their due time")
    parser.add_argument("--force", metavar="THREAD", help="start a paced thread before its reset")
    parser.add_argument("--left", metavar="THREAD", nargs="?", const="",
                        help="inspect the rooms: alone, the table gains a `left` count; "
                             "with a thread, its retained paths and commits")


def cmd_threads(args) -> int:
    from .threads import thread_table, render_table, left_by_thread, render_left
    from .home import Home, home_of_room, resolve_home
    from .ledger import read_facts
    from .verbs_loom import _room
    if args.home is not None:
        home = Home(resolve_home(args.home)[0])
    elif args.room or os.environ.get('BRNRD_ROOM'):
        home = home_of_room(_room(args.room))
    else:
        home = Home(resolve_home()[0])
    if args.due:
        from .threads import due_table
        sys.stdout.write(due_table(home, read_facts(home)))
        return 0
    if args.force:
        from .lifecycle import read_thread, own_thread, CLOSED
        from .project import fold
        from .loom import pending_bodies, _start
        from .port import PortError
        try:
            data = read_thread(home, args.force)
            if not data or data.get('status') in CLOSED:
                raise PortError('force: thread must be open on main; its opener can reopen it with send --reopen')
            if args.room or os.environ.get('BRNRD_ROOM'):
                own = own_thread(_room(args.room))
                if data.get('opened-by') != 'thread:' + own:
                    raise PortError(f"force: only {data.get('opened-by') or 'the operator'} or the operator may start anyway")
            facts = read_facts(home)
            held = fold(facts).holder.get(args.force)
            pending = pending_bodies(fold(facts).accepted).get(held[0]) if held else None
            if not pending or pending.kind != 'body.parked':
                raise PortError('force: no paced start; the loom will start on the next owed letter')
            from .router import install_of
            if install_of(held[0]) != home.install_id():
                raise PortError('force: run this command on the install holding the paced start, or wait for its reset')
            bodies = {}
            facts = _start(home, held[0], args.force, held[1], bodies, facts, force=True)
            for body in bodies.values():
                body.close_log()
            if not bodies:
                raise PortError('force: start was taken or preparation failed; reread threads and attention for its owner and next act')
        except (PortError, ValueError) as exc:
            sys.stderr.write(str(exc) + '\n')
            return 1
    facts = read_facts(home)
    # The inventory runs git in every room and fetches each project source,
    # so the plain table never pays for it: only --left asks.
    if args.left is None:
        sys.stdout.write(render_table(thread_table(home, facts)))
        return 0
    left = left_by_thread(home, facts, args.left or None)
    if args.left:
        sys.stdout.write(render_left(args.left, left.get(args.left, [])))
        return 0
    rows = thread_table(home, facts)
    for row in rows:
        items = left.get(row['thread'], [])
        count = sum(item.count for item in items)
        if items:
            row['left'] = (str(count) if count else '') + ('?' if any(item.error for item in items) else '')
    sys.stdout.write(render_table(rows))
    return 0


def _threads(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m brr.loom.runtime threads")
    _threads_arguments(parser)
    return cmd_threads(parser.parse_args(argv))

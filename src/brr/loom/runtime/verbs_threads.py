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
    parser.add_argument("--force", metavar="THREAD", help="start a paced thread before its reset")
    parser.add_argument("--left", metavar="THREAD", help="list retained paths and commits in a thread's rooms")


def cmd_threads(args) -> int:
    from .threads import thread_table, render_table, left_by_thread, render_left
    from .home import Home, home_of_room
    from .ledger import read_facts
    from .verbs_loom import _room
    home = Home(args.home) if args.home else home_of_room(_room(args.room))
    if args.force:
        from .lifecycle import read_thread, own_thread, CLOSED
        from .project import fold
        from .loom import pending_bodies, _start
        from .port import PortError
        try:
            data = read_thread(home, args.force)
            if not data or data.get('status') in CLOSED:
                raise PortError('force: thread must be open on main; its opener can reopen it with send --reopen')
            if args.room or not args.home or os.environ.get('BRNRD_ROOM'):
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
    left = left_by_thread(home, facts, args.left)
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


def add_when_parser(subparsers) -> None:
    parser = subparsers.add_parser("when")
    _when_arguments(parser)
    parser.set_defaults(func=cmd_when)


def _when_arguments(parser) -> None:
    parser.add_argument("time", help="YYYY-MM-DDTHH:MM:SSZ, +<n>m, +<n>h, +<n>d, or clear")
    parser.add_argument("--room")


def cmd_when(args) -> int:
    from .lifecycle import land_when
    from .port import PortError
    from .selfrepo import SelfError
    from .verbs_loom import _room
    try:
        stored = land_when(_room(args.room), args.time)
    except (PortError, SelfError, ValueError, OSError) as exc:
        sys.stderr.write(str(exc) + "\n")
        return 1
    if stored:
        sys.stdout.write(stored + "\n")
    return 0


def _when(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m brr.loom.runtime when")
    _when_arguments(parser)
    return cmd_when(parser.parse_args(argv))

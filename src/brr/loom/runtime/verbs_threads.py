"""The thread table has one command, in the runtime and the operator CLI."""

import argparse
import sys


def add_threads_parser(subparsers) -> None:
    parser = subparsers.add_parser("threads")
    _threads_arguments(parser)
    parser.set_defaults(func=cmd_threads)


def _threads_arguments(parser) -> None:
    parser.add_argument("--home")
    parser.add_argument("--room")


def cmd_threads(args) -> int:
    from .threads import thread_table, render_table
    from .home import Home, home_of_room
    from .ledger import read_facts
    from .verbs_loom import _room
    home = Home(args.home) if args.home else home_of_room(_room(args.room))
    sys.stdout.write(render_table(thread_table(home, read_facts(home))))
    return 0


def _threads(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m brr.loom.runtime threads")
    _threads_arguments(parser)
    return cmd_threads(parser.parse_args(argv))

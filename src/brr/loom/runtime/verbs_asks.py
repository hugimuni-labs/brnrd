"""``asks`` and ``graph``: two read-only views, in the runtime and the operator CLI."""

from __future__ import annotations

import argparse
import os
import sys


def _home(args):
    from .home import Home, home_of_room, resolve_home
    from .verbs_loom import _room
    if args.home is None and (args.room or os.environ.get("BRNRD_ROOM")):
        return home_of_room(_room(args.room))
    return Home(resolve_home(args.home)[0])


def _arguments(parser) -> None:
    parser.add_argument("--home")
    parser.add_argument("--room")


def cmd_asks(args) -> int:
    from .asks import asks
    from .ledger import read_facts
    from .lifecycle import main_snapshot
    home = _home(args)
    sys.stdout.write(asks(read_facts(home), main_snapshot(home), args.thread, args.all))
    return 0


def cmd_graph(args) -> int:
    from .asks import graph, render_graph
    from .ledger import read_facts
    sys.stdout.write(render_graph(graph(read_facts(_home(args))),
                                  "strand" if args.strands else "thread", args.only))
    return 0


def asks_arguments(parser) -> None:
    _arguments(parser)
    parser.add_argument("--thread")
    parser.add_argument("--all", action="store_true", help="list done rows instead of their count")


def graph_arguments(parser) -> None:
    _arguments(parser)
    parser.add_argument("only", nargs="?", help="one thread, or with --strands one strand")
    parser.add_argument("--strands", action="store_true", help="strand → threads instead of thread → strands")


def add_graph_parser(subparsers) -> None:
    parser = subparsers.add_parser("graph")
    graph_arguments(parser)
    parser.set_defaults(func=cmd_graph)


def _asks(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m brr.loom.runtime asks")
    asks_arguments(parser)
    return cmd_asks(parser.parse_args(argv))


def _graph(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m brr.loom.runtime graph")
    graph_arguments(parser)
    return cmd_graph(parser.parse_args(argv))

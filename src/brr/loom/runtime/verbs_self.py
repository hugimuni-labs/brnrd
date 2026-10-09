"""Dispatchers for ``python -m brr.loom.runtime``.

Step 2 owns ``init`` and ``wake``. Step 1 adds ``loom``, ``send``, ``molt``,
``jack``; step 4a adds ``send-self``. Each step adds its verbs here and the
parent merges the dispatchers. This copy starts with step 2's two.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from brr.loom.runtime.selfrepo import SelfError, init_self, run_wake


def cmd_init(args: argparse.Namespace) -> int:
    try:
        print(init_self(Path(args.home), person=args.person, remote=args.remote))
    except SelfError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def cmd_wake(args: argparse.Namespace) -> int:
    try:
        owed = Path(args.owed) if args.owed else None
        return run_wake(Path(args.room), args.thread, owed)
    except SelfError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m brr.loom.runtime")
    sub = parser.add_subparsers(dest="verb", required=True)

    init_p = sub.add_parser("init", help="seed a new self under HOME/self")
    init_p.add_argument("--home", required=True)
    init_p.add_argument("--person", default=None)
    init_p.add_argument("--remote", default=None)
    init_p.set_defaults(func=cmd_init)

    wake_p = sub.add_parser("wake", help="run the clone's own wake recipe")
    wake_p.add_argument("--room", required=True)
    wake_p.add_argument("--thread", required=True)
    wake_p.add_argument("--owed", default=None)
    wake_p.set_defaults(func=cmd_wake)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

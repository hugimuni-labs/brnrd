"""Parsers for seeding a self and running its own wake recipe."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from brr.loom.runtime.selfrepo import SelfError, init_self, run_wake


def cmd_init(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m brr.loom.runtime init")
    parser.add_argument("--home", required=True)
    parser.add_argument("--person", default=None)
    parser.add_argument("--remote", default=None)
    args = parser.parse_args(argv)
    try:
        print(init_self(Path(args.home), person=args.person, remote=args.remote))
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

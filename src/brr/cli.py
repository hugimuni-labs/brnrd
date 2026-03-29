"""brr CLI."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from . import adopt
from . import runners
from . import telegram
from . import daemon
from . import status as status_mod


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="brr", description="AI agent daemon for Git repositories")
    parser.add_argument("--version", action="version", version=f"brr {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="adopt a repo for brr management")
    p.add_argument("url", nargs="?", default=None, help="clone URL (optional)")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("run", help="run a task through the executor")
    p.add_argument("instruction", help="what to do")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("status", help="show project state")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("auth", help="authenticate a connector")
    s = p.add_subparsers(dest="connector", required=True)
    s.add_parser("telegram").set_defaults(func=cmd_auth_telegram)

    p = sub.add_parser("connect", help="bind repo to a connector")
    s = p.add_subparsers(dest="connector", required=True)
    s.add_parser("telegram").set_defaults(func=cmd_connect_telegram)

    p = sub.add_parser("up", help="start the daemon")
    p.set_defaults(func=cmd_up)

    args = parser.parse_args(argv)
    return args.func(args)


def cmd_init(args):
    adopt.init_repo(args.url)

def cmd_run(args):
    runners.run_task(args.instruction)

def cmd_status(args):
    sys.stdout.write(status_mod.get_status() + "\n")

def cmd_auth_telegram(args):
    telegram.auth()

def cmd_connect_telegram(args):
    telegram.connect()

def cmd_up(args):
    daemon.start()

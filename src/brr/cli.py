"""Command line interface for brr.

This module defines a minimal CLI using argparse.  Each command delegates
to a function in the appropriate module.  Most implementations are
placeholders; real logic will be added iteratively.
"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from . import adopt
from . import gitops
from . import runners
from . import telegram
from . import daemon
from . import status as status_mod


def main(argv: list[str] | None = None) -> None:
    """Entry point for the brr CLI."""
    parser = argparse.ArgumentParser(prog="brr", description="AI agent daemon for Git repositories")
    parser.add_argument("--version", action="version", version=f"brr {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True, help="subcommand to run")

    # init
    init_parser = subparsers.add_parser("init", help="initialise the repo for AI operation")
    init_parser.set_defaults(func=cmd_init)

    # run
    run_parser = subparsers.add_parser("run", help="run a single task through the executor")
    run_parser.add_argument("instruction", help="natural language instruction to execute")
    run_parser.set_defaults(func=cmd_run)

    # status
    status_parser = subparsers.add_parser("status", help="show a concise snapshot of project state")
    status_parser.set_defaults(func=cmd_status)

    # report
    report_parser = subparsers.add_parser("report", help="generate a narrative project report")
    report_parser.set_defaults(func=cmd_report)

    # auth <connector>
    auth_parser = subparsers.add_parser("auth", help="authenticate a chat connector")
    auth_sub = auth_parser.add_subparsers(dest="provider", required=True, help="connector to authenticate")
    tg_parser = auth_sub.add_parser("telegram", help="authenticate Telegram")
    tg_parser.set_defaults(func=cmd_auth_telegram)

    # connect <connector>
    conn_parser = subparsers.add_parser("connect", help="bind repo to a chat connector")
    conn_sub = conn_parser.add_subparsers(dest="provider", required=True, help="connector to use")
    conn_tg = conn_sub.add_parser("telegram", help="connect to Telegram chat/topic")
    conn_tg.set_defaults(func=cmd_connect_telegram)

    # up
    up_parser = subparsers.add_parser("up", help="bring the repo under managed operation")
    up_parser.set_defaults(func=cmd_up)

    args = parser.parse_args(argv)
    # Dispatch to the appropriate function
    return args.func(args)


def cmd_init(args: argparse.Namespace) -> None:
    """Handle `brr init` command."""
    adopt.init_repo()


def cmd_run(args: argparse.Namespace) -> None:
    """Handle `brr run` command."""
    instruction = args.instruction
    runners.run_task(instruction)


def cmd_status(args: argparse.Namespace) -> None:
    """Handle `brr status` command."""
    summary = status_mod.get_status()
    sys.stdout.write(summary + "\n")


def cmd_report(args: argparse.Namespace) -> None:
    """Handle `brr report` command."""
    report = status_mod.generate_report()
    sys.stdout.write(report + "\n")


def cmd_auth_telegram(args: argparse.Namespace) -> None:
    """Handle `brr auth telegram` command."""
    telegram.auth()


def cmd_connect_telegram(args: argparse.Namespace) -> None:
    """Handle `brr connect telegram` command."""
    telegram.connect()


def cmd_up(args: argparse.Namespace) -> None:
    """Handle `brr up` command."""
    daemon.start()
"""``python -m brr.loom.runtime machines prefer <install>``: ask for inbox on one machine.
``machines pin <thread> <install>`` / ``machines unpin <thread>``: run a thread on one machine.

Each fact lands in this install's ledger and reaches the others on the ledger
shuttle. The holder releases inbox to the preferred install
(``Router._handover``); a pinned thread is granted only by its install
(``Router.route``), and a body seated elsewhere leaves.
"""

from __future__ import annotations

import argparse
import sys

from brr.daemon2.facts import Fact

from .home import HOME_THREAD, Home, check_install, mint, resolve_home
from .ledger import append


def _machines(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m brr.loom.runtime machines")
    parser.add_argument("--home")
    parser.add_argument("--install", default=None)
    sub = parser.add_subparsers(dest="verb", required=True)
    prefer = sub.add_parser("prefer", help="hand the inbox lease, and the connectors, to this install")
    prefer.add_argument("to", metavar="INSTALL")
    pin = sub.add_parser("pin", help="run this thread only on this install; it never fails over")
    pin.add_argument("thread")
    pin.add_argument("to", metavar="INSTALL")
    unpin = sub.add_parser("unpin", help="let this thread run wherever inbox is held")
    unpin.add_argument("thread")
    args = parser.parse_args(argv)
    root, _ = resolve_home(args.home)
    home = Home(root, install=args.install)
    to = None
    if args.verb != "unpin":
        try:
            to = check_install(args.to)
        except ValueError as exc:
            sys.stderr.write(f"{exc}\n")
            return 2
        if to != home.install_id() and not (root / "ledger" / "facts" / to).is_dir():
            # A typo would hand inbox, or the thread, to nobody: the bodies
            # here leave and nothing takes over.
            sys.stderr.write(f"{to}: no ledger from that install here; is it joined and shuttled?\n")
            return 2
    if args.verb != "prefer":
        if args.thread == HOME_THREAD:
            sys.stderr.write(f"{HOME_THREAD} follows the inbox lease; use `machines prefer`\n")
            return 2
        fact = append(home, Fact(kind="machines.pin", by="person:operator",
                                 id="machines.pin:" + mint(12),
                                 data={"thread": args.thread, "install": to, "from": home.install_id()}))
        where = f"runs only on {to}" if to else "runs wherever inbox is held"
        sys.stdout.write(f"{fact.id}: thread {args.thread} {where}\n")
        return 0
    fact = append(home, Fact(kind="machines.prefer", by="person:operator",
                             id="machines.prefer:" + mint(12),
                             data={"install": to, "from": home.install_id()}))
    sys.stdout.write(f"{fact.id}: the holder hands inbox to {to} within a minute\n")
    return 0

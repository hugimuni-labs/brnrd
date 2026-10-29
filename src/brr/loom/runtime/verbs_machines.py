"""``python -m brr.loom.runtime machines prefer <install>``: ask for inbox on one machine.

The fact lands in this install's ledger and reaches the holder on the ledger
shuttle; the holder releases inbox to that install (``Router._handover``).
"""

from __future__ import annotations

import argparse
import sys

from brr.daemon2.facts import Fact

from .home import Home, check_install, mint, resolve_home
from .ledger import append


def _machines(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m brr.loom.runtime machines")
    parser.add_argument("--home")
    parser.add_argument("--install", default=None)
    sub = parser.add_subparsers(dest="verb", required=True)
    prefer = sub.add_parser("prefer", help="hand the inbox lease, and the connectors, to this install")
    prefer.add_argument("to", metavar="INSTALL")
    args = parser.parse_args(argv)
    try:
        to = check_install(args.to)
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    root, _ = resolve_home(args.home)
    home = Home(root, install=args.install)
    if to != home.install_id() and not (root / "ledger" / "facts" / to).is_dir():
        # A typo would hand inbox to nobody: the holder's bodies leave and the
        # connectors stay down until it takes the lease back.
        sys.stderr.write(f"{to}: no ledger from that install here; is it joined and shuttled?\n")
        return 2
    fact = append(home, Fact(kind="machines.prefer", by="person:operator",
                             id="machines.prefer:" + mint(12),
                             data={"install": to, "from": home.install_id()}))
    sys.stdout.write(f"{fact.id}: the holder hands inbox to {to} within a minute\n")
    return 0

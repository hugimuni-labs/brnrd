"""``python -m brr.loom.runtime machines prefer <install>``: ask for inbox on one machine.
``machines priority <install>``: persist which install leads when available.
``machines enable <self-url>``: configure shared leases in strict mode.
``machines pin <thread> <install>`` / ``machines unpin <thread>``: run a thread on one machine.
``machines join <self-url>``: make an empty home a second machine of the resident (``installs.join``).

Each fact lands in this install's ledger and reaches the others on the ledger
shuttle. The holder releases inbox to the preferred install
(``Router._handover``); a pinned thread is granted only by its install
(``Router.route``), and a body seated elsewhere leaves.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from brr.daemon2.facts import Fact

from .home import HOME_THREAD, Home, check_install, mint, resolve_home
from .ledger import append
from .refusals import Parser, refuse


def _machines(argv: list[str]) -> int:
    parser = Parser(verb="machines", prog="python -m brr.loom.runtime machines")
    parser.add_argument("--home")
    parser.add_argument("--install", default=None)
    sub = parser.add_subparsers(dest="verb", required=True)
    name = sub.add_parser("name", help="name this computer (empty alias restores its computer name)")
    name.add_argument("alias")
    prefer = sub.add_parser("prefer", help="hand the inbox lease, and the connectors, to this install")
    prefer.add_argument("to", metavar="INSTALL")
    priority = sub.add_parser("priority", help="persist the install that leads whenever it is available")
    priority.add_argument("to", metavar="INSTALL")
    enable = sub.add_parser("enable", help="enable shared leases in strict mode on this install")
    enable.add_argument("url", metavar="SELF_URL")
    pin = sub.add_parser("pin", help="run this thread only on this install; it never fails over")
    pin.add_argument("thread")
    pin.add_argument("to", metavar="INSTALL")
    unpin = sub.add_parser("unpin", help="let this thread run wherever inbox is held")
    unpin.add_argument("thread")
    join = sub.add_parser("join", help="clone the self at this URL into an empty home, as a new install")
    join.add_argument("url", metavar="SELF_URL")
    args = parser.parse_args(argv)
    if args.verb == "join":
        from .installs import JoinError, join as join_home
        from .selfrepo import SelfError
        # Join creates a home, as setup does, and where setup would look for it.
        target = args.home or os.environ.get("BRNRD_HOME") or Path.home() / "brnrd"
        try:
            sys.stdout.write(join_home(resolve_home(target)[0], args.url) + "\n")
        except (JoinError, SelfError) as exc:
            return refuse("machines", exc, prefix="not joined: ")
        return 0
    root, _ = resolve_home(args.home)
    home = Home(root, install=args.install)
    if args.verb == "enable":
        from .installs import enable_leases
        enable_leases(root, args.url)
        sys.stdout.write("lease_remote is set; strict mode, ttl 180 s. Restart the loom before joining another machine.\n")
        return 0
    if args.verb == "name":
        from .config import load_config, set_value
        from .installs import display_name
        set_value(root, "name", args.alias.strip())
        sys.stdout.write(f"This computer is {display_name(load_config(root))}\n")
        return 0
    to = None
    if args.verb != "unpin":
        try:
            to = check_install(args.to)
        except ValueError as exc:
            return refuse("machines", exc, 2)
        if to != home.install_id() and not (root / "ledger" / "facts" / to).is_dir():
            # A typo would hand inbox, or the thread, to nobody: the bodies
            # here leave and nothing takes over.
            return refuse("machines", f"{to}: no ledger from that install here; is it joined and shuttled?", 2)
    if args.verb == "priority":
        from .config import load_config
        from .gitlease import Unreachable
        from .router import gate_for
        gate = gate_for(home, load_config(root))
        if gate is None:
            return refuse("machines", "enable shared leases with `machines enable SELF_URL` first", 2)
        try:
            if not gate.set_priority("inbox", to):
                return refuse("machines", "lease changed or is absent; retry after the loom acquires it", 2)
        except Unreachable as exc:
            return refuse("machines", exc, 2)
        sys.stdout.write(f"inbox priority is {to}, saved on the shared lease; return handback within one ttl on healthy ticks\n")
        return 0
    if args.verb != "prefer":
        if args.thread == HOME_THREAD:
            return refuse("machines", f"{HOME_THREAD} follows the inbox lease; use `machines prefer`", 2)
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

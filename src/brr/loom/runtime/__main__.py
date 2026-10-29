"""The loom CLI dispatches once; command parsers receive only their arguments."""

from __future__ import annotations

import importlib
import sys

VERBS = {
    "where": ("service", "cmd_where"),
    "config": ("service", "cmd_config"),
    "loom": ("verbs_loom", "_loom"),
    "send": ("verbs_loom", "_send"),
    "settle": ("verbs_loom", "_settle"),
    "shed": ("verbs_loom", "_molt"),
    "molt": ("verbs_loom", "_molt"),
    "jack": ("verbs_loom", "_jack"),
    "resume": ("verbs_loom", "_resume"),
    "drop": ("verbs_loom", "_drop"),
    "wish": ("verbs_wishes", "_wish"),
    "wishes": ("verbs_wishes", "_wishes"),
    "ask": ("verbs_wishes", "_ask"),
    "threads": ("verbs_threads", "_threads"),
    "asks": ("verbs_asks", "_asks"),
    "graph": ("verbs_asks", "_graph"),
    "face": ("verbs_faces", "_face"),
    "machines": ("verbs_machines", "_machines"),
    "attention": ("verbs_loom", "_attention"),
    "init": ("verbs_self", "cmd_init"),
    "wake": ("verbs_self", "cmd_wake"),
    "lib": ("verbs_self", "cmd_lib"),
    "send-self": ("verbs_merge", "main"),
    "publish": ("publish", "main"),
    "forge-token": ("gates", "main"),
    "reap": ("reap", "main"),
    "merge-driver": ("merge_driver", "main"),
}

# One line per verb for ``brnrd runtime --help``; a verb's own ``--help`` has the rest.
SUMMARY = {
    "send": "send a letter: a reply (--re), a note, a new thread, a timer (--at)",
    "wish": "what is left on a wish (--left), its result, a park (--wait)",
    "ask": "ask a person for a decision or an act, --for the wish it unblocks",
    "settle": "close a thread whose work is done",
    "shed": "hand the seat to a successor (molt is its old name)",
    "send-self": "land this room's self commits on main",
    "publish": "push a project branch and record its pull request",
    "lib": "the library: folders and repos the self works in",
    "threads": "every thread, who owes it and what can start it",
    "asks": "what is in hand, asked and unanswered, over every thread",
    "wishes": "the wishes of a thread",
    "graph": "threads and their strands",
    "attention": "parked bodies and notices nobody has settled",
    "face": "wear, change or drop a face",
    "machines": "the machines this home runs on",
    "where": "where the home, rooms and self are",
    "config": "show or edit the home config",
    "resume": "resume a paused tool child",
    "drop": "give up on a paused tool child",
    "init": "create a home and its self",
    "wake": "print the wake a room's body would get",
    "forge-token": "print the forge token bodies use",
    "reap": "remove a finished strand's room",
    "molt": "old name of shed",
    "loom": "the loom process itself (loom machinery)",
    "jack": "a body's hook (loom machinery)",
    "merge-driver": "git merge driver for the self (loom machinery)",
}


def usage() -> str:
    width = max(len(verb) for verb in VERBS)
    rows = [f"  {verb:<{width}}  {SUMMARY.get(verb, '')}".rstrip() for verb in VERBS]
    return "usage: brnrd runtime <verb> ...\n\nverbs:\n" + "\n".join(rows) + "\n\n`brnrd runtime <verb> --help` shows a verb's flags.\n"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] in (["-h"], ["--help"], ["help"]):
        sys.stdout.write(usage())
        return 0
    target = VERBS.get(argv[0]) if argv else None
    if target is None:
        if argv:
            sys.stderr.write(f"brnrd runtime: no verb {argv[0]!r}\n")
        sys.stderr.write(usage())
        return 2
    module, function = target
    command = getattr(importlib.import_module(f"brr.loom.runtime.{module}"), function)
    return int(command(argv[1:]))


if __name__ == "__main__":
    sys.exit(main())

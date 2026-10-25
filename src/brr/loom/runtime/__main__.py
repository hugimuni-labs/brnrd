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
    "molt": ("verbs_loom", "_molt"),
    "jack": ("verbs_loom", "_jack"),
    "resume": ("verbs_loom", "_resume"),
    "drop": ("verbs_loom", "_drop"),
    "wish": ("verbs_wishes", "_wish"),
    "wishes": ("verbs_wishes", "_wishes"),
    "ask": ("verbs_wishes", "_ask"),
    "migrate-plan": ("migrate", "_migrate_plan"),
    "threads": ("verbs_threads", "_threads"),
    "asks": ("verbs_asks", "_asks"),
    "graph": ("verbs_asks", "_graph"),
    "face": ("verbs_faces", "_face"),
    "attention": ("verbs_loom", "_attention"),
    "init": ("verbs_self", "cmd_init"),
    "wake": ("verbs_self", "cmd_wake"),
    "send-self": ("verbs_merge", "main"),
    "publish": ("publish", "main"),
    "merge-driver": ("merge_driver", "main"),
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    target = VERBS.get(argv[0]) if argv else None
    if target is None:
        sys.stderr.write(f"usage: python -m brr.loom.runtime {'|'.join(VERBS)} ...\n")
        return 2
    module, function = target
    command = getattr(importlib.import_module(f"brr.loom.runtime.{module}"), function)
    return int(command(argv[1:]))


if __name__ == "__main__":
    sys.exit(main())

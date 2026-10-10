"""The loom CLI dispatches once; command parsers receive only their arguments."""

from __future__ import annotations

import importlib
import sys

VERBS = {
    "loom": ("verbs_loom", "_loom"),
    "send": ("verbs_loom", "_send"),
    "molt": ("verbs_loom", "_molt"),
    "jack": ("verbs_loom", "_jack"),
    "attention": ("verbs_loom", "_attention"),
    "init": ("verbs_self", "cmd_init"),
    "wake": ("verbs_self", "cmd_wake"),
    "send-self": ("verbs_merge", "main"),
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

"""``python -m brr.loom.runtime <verb> …``: each step's verbs live in its own module."""

from __future__ import annotations

import importlib
import sys

VERBS = {
    "loom": "verbs_loom", "send": "verbs_loom", "molt": "verbs_loom", "jack": "verbs_loom",
    "attention": "verbs_loom",
    "init": "verbs_self", "wake": "verbs_self",
    "send-self": "verbs_merge",
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    module = VERBS.get(argv[0]) if argv else None
    if module is None:
        sys.stderr.write(f"usage: python -m brr.loom.runtime {'|'.join(VERBS)} ...\n")
        return 2
    return int(importlib.import_module(f"brr.loom.runtime.{module}").main(argv))


if __name__ == "__main__":
    sys.exit(main())

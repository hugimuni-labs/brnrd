"""``python -m brr.loom.runtime <verb>``.

Step 4a owns ``send-self``. Sibling strands add their verbs as further
branches of :func:`main`; the parent merges the dispatchers.
"""

from __future__ import annotations

import argparse
import sys

from brr.loom.runtime.merge import SendError, send_to_self


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        _usage()
        return 2
    verb, rest = args[0], args[1:]
    if verb == "send-self":
        return _cmd_send_self(rest)
    print(f"unknown verb: {verb}", file=sys.stderr)
    return 2


def _usage() -> None:
    print(
        "usage: python -m brr.loom.runtime send-self --room DIR",
        file=sys.stderr,
    )


def _cmd_send_self(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m brr.loom.runtime send-self")
    parser.add_argument("--room", required=True)
    ns = parser.parse_args(argv)
    try:
        outcome = send_to_self(ns.room)
    except SendError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return _emit(outcome)


def _emit(outcome) -> int:
    if outcome.status == "merged":
        print(outcome.sha)
        return 0
    if outcome.status == "stopped":
        print("stopped")
        for path in outcome.files:
            print(path)
        return 2
    if outcome.status == "failed":
        print(f"failed tries={outcome.tries}")
        for path in outcome.files:
            print(path)
        return 3
    if outcome.status == "refused":
        sys.stderr.write(outcome.stderr)
        if outcome.stderr and not outcome.stderr.endswith("\n"):
            sys.stderr.write("\n")
        return 1
    print(f"unknown outcome: {outcome.status}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

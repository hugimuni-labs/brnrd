"""The ``send-self`` command parser and its outcome rendering."""

from __future__ import annotations

import argparse
import sys

from brr.loom.runtime.merge import SendError, send_to_self


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m brr.loom.runtime send-self")
    parser.add_argument("--room", required=True)
    parser.add_argument("--widening", default=None)
    ns = parser.parse_args(argv)
    try:
        outcome = send_to_self(ns.room, widening=ns.widening)
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
    # send_to_self's remaining outcome is refused; no external status enters.
    sys.stderr.write(outcome.stderr)
    if outcome.stderr and not outcome.stderr.endswith("\n"):
        sys.stderr.write("\n")
    return 1

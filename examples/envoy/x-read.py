#!/usr/bin/env python3
"""The envoy's read half — on-demand X mentions + popularity stats.

Twin of ``x-post.py`` in this directory; see that file's docstring for
the installed-shim shape and rationale. This file holds nothing but its
own directory — the mechanics live in ``brr.envoy_x``. Run this script with
an interpreter where ``brr`` is importable (usually ``<repo>/.venv/bin/python3``).

    <python-with-brr> x-read.py             -> mentions since last look + metrics
    <python-with-brr> x-read.py --all       -> ignore the since-cursor this once
    <python-with-brr> x-read.py --json      -> machine shape
"""
import os
import sys

try:
    from brr import envoy_x
except ImportError as exc:
    # Only claim "wrong interpreter" when brr itself is what is missing.
    # A ModuleNotFoundError raised *inside* brr names some other module, and
    # telling that reader to switch interpreters sends them to a remedy that
    # cannot work -- the message would contradict the very check it tells
    # them to run. An ImportError that is not a ModuleNotFoundError (a stale
    # or half-copied install where ``brr`` imports but the submodule is gone)
    # carries name="brr" and is caught here on purpose: it used to fall
    # through as a bare traceback, which is the thing this guard exists to
    # stop.
    _missing = getattr(exc, "name", None)
    if _missing and _missing != "brr" and not _missing.startswith("brr."):
        raise
    raise SystemExit(
        "x-read.py needs the brr package, and this interpreter cannot import it.\n"
        f"  you ran: {sys.executable}\n"
        "Use an interpreter that already has brr -- usually <repo>/.venv/bin/python3 --\n"
        "or install brr into this one: pip install -e <repo>\n"
        "The system python3 on PATH usually cannot import brr; that is the common\n"
        "cause, but a checkout that was never installed anywhere lands here too."
    ) from exc

HERE = os.path.dirname(os.path.abspath(__file__))

if __name__ == "__main__":
    envoy_x.main_read(sys.argv[1:], HERE)

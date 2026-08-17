#!/usr/bin/env python3
"""The envoy's write half — post or reply as the account's X identity.

This is the **installed shim**: the mechanics now live in the product
tree (``brr.envoy_x``, covered by tests and the gate) so a class of bug
like the 2026-08-13 incident — a posting script that tweeted the literal
string ``"--help"`` because argv was payload — is caught in CI, not
discovered live. This file holds nothing but its own directory; every
path (env file, receipt log, refresh script) resolves relative to *here*,
so installing it is: drop this file (and its ``x-read.py`` twin) beside
the account's ``x-brnrd-resident.env`` / ``x-post-log.jsonl`` /
``x-refresh.py`` / ``x-read-state.json``, keep the surrounding files
where they already are, done. ``brr`` must be importable from wherever
this runs (an editable or regular install of this project).

CLI shape is byte-compatible with the account-home original — the sweep
contracts and schedule prompts cite the interpreter that can import ``brr``
(typically ``<repo>/.venv/bin/python3``) and this shim honors that invocation
unchanged:

    <python-with-brr> x-post.py "text"                     -> tweet
    <python-with-brr> x-post.py "text" --reply-to <id>     -> reply in thread
    <python-with-brr> x-post.py "text" --dry-run           -> print what would post
    <python-with-brr> x-post.py delete <tweet-id>          -> delete a post
    add --json for the raw API response

where ``<python-with-brr>`` is a Python interpreter where ``brr`` is importable
(e.g., an editable install of this project or the account home's own copy).
Usually: ``<repo>/.venv/bin/python3 x-post.py …``
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
        "x-post.py needs the brr package, and this interpreter cannot import it.\n"
        f"  you ran: {sys.executable}\n"
        "Use an interpreter that already has brr -- usually <repo>/.venv/bin/python3 --\n"
        "or install brr into this one: pip install -e <repo>\n"
        "The system python3 on PATH usually cannot import brr; that is the common\n"
        "cause, but a checkout that was never installed anywhere lands here too."
    ) from exc

HERE = os.path.dirname(os.path.abspath(__file__))

if __name__ == "__main__":
    envoy_x.main_post(sys.argv[1:], HERE)

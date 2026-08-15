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
except ModuleNotFoundError as exc:
    raise SystemExit(
        "x-post.py needs the brr package: this interpreter cannot import it. "
        "Run the script with an interpreter that has brr installed, usually:\n"
        "  <repo>/.venv/bin/python3 x-post.py […]\n"
        "or wherever 'python3 -c import\\ brr' works in your environment.\n"
        "The system 'python3' on the PATH often cannot import brr; "
        "that is usually the issue, not the package."
    ) from exc

HERE = os.path.dirname(os.path.abspath(__file__))

if __name__ == "__main__":
    envoy_x.main_post(sys.argv[1:], HERE)

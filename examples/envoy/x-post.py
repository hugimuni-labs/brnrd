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
contracts and schedule prompts cite ``python3 ~/brnrd/account/x-post.py
"text"`` and this shim honors that invocation unchanged:

    python3 x-post.py "text"                     -> tweet
    python3 x-post.py "text" --reply-to <id>     -> reply in thread
    python3 x-post.py "text" --dry-run           -> print what would post
    python3 x-post.py delete <tweet-id>          -> delete a post
    add --json for the raw API response
"""
import os
import sys

from brr import envoy_x

HERE = os.path.dirname(os.path.abspath(__file__))

if __name__ == "__main__":
    envoy_x.main_post(sys.argv[1:], HERE)

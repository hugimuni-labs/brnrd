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
except ModuleNotFoundError as exc:
    raise SystemExit(
        "x-read.py needs the brr package: this interpreter cannot import it. "
        "Run the script with an interpreter that has brr installed, usually:\n"
        "  <repo>/.venv/bin/python3 x-read.py […]\n"
        "or wherever 'python3 -c import\\ brr' works in your environment.\n"
        "The system 'python3' on the PATH often cannot import brr; "
        "that is usually the issue, not the package."
    ) from exc

HERE = os.path.dirname(os.path.abspath(__file__))

if __name__ == "__main__":
    envoy_x.main_read(sys.argv[1:], HERE)

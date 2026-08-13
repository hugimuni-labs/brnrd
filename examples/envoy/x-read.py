#!/usr/bin/env python3
"""The envoy's read half — on-demand X mentions + popularity stats.

Twin of ``x-post.py`` in this directory; see that file's docstring for
the installed-shim shape and rationale. This file holds nothing but its
own directory — the mechanics live in ``brr.envoy_x``.

    python3 x-read.py             -> mentions since last look + metrics
    python3 x-read.py --all       -> ignore the since-cursor this once
    python3 x-read.py --json      -> machine shape
"""
import os
import sys

from brr import envoy_x

HERE = os.path.dirname(os.path.abspath(__file__))

if __name__ == "__main__":
    envoy_x.main_read(sys.argv[1:], HERE)

"""The priced decision chain (`.links.jsonl`, `brnrd do --link`).

The measurement that produced the verb: a run held its seat across a
71-minute gate — ten `brnrd await` calls, ~250k weighted tokens, the single
largest expenditure of that run — and chose it ten separate times without
ever attaching a number to it. Nothing was hidden; the bar printed the spend
the whole time. What was missing was *attribution*.
"""

from __future__ import annotations

from pathlib import Path


from brr import links


def test_the_control_file_is_dot_prefixed(tmp_path):
    """`daemon._drain_outbox` skips dot-files outright.

    A bare `links.jsonl` in a live outbox would be swept up as an
    undelivered chat message and fail to parse as frontmatter — the exact
    reasoning `do.ASKS_CONTROL_NAME` records for its own name.
    """
    assert links.CONTROL_NAME.startswith(".")


# ── the CLI verb ────────────────────────────────────────────────────


# ── the cut: what a breached ceiling leaves behind ──────────────────



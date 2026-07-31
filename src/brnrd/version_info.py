"""What code is actually serving — the deployed-version read (#510 era).

"Is my merge live?" used to mean probing SvelteKit's ``version.json`` build
stamp and inferring. This module gives the backend an honest answer instead:
a ``build_info.txt`` dropped into the installed package at build time by
``scripts/stamp_build_info.py`` — the one writer every deploy surface calls
(commit sha from the CI build arg or a real clone, plus a UTC build stamp,
plus whether that first line is a real sha) — and the process start time.

Local/dev installs have no ``build_info.txt``; every field degrades to
``None`` rather than guessing — an absent answer is honest, a fabricated
one is not.

``build_info.txt``'s third line is the honesty fix (2026-07-30 incident):
the git-less build tree of the PaaS this backend used to run on made the sha
lookup fall through to an exported tree id, and ``commit`` reported that tree
id unconditionally — the field named "is my merge live?" could never actually
answer it. The tree-id rung is gone with that host (2026-07-31), but the third
line stays: it still separates *stamped with a real sha* from *stamped with
nothing*, and a file stamped before the fix has no third line at all, which
reads as unknown rather than as a guess.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_STARTED_AT = datetime.now(timezone.utc).isoformat()
_BUILD_INFO_PATH = Path(__file__).parent / "build_info.txt"


def build_info() -> dict[str, Any]:
    """The deployed build's identity, best-effort and never fabricated."""

    commit: str | None = None
    built_at: str | None = None
    try:
        lines = _BUILD_INFO_PATH.read_text(encoding="utf-8").splitlines()
        value = (lines[0].strip() or None) if lines else None
        built_at = (lines[1].strip() or None) if len(lines) > 1 else None
        source = (lines[2].strip() or None) if len(lines) > 2 else None
        # Only a build hook that recorded "this line is a real git sha" may
        # populate ``commit`` — a source-less (pre-fix) or ``tree`` file
        # means the first line is (or may be) the tree id, and reporting it
        # as ``commit`` is exactly the bug this field exists to end.
        if source == "git":
            commit = value
    except OSError:
        pass
    return {
        "commit": commit,
        "built_at": built_at,
        "started_at": _STARTED_AT,
    }

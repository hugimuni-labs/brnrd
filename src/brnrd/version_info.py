"""What code is actually serving — the deployed-version read (#510 era).

"Is my merge live?" used to mean probing SvelteKit's ``version.json`` build
stamp and inferring. This module gives the backend an honest answer instead:
a ``build_info.txt`` dropped into the installed package at build time by
``scripts/stamp_build_info.py`` — the one writer shared by the backend
Dockerfile and the Upsun build hook (commit sha from the CI build arg or a
real clone, else ``PLATFORM_TREE_ID``, plus a UTC build stamp, plus which of
the two the sha line actually is) — and the process start time.

Local/dev installs have no ``build_info.txt``; every field degrades to
``None`` rather than guessing — an absent answer is honest, a fabricated
one is not.

``build_info.txt``'s third line is the honesty fix (2026-07-30 incident):
the build tree the Upsun hook runs in has no ``.git``, so the sha lookup
always fell through to the tree id, and ``commit`` reported that tree id
unconditionally — the field named "is my merge live?" could never actually
answer it. A file stamped before this fix has no third line, and an absent
source reads as unknown rather than as a guessed ``"tree"`` — a two-line
``build_info.txt`` predates the distinction entirely, so guessing either way
would just relocate the same dishonesty.
"""

from __future__ import annotations

import os
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
        "tree_id": os.environ.get("PLATFORM_TREE_ID") or None,
        "started_at": _STARTED_AT,
    }

"""What code is actually serving — the deployed-version read (#510 era).

"Is my merge live?" used to mean probing SvelteKit's ``version.json`` build
stamp and inferring. This module gives the backend an honest answer instead:
a ``build_info.txt`` dropped into the installed package by the Upsun build
hook (commit sha when the build tree carries ``.git``, else
``PLATFORM_TREE_ID``, plus a UTC build stamp), and the process start time.

Local/dev installs have no ``build_info.txt``; every field degrades to
``None`` rather than guessing — an absent answer is honest, a fabricated
one is not.
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
        commit = (lines[0].strip() or None) if lines else None
        built_at = (lines[1].strip() or None) if len(lines) > 1 else None
    except OSError:
        pass
    return {
        "commit": commit,
        "built_at": built_at,
        "tree_id": os.environ.get("PLATFORM_TREE_ID") or None,
        "started_at": _STARTED_AT,
    }

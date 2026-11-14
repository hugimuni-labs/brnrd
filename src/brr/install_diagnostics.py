"""Read-only CLI diagnostics for competing executable installations."""

from __future__ import annotations

import os
from pathlib import Path
import sys


def warn_multiple_installations() -> None:
    """Name PATH entries by resolved executable; never run or modify them.

    Best effort: a disappearing/inaccessible entry must not break recovery.
    Different launchers may import the same package, so report paths rather
    than guessing versions or claiming which other package they would load.
    """
    found: dict[Path, Path] = {}
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory or os.curdir) / "brnrd"
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                found.setdefault(candidate.resolve(), candidate.absolute())
        except (OSError, RuntimeError):
            continue
    if len(found) < 2:
        return
    import brr

    print("warning: multiple brnrd installations on PATH", file=sys.stderr)
    invoked = sys.argv[0]
    if Path(invoked).name == "__main__.py":
        invoked = os.environ.get("BRNRD_CLI_ENTRY") or f"{sys.executable} -m brr"
    print(f"  invoked entry: {invoked}", file=sys.stderr)
    print(f"  imported package: {brr.__file__}", file=sys.stderr)
    print("  PATH entries (in search order):", file=sys.stderr)
    for resolved, entry in found.items():
        suffix = f" -> {resolved}" if entry != resolved else ""
        print(f"    {entry}{suffix}", file=sys.stderr)

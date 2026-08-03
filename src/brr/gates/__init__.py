"""Gates — transport adapters that create event files and deliver responses.

Each gate runs in its own thread (or as a standalone process) and
communicates with the daemon exclusively through the filesystem:
write events to ``.brr/inbox/``, read responses from ``.brr/responses/``.

See ``gates/README.md`` for the file protocol spec.
"""

from __future__ import annotations

import importlib

#: Every built-in gate brnrd ships, in catalog order. Single source of
#: truth for what was previously the same literal hardcoded three times
#: (``gates/runtime.py``, ``daemon.py``, ``updates.py`` — kb/design-
#: the-post.md's free-deletions list).
BUILTIN_GATES: tuple[str, ...] = ("telegram", "slack", "github", "cloud", "signal")


def import_gate(name: str):
    """Dynamically import a built-in gate module by name."""
    return importlib.import_module(f".{name}", package=__name__)

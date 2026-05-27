"""Polling cursor helpers — ISO formatting, initial lookback.

Brnrd-reusable: the managed backend uses the same ISO format for its
event cursors so a brnrd-managed inbox and an OSS-managed one stay
diff-able on the wire.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .constants import _INITIAL_LOOKBACK


def _format_iso(when: datetime) -> str:
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _initial_since() -> str:
    """ISO timestamp for the gate's first poll.

    Capping at one hour back keeps freshly-configured gates from
    re-processing a year of historical comments. Once the first poll
    sets a real cursor, this helper isn't called again.
    """
    return _format_iso(datetime.now(timezone.utc) - _INITIAL_LOOKBACK)

"""Gates — transport adapters that create event files and deliver responses.

Each gate runs in its own thread (or as a standalone process) and
communicates with the daemon exclusively through the filesystem:
write events to ``.brr/inbox/``, read responses from ``.brr/responses/``.

See ``gates/README.md`` for the file protocol spec.
"""

from __future__ import annotations

import importlib
from pathlib import Path

from .. import protocol


def write_event(inbox_dir: Path, source: str, body: str, **meta: object) -> Path:
    """Create an event file in the inbox. Returns the event file path."""
    return protocol.create_event(inbox_dir, source=source, body=body, **meta)


def list_done(inbox_dir: Path, source: str) -> list[dict]:
    """Find done events originating from *source*."""
    return protocol.list_done(inbox_dir, source)


def read_response(responses_dir: Path, event_id: str) -> str | None:
    """Read the response body for an event, or None if missing."""
    return protocol.read_response(responses_dir, event_id)


def cleanup(event_path: Path, response_path: Path | None) -> None:
    """Delete event and response files after delivery."""
    event_path.unlink(missing_ok=True)
    if response_path:
        response_path.unlink(missing_ok=True)


def import_gate(name: str):
    """Dynamically import a built-in gate module by name."""
    return importlib.import_module(f".{name}", package=__name__)

"""Adapters for today's event, response, portal and outbox wire."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import portals, protocol
from ..outbox import table


def public_event(event: dict[str, Any]) -> dict[str, Any]:
    return {key: str(value) if isinstance(value, Path) else value
            for key, value in event.items()}


@dataclass
class FileDoor:
    inbox: Path
    responses: Path

    def pending(self) -> list[dict[str, Any]]:
        return protocol.list_dispatchable(self.inbox)

    def get(self, event_id: str) -> dict[str, Any] | None:
        return protocol._read_event(self.inbox / f"{event_id}.md")

    def send(self, event: dict[str, Any], body: str,
             key: str, gen: int) -> dict[str, Any]:
        """Local response queue; a remote gate consumes its own file.

        The queue is deterministic by event id, so retrying the same body
        after a crash leaves the same carrier. A remote gate must still
        validate the idempotency key and generation at delivery.
        """
        target = protocol.response_path(self.responses, str(event["id"]))
        old = protocol.read_response(self.responses, str(event["id"]))
        if old is not None and old != body.strip():
            raise ValueError("response key already carries different body")
        protocol.write_response(self.responses, str(event["id"]), body)
        protocol.set_status(event, "done")
        return {"path": str(target), "key": key, "gen": gen}

    @staticmethod
    def parse_outbox(path: Path) -> tuple[str, dict[str, Any], str]:
        fm, body = protocol.parse_outbox_message(path.read_text(encoding="utf-8"))
        for row in table.ROWS:
            if row.selects is None or row.selects(fm):
                return row.key, fm, body.strip()
        raise AssertionError("outbox table has no fallback")

    @staticmethod
    def outbox_entries(outbox_dir: Path) -> list[Path]:
        if not outbox_dir.exists():
            return []
        return sorted(
            (path for path in outbox_dir.iterdir()
             if path.is_file() and not path.name.startswith(".")
             and path.name not in portals.CONTROL_NAMES
             and not portals.is_staging_name(path.name)),
            key=lambda path: (path.stat().st_mtime_ns, path.name),
        )

    @staticmethod
    def write_views(outbox_dir: Path, current_event: str,
                    events: list[dict[str, Any]], *,
                    phase: str, notices: list[dict[str, Any]],
                    await_state: dict[str, Any] | None = None) -> None:
        visible = [public_event(event) for event in events
                   if event.get("id") != current_event]
        portals.write_live_inbox(outbox_dir, current_event, visible)
        capsule = {
            "version": 1,
            "stage": "brnrd daemon run",
            "phase": phase,
            "current_event": current_event,
            "events": visible,
            "notices": notices,
            "await": await_state or {"armed": False, "resolved": False},
            "resources": {
                "quota": "unimplemented", "spend": "unimplemented",
                "context_window": "unimplemented",
                "coexisting_runs": "unimplemented",
                "remote_scm": "unimplemented",
            },
        }
        capsule["change_token"] = portals.content_token(capsule)
        portals.write_portal_state(outbox_dir, capsule)

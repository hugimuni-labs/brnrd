"""Adapters for today's event, response, portal and outbox wire."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from .. import portals, protocol
from ..outbox import table


_PORTAL_SHAPE = json.loads(
    resources.files("brr.daemon2").joinpath("portal_shape.json").read_text(
        encoding="utf-8"))


def public_event(event: dict[str, Any]) -> dict[str, Any]:
    return {key: str(value) if isinstance(value, Path) else value
            for key, value in event.items()}


@dataclass
class FileDoor:
    inbox: Path
    responses: Path
    letters: Any = None
    other_queues: tuple[tuple[Path, Path, str], ...] = ()

    def queues(self) -> tuple[tuple[Path, Path, str], ...]:
        return ((self.inbox, self.responses, ""), *self.other_queues)

    def event_paths(self):
        for inbox, _responses, _label in self.queues():
            yield from inbox.glob("*.md")

    def response_dir(self, event: dict[str, Any]) -> Path:
        path = Path(event.get("_path") or self.inbox / f"{event['id']}.md")
        for inbox, responses, _label in self.queues():
            if path.parent == inbox:
                return responses
        return self.responses

    @staticmethod
    def _label(event: dict[str, Any] | None, label: str) -> dict[str, Any] | None:
        if event is not None and label and not event.get("repo_label"):
            return {**event, "repo_label": label}
        return event

    def _project(self, event: dict[str, Any] | None) -> dict[str, Any] | None:
        if event is None or self.letters is None:
            return event
        state = self.letters.state(str(event["id"]))
        if state is None:
            return event
        status = {"pending": "pending", "claimed": "processing",
                  "answered": "done", "retired": ("done" if state.retirement
                  and state.retirement.get("why") == "also" else "noted")}[state.state]
        return {**event, "status": status}

    def pending(self) -> list[dict[str, Any]]:
        pending = []
        for inbox, _responses, label in self.queues():
            for event in protocol.list_dispatchable(inbox):
                projected = self._project(self._label(event, label))
                if projected is not None and projected["status"] in {"pending", "processing"}:
                    pending.append(projected)
        pending.sort(key=protocol._event_queue_sort_key)
        return pending

    def get(self, event_id: str) -> dict[str, Any] | None:
        for inbox, _responses, label in self.queues():
            event = protocol._read_event(inbox / f"{event_id}.md")
            if event is not None:
                return self._project(self._label(event, label))
        return None

    def send(self, event: dict[str, Any], body: str,
             key: str, gen: int, *,
             message_path: Path | None = None) -> dict[str, Any]:
        """Local response queue; a remote gate consumes its own file.

        The queue is deterministic by event id, so retrying the same body
        after a crash leaves the same carrier. A remote gate must still
        validate the idempotency key and generation at delivery.
        """
        responses = self.response_dir(event)
        target = protocol.response_path(responses, str(event["id"]))
        old = protocol.read_response(responses, str(event["id"]))
        if old is not None and old != body.strip():
            raise ValueError("response key already carries different body")
        protocol.write_response(responses, str(event["id"]), body,
                                message_path=message_path)
        # Today's gate delivery organ selects terminal carriers from the
        # event file. This is a compatibility projection of the letter fact;
        # daemon2 still reads the fact as authority on restart.
        raw = protocol._read_event(Path(event["_path"]))
        if raw is not None and raw.get("status") not in {"done", "delivered"}:
            protocol.set_status(raw, "done")
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
                    await_state: dict[str, Any] | None = None,
                    run_id: str = "", repo: str = "",
                    runner_name: str = "", branch: str = "",
                    current_replyable: bool = True,
                    controls: dict[str, Any] | None = None,
                    resources: dict[str, Any] | None = None) -> None:
        visible = [public_event(event) for event in events
                   if event.get("id") != current_event]
        portals.write_live_inbox(outbox_dir, current_event, visible)
        capsule = copy.deepcopy(_PORTAL_SHAPE)
        capsule["version"] = 1
        capsule["run"].update(id=run_id, event_id=current_event,
                              phase=phase, status=phase, repo=repo,
                              runner=runner_name, branch=branch)
        capsule["inbound"].update(current_event=current_event,
                                  current_event_replyable=current_replyable,
                                  events=visible)
        controls = controls or {}
        if controls.get("topic"):
            capsule["inbound"]["current_event_topic"]["confirmed"] = controls["topic"]
            capsule["run"]["topic"] = controls["topic"]
        capsule["attention"].update(
            pending_event_count=len(visible),
            pending_outbox_file_count=len(FileDoor.outbox_entries(outbox_dir)),
            needs_attention=bool(visible or notices))
        capsule["notices"] = notices
        capsule["await"] = await_state or {"armed": False}
        capsule["resources"]["runner"]["name"] = runner_name
        if resources:
            capsule["resources"].update(resources)
        capsule["outbound"]["pending_outbox_files"] = [
            path.name for path in FileDoor.outbox_entries(outbox_dir)]
        card = outbox_dir / ".card"
        if card.is_file() and not card.is_symlink():
            capsule["card"].update(active=True, text=card.read_text(encoding="utf-8"))
        capsule["name"]["written"] = bool(controls.get("name"))
        if controls.get("pr"):
            capsule["produce"]["pr"] = controls["pr"]
        capsule["change_token"] = portals.content_token(capsule)
        portals.write_portal_state(outbox_dir, capsule)

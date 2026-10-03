"""Project resident control files through the existing card and menu organs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .. import account, card, card_frame, menus, promises, protocol, relics, run_ledger, run_topic, updates
from ..run import Run


def _text(path: Path, cap: int = 64 * 1024) -> str:
    if path.is_symlink():
        return ""
    try:
        with path.open("rb") as stream:
            return stream.read(cap).decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


class ControlMirror:
    def __init__(self, runtime_dir: Path, account_context: Any = None):
        self.runtime_dir = runtime_dir
        self.account_context = account_context

    def _run(self, state: dict[str, Any]) -> Run:
        task = state.get("control_run")
        if task is not None:
            return task
        event = state["event"]
        task = Run(
            id=state["run_id"], event_id=str(event["id"]),
            body=str(event.get("body") or ""), env="host", status="running",
            source=str(event.get("source") or ""),
            conversation_key=state["conversation"],
            meta={k: v for k, v in event.items()
                  if k not in {"id", "body", "status", "source", "conversation_key", "_path"}
                  and isinstance(v, (str, int, float, bool))},
        )
        task.save(self.runtime_dir / "runs")
        state["control_run"] = task
        return task

    def tick(self, state: dict[str, Any]) -> dict[str, Any]:
        outbox = state["outbox"]
        card_path = outbox / ".card"
        if card_path.is_symlink():
            return {}
        body = _text(card_path)
        reported = relics.read_reported(outbox) if not (outbox / relics.CONTROL_NAME).is_symlink() else []
        promised = promises.read(outbox) if not (outbox / promises.CONTROL_NAME).is_symlink() else []
        topics = (run_ledger.read_run_topics_control(outbox)
                  if not (outbox / run_ledger.RUN_TOPICS_CONTROL_NAME).is_symlink() else None)
        name = (run_ledger.read_run_name_control(outbox)
                if not (outbox / run_ledger.RUN_NAME_CONTROL_NAME).is_symlink() else None)
        mood = (run_ledger.read_run_mood_control(outbox)
                if not (outbox / run_ledger.RUN_MOOD_CONTROL_NAME).is_symlink() else None)
        pr = relics._read_pr_control(outbox) if not (outbox / relics.PR_CONTROL_NAME).is_symlink() else None
        room = _text(outbox / ".room", 512).splitlines()[:1]
        control = run_topic.read_control(outbox)
        snapshot = {"name": name, "mood": mood, "topic": control.stamp if control else None,
                    "topics": topics or [], "room": room[0] if room else None,
                    "promises": promised, "relics": reported, "pr": pr}
        if body or any(snapshot.values()):
            if body:
                lines = card_frame.build_ledger({}, relics=reported)
                framed = card_frame.splice_ledger(body, card_frame.render_ledger(lines))
                if framed.strip() != body:
                    card_frame._write_card_if_unchanged(card_path, body + "\n", framed)
                    body = _text(card_path)
            task = self._run(state)
            topic = run_topic.settle(
                task, outbox_dir=outbox,
                account_home=(self.account_context.home_root
                              if self.account_context is not None else None),
                inbox_dir=Path(state["event"]["_path"]).parent,
                notice=lambda kind, message: state["notices"].append({
                    "kind": kind, "text": message, "verb": "topic"}),
                is_strand=state["is_child"])
            snapshot["topic"] = topic
            task.meta.update(run_name=name or "", mood=mood or "", pr=pr or "")
            projection = card.now_projection(body, limit=card.CARD_TEXT_MAX_CHARS)
            stamp = hashlib.sha256(json.dumps([projection, snapshot], sort_keys=True).encode()).hexdigest()
            if stamp != state.get("card_mirror_stamp"):
                task.save()
                if body and self.account_context is not None and self.account_context.enabled:
                    label = str(state["event"].get("repo_label") or
                                self.account_context.default_repo.label)
                    node = account.run_dir(self.account_context, label, task.id)
                    node.mkdir(parents=True, exist_ok=True)
                    protocol._atomic_write(node / "body.md", body + "\n")
                    if topics:
                        protocol._atomic_write(node / "topics.md",
                                               "topics: " + " ".join(topics) + "\n")
                if not state.get("card_mirror_started"):
                    updates.emit(self.runtime_dir, updates.UpdatePacket(
                        "run_created", state["conversation"], str(state["event"]["id"]),
                        {"run_id": task.id, "event_id": task.event_id,
                         "source": task.source}))
                    state["card_mirror_started"] = True
                updates.emit(self.runtime_dir, updates.UpdatePacket(
                    "card_composed", state["conversation"], task.event_id,
                    {"run_id": task.id, "event_id": task.event_id,
                     "text": projection, "course": card.course(body),
                     "controls": snapshot}))
                state["card_mirror_stamp"] = stamp

        menu_path = outbox / menus.MENU_NAME
        if menu_path.is_file() and not menu_path.is_symlink() and not state["is_child"]:
            raw = menu_path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if digest != state.get("menu_mirror_stamp"):
                state["menu_mirror_stamp"] = digest
                try:
                    menu, _ = menus.read_outbox_menu(
                        outbox, expected_thread=state["conversation"])
                    stored, superseded = menus.promote_menu(
                        self.runtime_dir, menu, run_id=state["run_id"])
                except (menus.MenuValidationError, OSError) as exc:
                    state["notices"].append({"kind": "dropped", "verb": "menu",
                                             "text": f"menu.json refused: {exc}"})
                else:
                    updates.emit(self.runtime_dir, updates.UpdatePacket(
                        "menu_composed", state["conversation"],
                        str(state["event"]["id"]),
                        {"run_id": state["run_id"], "menu": stored,
                         "superseded_menu_id": superseded}))
        return snapshot

    def finish(self, state: dict[str, Any], returncode: int) -> None:
        task = state.get("control_run")
        if task is None:
            return
        kind = ("held" if state.get("cut") or task.status == "held" else
                "stopped" if state.get("halted") else
                "done" if returncode == 0 else "failed")
        task.status = "held" if kind == "held" else "done" if kind == "done" else "error"
        task.save()
        updates.emit(self.runtime_dir, updates.UpdatePacket(
            kind, state["conversation"], task.event_id,
            {"run_id": task.id, "event_id": task.event_id}))

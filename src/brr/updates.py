"""Lifecycle update packets — gate-agnostic task progress events.

The daemon emits typed packets for task lifecycle moments. They are
persisted to the conversation log
(``.brr/conversations/<key>.ndjson``) and optionally rendered by gates
(Telegram, Slack, Git, CLI). The core stays gate-agnostic; gates may
opt in to a ``render_update(brr_dir, packet)`` hook.

Packet types are stable identifiers — gates branch on them to decide
how (or whether) to surface the event to a human.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import conversations


PACKET_TYPES = (
    "event_received",
    "task_created",
    "env_prepared",
    "container_started",
    "run_started",
    "attempt_started",
    "attempt_failed",
    "retrying",
    "artifact_created",
    "finalizing",
    "container_preserved",
    "push_started",
    "push_done",
    "done",
    "failed",
    "conflict",
)


@dataclass
class UpdatePacket:
    """A single lifecycle update.

    *conversation_key* is the gate-thread key (e.g. ``telegram:123:``)
    used to route the packet to the right conversation log. May be
    empty for orphan events; in that case the packet is rendered to
    console but not persisted.
    """

    type: str
    conversation_key: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return {
            "type": self.type,
            **self.payload,
        }


_QUIET_TYPES = {
    "event_received",
    "artifact_created",
    "container_started",
    "container_preserved",
}


def emit(brr_dir: Path, packet: UpdatePacket) -> None:
    """Persist *packet* to its conversation log and notify gates.

    The packet is appended to the gate thread's append-only log,
    rendered to the daemon console for operator visibility, and then
    offered to any gate that exposes a ``render_update`` hook.
    Failures inside renderers are swallowed — lifecycle persistence
    must succeed even if a gate is misconfigured.
    """
    if packet.type not in PACKET_TYPES:
        return
    if packet.conversation_key:
        conversations.append_update(
            brr_dir,
            packet.conversation_key,
            type=packet.type,
            payload=packet.payload or {},
        )
    _render_console(packet)
    _dispatch_to_gates(brr_dir, packet)


def emit_many(brr_dir: Path, packets: list[UpdatePacket]) -> None:
    for packet in packets:
        emit(brr_dir, packet)


def _render_console(packet: UpdatePacket) -> None:
    if packet.type in _QUIET_TYPES:
        return
    payload = packet.payload or {}
    bits = [f"[brr:update] {packet.type}"]
    if packet.conversation_key:
        bits.append(f"conv={packet.conversation_key}")
    for key in ("task_id", "event_id", "branch", "stage", "kind", "error"):
        if key in payload and payload[key] not in (None, ""):
            bits.append(f"{key}={payload[key]}")
    print(" ".join(bits), file=sys.stdout, flush=False)


def _dispatch_to_gates(brr_dir: Path, packet: UpdatePacket) -> None:
    from .gates import import_gate

    for name in ("telegram", "slack", "git_gate"):
        try:
            mod = import_gate(name)
        except ImportError:
            continue
        renderer = getattr(mod, "render_update", None)
        if renderer is None:
            continue
        try:
            renderer(brr_dir, packet)
        except Exception:
            # Gate-side rendering must never break the daemon.
            continue

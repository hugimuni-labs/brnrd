"""Lifecycle update packets — gate-agnostic task progress events.

The daemon emits typed packets for task lifecycle moments. They are
persisted to the conversation log (``.brr/conversations/<safe-key>/
<event-id>.jsonl``) and optionally rendered by gates (Telegram, Slack,
GitHub, CLI). The core stays gate-agnostic; gates may
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
    "synced",
    "task_created",
    "env_prepared",
    "container_started",
    "run_started",
    "attempt_started",
    "attempt_failed",
    "retrying",
    "artifact_created",
    "heartbeat",
    "finalizing",
    "container_preserved",
    "push_started",
    "push_done",
    "kb_maintenance_done",
    "done",
    "failed",
    "conflict",
)


@dataclass
class UpdatePacket:
    """A single lifecycle update.

    *conversation_key* is the gate-thread key (e.g. ``telegram:123:``)
    used to route the packet to the right conversation directory. May
    be empty for orphan events; in that case the packet is rendered to
    console but not persisted.

    *event_id* selects the per-event-pipeline jsonl file under that
    directory. The contention-free conversation layer (see
    ``kb/design-concurrent-execution.md``) routes every record one
    worker emits into the same ``<event-id>.jsonl`` so concurrent
    workers never share a file. Packets without ``event_id`` fall
    through to the orphan log so a buggy emitter is observable rather
    than silently dropped.
    """

    type: str
    conversation_key: str = ""
    event_id: str = ""
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
    # Heartbeats fire every 30s during a run; logging each one would
    # bury the meaningful packets. They still flow through the gate
    # renderer (which folds them into the live elapsed counter).
    "heartbeat",
    # kb maintenance is best-effort housekeeping; the operator sees
    # the outcome on the response card, no need to print every pass
    # to the daemon console.
    "kb_maintenance_done",
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
        # Prefer the explicit field, but fall back to a payload-provided
        # event_id so callers migrating to the field-based shape don't
        # silently drop into the orphan log during transition.
        event_id = packet.event_id or str(packet.payload.get("event_id") or "")
        conversations.append_update(
            brr_dir,
            packet.conversation_key,
            type=packet.type,
            payload=packet.payload or {},
            event_id=event_id,
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

    for name in ("telegram", "slack", "github", "cloud"):
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

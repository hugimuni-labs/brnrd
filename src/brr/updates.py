"""Lifecycle update packets — gate-agnostic stream events.

The daemon emits typed packets for stream lifecycle moments. They are
persisted to ``.brr/streams/<stream-id>/events.ndjson`` and optionally
rendered by gates (Telegram, Slack, Git, CLI). The core stays gate-
agnostic; gates may opt in to a ``render_update(brr_dir, packet)``
hook.

Packet types are stable identifiers — gates branch on them to decide
how (or whether) to surface the event to a human.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import stream


PACKET_TYPES = (
    "stream_created",
    "event_received",
    "task_created",
    "triage_done",
    "run_started",
    "artifact_created",
    "needs_context",
    "done",
    "failed",
    "conflict",
)


@dataclass
class UpdatePacket:
    """A single lifecycle update."""

    type: str
    stream_id: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "stream_id": self.stream_id,
            **self.payload,
        }


_QUIET_TYPES = {"event_received", "artifact_created"}


def emit(brr_dir: Path, packet: UpdatePacket) -> None:
    """Persist *packet* to its stream's events log and notify gates.

    The packet is appended to the stream's append-only event log,
    rendered to the daemon console for operator visibility, and then
    offered to any gate that exposes a ``render_update`` hook.
    Failures inside renderers are swallowed — lifecycle persistence
    must succeed even if a gate is misconfigured.
    """
    if packet.type not in PACKET_TYPES:
        return
    record = {"ts": stream._now_iso(), **packet.to_record()}
    path = stream.events_path(brr_dir, packet.stream_id)
    stream._append_jsonl(path, record)
    _render_console(packet)
    _dispatch_to_gates(brr_dir, packet)


def emit_many(brr_dir: Path, packets: list[UpdatePacket]) -> None:
    for packet in packets:
        emit(brr_dir, packet)


def _render_console(packet: UpdatePacket) -> None:
    if packet.type in _QUIET_TYPES:
        return
    payload = packet.payload or {}
    bits = [f"[brr:update] {packet.type}", f"stream={packet.stream_id}"]
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

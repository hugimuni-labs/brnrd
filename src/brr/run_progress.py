"""Run progress — gate-agnostic projection over conversation log records.

Conversation logs (``.brr/conversations/<key>.ndjson``) capture every
fact the daemon emits about an event/task: the event arrival, the
task row, lifecycle update packets, and artifact records. This module
folds those records into a compact ``RunProgressView`` that gates and
local diagnostics can render the same way.

The projection is read-only and does not mutate conversation state.
Renderers should treat it as the canonical view of a single task's
execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import conversations as conversations_mod


# ── States and phases ───────────────────────────────────────────────


PHASES = (
    "queued",
    "preparing",
    "running",
    "finalizing",
    "delivering",
    "delivered",
    "failed",
    "conflict",
)

STATES = ("active", "succeeded", "failed")


_PHASE_BY_PACKET: dict[str, str] = {
    "event_received": "queued",
    "task_created": "preparing",
    "env_prepared": "preparing",
    "container_started": "running",
    "attempt_started": "running",
    "attempt_failed": "running",
    "retrying": "running",
    "run_started": "running",
    "artifact_created": "running",
    "finalizing": "finalizing",
    "container_preserved": "finalizing",
    "push_started": "delivering",
    "push_done": "delivered",
    "done": "delivered",
    "failed": "failed",
    "conflict": "conflict",
}


_TERMINAL_STATE: dict[str, str] = {
    "done": "succeeded",
    "failed": "failed",
    "conflict": "failed",
}


# ── View dataclass ──────────────────────────────────────────────────


@dataclass
class RunProgressView:
    """Snapshot of a single task's execution, derived from conversation
    records.

    Renderers (gate cards, local diagnostics) consume this struct.
    Treat fields as advisory — older logs may lack newer record types.
    """

    conversation_key: str
    task_id: str | None
    phase: str = "queued"
    state: str = "active"
    branch_name: str | None = None
    base_branch: str | None = None
    env: str | None = None
    attempt: int = 0
    started_at: str | None = None
    updated_at: str | None = None
    detail: str = ""
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    container_ids: list[str] = field(default_factory=list)
    response_path: str | None = None
    error: str | None = None
    event_id: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state in {"succeeded", "failed"}

    def status_label(self) -> str:
        """Short human label, e.g. 'running', 'done', 'failed'."""
        if self.state == "succeeded":
            return "done"
        if self.state == "failed":
            return "failed" if self.phase != "conflict" else "conflict"
        return self.phase or "active"


# ── Projection ──────────────────────────────────────────────────────


def project_task(
    brr_dir: Path,
    conversation_key: str,
    task_id: str,
) -> RunProgressView | None:
    """Project the latest progress for a given (conversation, task)."""
    if not conversation_key:
        return None
    records = conversations_mod.read_records(brr_dir, conversation_key)
    if not records:
        return None
    return _project(records, conversation_key=conversation_key, task_id=task_id)


def project_conversation_latest(
    brr_dir: Path,
    conversation_key: str,
) -> RunProgressView | None:
    """Project the most recent task's progress for a conversation.

    Useful when a gate wants to show "what is currently happening" in
    a thread without tracking individual task IDs.
    """
    if not conversation_key:
        return None
    records = conversations_mod.read_records(brr_dir, conversation_key)
    latest_task_id = _latest_task_id(records)
    if latest_task_id is None:
        return None
    return _project(
        records, conversation_key=conversation_key, task_id=latest_task_id,
    )


def _latest_task_id(records: list[dict[str, Any]]) -> str | None:
    for record in reversed(records):
        tid = record.get("task_id")
        if tid:
            return str(tid)
    return None


def _project(
    records: list[dict[str, Any]],
    *,
    conversation_key: str,
    task_id: str,
) -> RunProgressView:
    view = RunProgressView(
        conversation_key=conversation_key,
        task_id=task_id,
    )

    last_ts: str | None = None
    for record in records:
        if record.get("task_id") not in (None, task_id):
            # Records that mention some other task are skipped.
            continue
        kind = record.get("kind")
        ts = record.get("ts")
        if ts and record.get("task_id") == task_id:
            view.updated_at = ts
            last_ts = ts

        if kind == "task":
            view.branch_name = record.get("branch_name") or view.branch_name
            view.base_branch = record.get("base_branch") or view.base_branch
            view.env = record.get("env") or view.env
            view.event_id = record.get("event_id") or view.event_id
            continue

        if kind == "artifact":
            view.artifacts.append(record)
            if record.get("artifact_kind") == "response" and record.get("path"):
                view.response_path = str(record["path"])
            continue

        if kind != "update":
            continue

        ptype = record.get("type")
        if not ptype:
            continue

        if ptype == "task_created":
            view.env = record.get("env") or view.env
            view.event_id = record.get("event_id") or view.event_id
        elif ptype == "env_prepared":
            view.env = record.get("env") or view.env
            view.branch_name = record.get("branch_name") or view.branch_name
        elif ptype == "container_started":
            cid = record.get("container")
            if cid and cid not in view.container_ids:
                view.container_ids.append(str(cid))
        elif ptype == "container_preserved":
            preserved = record.get("containers")
            if isinstance(preserved, list):
                for cid in preserved:
                    if cid not in view.container_ids:
                        view.container_ids.append(str(cid))
            elif preserved and preserved not in view.container_ids:
                view.container_ids.append(str(preserved))
        elif ptype == "attempt_started":
            attempt = record.get("attempt")
            if isinstance(attempt, int):
                view.attempt = attempt
            view.started_at = view.started_at or ts
        elif ptype == "run_started":
            view.started_at = view.started_at or ts
            view.attempt = view.attempt or 1
        elif ptype == "attempt_failed":
            reason = record.get("reason")
            if reason:
                view.detail = f"attempt {record.get('attempt', view.attempt)} failed: {reason}"
        elif ptype == "retrying":
            attempt = record.get("attempt")
            if isinstance(attempt, int):
                view.attempt = attempt
            view.detail = f"retry attempt {view.attempt}"
        elif ptype == "artifact_created":
            label = record.get("label") or record.get("kind") or "artifact"
            view.detail = f"artifact: {label}"
        elif ptype == "finalizing":
            stage = record.get("stage")
            # Don't clobber a terminal explanation: once the projection
            # knows a task is failed/conflict, the failure detail is
            # more useful to the operator than "finalizing (failed)".
            if view.state == "active":
                view.detail = f"finalizing ({stage})" if stage else "finalizing"
        elif ptype == "push_started":
            view.detail = "pushing changes"
        elif ptype == "push_done":
            commits = record.get("commits")
            view.detail = (
                f"pushed {commits} commit(s)" if commits else "pushed"
            )
        elif ptype == "failed":
            view.state = "failed"
            stage = record.get("stage")
            err = record.get("error")
            exit_code = record.get("exit_code")
            timed_out = bool(record.get("timed_out"))
            bits: list[str] = []
            if timed_out:
                bits.append("timed out")
            elif stage:
                bits.append(f"stage={stage}")
            if exit_code not in (None, "") and not timed_out:
                bits.append(f"exit {exit_code}")
            if err:
                bits.append(str(err))
            view.detail = " · ".join(bits) or "failed"
            view.error = err if isinstance(err, str) else view.error
        elif ptype == "conflict":
            view.state = "failed"
            view.detail = (
                f"merge conflict on {record.get('branch')}"
                if record.get("branch") else "merge conflict"
            )
        elif ptype == "done":
            view.state = "succeeded"
            view.detail = view.detail or "done"

        new_phase = _PHASE_BY_PACKET.get(ptype)
        if new_phase is not None:
            if view.state in {"succeeded", "failed"}:
                view.phase = _PHASE_BY_PACKET.get(
                    "done" if view.state == "succeeded" else "failed",
                    view.phase,
                )
                if ptype in _TERMINAL_STATE:
                    view.phase = new_phase
            else:
                view.phase = new_phase

        if ptype in _TERMINAL_STATE:
            view.state = _TERMINAL_STATE[ptype]

    if last_ts and not view.updated_at:
        view.updated_at = last_ts

    return view


# ── Rendering ───────────────────────────────────────────────────────


def render_text(view: RunProgressView, *, compact: bool = True) -> str:
    """Render a RunProgressView for human consumption.

    *compact* mode is the chat-surface card: header line plus the phase
    and any genuinely actionable detail (retry count when retrying, error
    message on failure). Branch / env / response paths are dev-side noise
    in a chat reply — they live in the verbose form for ``brr status``
    and ``brr inspect``.
    """
    lines: list[str] = []
    header = _header_line(view)
    lines.append(header)

    rows: list[tuple[str, str]] = []
    rows.append(("phase", _phase_text(view)))

    if compact:
        # Show retry-attempt counter only while the run is still active —
        # once a task is delivered/failed, the chat reader doesn't need
        # the attempt number.
        if view.attempt > 1 and not view.is_terminal:
            rows.append(("attempt", str(view.attempt)))
        if view.error:
            rows.append(("error", view.error))
        elif view.state == "failed" and view.detail:
            rows.append(("detail", view.detail))
    else:
        branch_text = _branch_text(view)
        if branch_text:
            rows.append(("branch", branch_text))
        if view.env:
            rows.append(("env", view.env))
        if view.attempt and view.attempt > 1:
            rows.append(("attempt", str(view.attempt)))
        if view.detail:
            rows.append(("last", view.detail))
        if view.error:
            rows.append(("error", view.error))
        if view.container_ids:
            rows.append(("containers", ", ".join(view.container_ids)))
        if view.response_path and view.is_terminal:
            rows.append(("response", view.response_path))

    if rows:
        lines.append("")
        for key, value in rows:
            lines.append(f"{key}: {value}")

    if not compact and view.artifacts:
        lines.append("")
        lines.append(f"artifacts ({len(view.artifacts)}):")
        for artifact in view.artifacts[-5:]:
            label = artifact.get("label") or artifact.get("artifact_kind") or "artifact"
            path = artifact.get("path", "")
            lines.append(f"  - {label} -> {path}")

    return "\n".join(lines).rstrip() + "\n"


def _header_line(view: RunProgressView) -> str:
    bits = ["brr"]
    if view.task_id:
        bits.append(view.task_id)
    bits.append(view.status_label())
    return " · ".join(bits)


def _phase_text(view: RunProgressView) -> str:
    phase = view.phase or "queued"
    if view.state == "succeeded":
        return "delivered"
    if view.state == "failed":
        return phase if phase in ("failed", "conflict") else "failed"
    return phase


def _branch_text(view: RunProgressView) -> str:
    if view.branch_name and view.base_branch:
        return f"{view.branch_name} <- {view.base_branch}"
    if view.branch_name:
        return view.branch_name
    return ""


# ── Terminal-state introspection ────────────────────────────────────


def is_terminal_packet(packet_type: str) -> bool:
    """Return True if the packet type represents a terminal state."""
    return packet_type in _TERMINAL_STATE


def task_id_from_packet(packet: Any) -> str | None:
    """Extract a task ID from an UpdatePacket (or compatible mapping)."""
    payload = getattr(packet, "payload", None)
    if payload is None and isinstance(packet, dict):
        payload = packet
    if not isinstance(payload, dict):
        return None
    tid = payload.get("task_id")
    if tid:
        return str(tid)
    return None

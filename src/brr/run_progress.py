"""Run progress — gate-agnostic projection over conversation log records.

Conversation logs (under ``.brr/conversations/<safe-key>/``, one
``<event-id>.jsonl`` per pipeline) capture every fact the daemon emits
about an event/task: the event arrival, the task row, lifecycle update
packets, and artifact records. This module
folds those records into a compact ``RunProgressView`` that gates and
local diagnostics can render the same way.

The projection is read-only and does not mutate conversation state.
Renderers should treat it as the canonical view of a single task's
execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
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
class PhaseEntry:
    """One step in the run's vertical timeline.

    The strike-through "log" rendering on chat gates is built from the
    list of these entries: closed entries become struck-through, the
    last open entry is the live "this is what's happening now" line.
    """

    name: str  # canonical: preparing | running | finalizing | delivered | failed | conflict
    started_at: str | None = None
    ended_at: str | None = None
    attempt: int | None = None  # set on running entries when retries happen
    detail: str | None = None  # appended after duration on terminal/closed entries


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
    display_base: str | None = None
    env: str | None = None
    runner_name: str | None = None
    attempt: int = 0
    started_at: str | None = None
    updated_at: str | None = None
    detail: str = ""
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    container_ids: list[str] = field(default_factory=list)
    response_path: str | None = None
    error: str | None = None
    event_id: str | None = None
    phase_history: list[PhaseEntry] = field(default_factory=list)
    push_commits: int | None = None
    push_ok: bool = True
    push_error: str | None = None
    view_url: str | None = None
    sync_summary: str | None = None

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


_TERMINAL_PHASE_NAMES = {"delivered", "failed", "conflict"}


def _open_phase(view: RunProgressView, name: str, ts: str | None,
                *, attempt: int | None = None) -> None:
    """Append a new phase entry, closing the previous open one at *ts*."""
    if view.phase_history:
        last = view.phase_history[-1]
        if last.ended_at is None and last.name not in _TERMINAL_PHASE_NAMES:
            last.ended_at = ts
    view.phase_history.append(
        PhaseEntry(name=name, started_at=ts, attempt=attempt),
    )


def _close_open_phase(view: RunProgressView, ts: str | None) -> None:
    if view.phase_history:
        last = view.phase_history[-1]
        if last.ended_at is None and last.name not in _TERMINAL_PHASE_NAMES:
            last.ended_at = ts


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
            view.display_base = (
                record.get("target_branch")
                or record.get("expected_publish_branch")  # compat: old records
                or view.display_base
            )
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

        if ptype == "synced":
            summary = record.get("summary")
            if isinstance(summary, str) and summary:
                view.sync_summary = summary
            elif record.get("error"):
                view.sync_summary = f"sync error: {record['error']}"
            continue

        if ptype == "task_created":
            view.env = record.get("env") or view.env
            view.event_id = record.get("event_id") or view.event_id
            _open_phase(view, "preparing", ts)
        elif ptype == "env_prepared":
            view.env = record.get("env") or view.env
            view.branch_name = record.get("branch_name") or view.branch_name
            view.display_base = (
                record.get("target_branch")
                or record.get("expected_publish_branch")  # compat: old records
                or view.display_base
            )
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
            _open_phase(view, "running", ts, attempt=attempt or view.attempt or 1)
        elif ptype == "run_started":
            view.started_at = view.started_at or ts
            view.attempt = view.attempt or 1
            view.runner_name = record.get("runner") or view.runner_name
            view.branch_name = record.get("branch") or view.branch_name
            view.display_base = (
                record.get("target_branch")
                or record.get("expected_publish_branch")  # compat: old records
                or view.display_base
            )
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
        elif ptype == "heartbeat":
            # Heartbeats only bump updated_at — they don't move state.
            # The render reads the current wall clock to compute elapsed,
            # so the live "running · X" line will tick on its own once
            # the gate re-renders in response to this packet.
            pass
        elif ptype == "finalizing":
            stage = record.get("stage")
            # Don't clobber a terminal explanation: once the projection
            # knows a task is failed/conflict, the failure detail is
            # more useful to the operator than "finalizing (failed)".
            if view.state == "active":
                view.detail = f"finalizing ({stage})" if stage else "finalizing"
                _open_phase(view, "finalizing", ts)
        elif ptype == "push_started":
            view.branch_name = record.get("branch") or view.branch_name
            view.detail = "pushing changes"
        elif ptype == "push_done":
            view.branch_name = record.get("branch") or view.branch_name
            commits = record.get("commits")
            view.push_commits = int(commits) if isinstance(commits, int) else view.push_commits
            view.push_ok = bool(record.get("ok", True))
            error = record.get("error")
            if isinstance(error, str) and error:
                view.push_error = error
            view_url = record.get("view_url")
            if isinstance(view_url, str) and view_url:
                view.view_url = view_url
            if view.push_ok:
                view.detail = (
                    f"pushed {commits} commit(s)" if commits else "pushed"
                )
            else:
                view.detail = "push failed"
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
            _close_open_phase(view, ts)
            # Phase entry detail is the "what went wrong" second line
            # under the terminal "failed · 4m 02s" header — the runner's
            # own error message reads better than the dev-side bit
            # roll-up that lives in view.detail for the verbose form.
            if err:
                phase_detail = str(err)
                if exit_code not in (None, "") and not timed_out:
                    phase_detail = f"{phase_detail} (exit {exit_code})"
            elif exit_code not in (None, ""):
                phase_detail = f"exit {exit_code}"
            elif timed_out:
                phase_detail = "timed out"
            else:
                phase_detail = stage or "failed"
            view.phase_history.append(PhaseEntry(
                name="failed", started_at=ts, detail=phase_detail,
            ))
        elif ptype == "conflict":
            view.branch_name = (
                record.get("publish_branch")
                or record.get("branch")
                or view.branch_name
            )
            view.state = "failed"
            view.detail = (
                f"publish conflict on {record.get('branch')}"
                if record.get("branch") else "publish conflict"
            )
            _close_open_phase(view, ts)
            view.phase_history.append(PhaseEntry(
                name="conflict", started_at=ts, detail=view.detail,
            ))
        elif ptype == "done":
            view.branch_name = (
                record.get("publish_branch")
                or view.branch_name
            )
            view.state = "succeeded"
            view.detail = view.detail or "done"
            _close_open_phase(view, ts)
            view.phase_history.append(PhaseEntry(
                name="delivered", started_at=ts,
            ))

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


@dataclass(frozen=True)
class RenderStyle:
    """Markup tokens for the strike-through phase log.

    Defaults to plain text (no markup) for CLI / log surfaces. Gates pass
    their own styles — Telegram uses HTML ``<s>…</s>`` with parse_mode
    HTML; Slack uses ``~text~`` (mrkdwn). Past phases get wrapped in
    ``done_open``/``done_close``; the current live phase is plain.
    """

    done_open: str = ""
    done_close: str = ""


PLAIN_STYLE = RenderStyle()
TELEGRAM_HTML_STYLE = RenderStyle(done_open="<s>", done_close="</s>")
SLACK_MRKDWN_STYLE = RenderStyle(done_open="~", done_close="~")
GITHUB_MARKDOWN_STYLE = RenderStyle(done_open="~~", done_close="~~")


# Lifecycle packets that warrant (re)rendering a live progress card.
# The canonical set lives here so every gate — telegram, slack, github,
# and the managed cloud gate — surfaces exactly the same moments and
# can't drift apart.
CARD_PACKETS = frozenset({
    "task_created",
    "env_prepared",
    "container_started",
    "container_preserved",
    "run_started",
    "attempt_started",
    "attempt_failed",
    "retrying",
    "artifact_created",
    "heartbeat",
    "finalizing",
    "push_started",
    "push_done",
    "done",
    "failed",
    "conflict",
})


def render_text(
    view: RunProgressView,
    *,
    compact: bool = True,
    style: RenderStyle | None = None,
    now: datetime | None = None,
) -> str:
    """Render a RunProgressView for human consumption.

    *compact* mode is the chat-surface card: a sticky header naming the
    runner, env, and branch, then a vertical strike-through phase log
    where past phases are crossed out and the live phase shows its
    rolling elapsed time. The strike-through markup comes from *style*
    so each gate can plug in its native syntax (HTML for Telegram,
    mrkdwn for Slack).

    *now* defaults to the current UTC time and drives the live elapsed
    counter — heartbeats trigger re-renders, the elapsed counter ticks,
    and the gate's duplicate-text guard suppresses any no-op edits.

    *compact=False* is the expanded diagnostic form: it adds branch,
    env, response paths, container IDs, and the artifact list.
    """
    style = style or PLAIN_STYLE
    if compact:
        return _render_compact(view, style, now)
    return _render_verbose(view)


def _render_compact(
    view: RunProgressView,
    style: RenderStyle,
    now: datetime | None,
) -> str:
    now = now or datetime.now(timezone.utc)
    lines: list[str] = []

    header = _compact_header(view)
    if header:
        lines.append(header)

    history = list(view.phase_history)
    multi_attempt = sum(1 for e in history if e.name == "running") > 1
    task_started_at = history[0].started_at if history else view.started_at

    if view.sync_summary:
        # Surfaces the daemon's pre-task fetch+ff outcome on the card so
        # operators see when the seed branch was actually advanced (or
        # why we couldn't). Quiet when sync was a no-op — daemon only
        # emits the packet on meaningful changes.
        lines.append(f"synced: {view.sync_summary}")

    if not history:
        # No state to log yet — fall back to a single status line so
        # operators see something on a freshly-arrived event.
        if header:
            lines.append("")
        lines.append(view.status_label())
        return "\n".join(lines).rstrip() + "\n"

    if header or view.sync_summary:
        lines.append("")

    last_index = len(history) - 1
    for index, entry in enumerate(history):
        is_terminal = entry.name in _TERMINAL_PHASE_NAMES
        is_active = (
            index == last_index
            and entry.ended_at is None
            and not is_terminal
        )
        label = _phase_label(entry, multi_attempt)

        if is_terminal:
            total_elapsed = _elapsed_seconds(task_started_at, entry.started_at)
            line = label
            if total_elapsed is not None:
                line += f" · {_format_duration(total_elapsed)}"
            extra = _terminal_extra(view, entry)
            if extra:
                line += f" · {extra}"
            lines.append(line)
            if entry.detail and entry.name in {"failed", "conflict"}:
                lines.append(entry.detail)
            if entry.name == "delivered" and view.view_url:
                # The forge URL goes on its own line so the terminal
                # line stays readable on narrow chat surfaces. Bare
                # URLs auto-link on every gate we render to, so no
                # markdown wrapping is needed.
                lines.append(f"view: {view.view_url}")
        elif is_active:
            elapsed = _elapsed_seconds(entry.started_at, _to_iso(now))
            if elapsed is not None and elapsed >= 1:
                lines.append(f"{label} · {_format_duration(elapsed)}")
            else:
                lines.append(label)
        else:
            duration = _elapsed_seconds(entry.started_at, entry.ended_at)
            text = label
            if duration is not None and duration >= 1:
                text += f" · {_format_duration(duration)}"
            lines.append(f"{style.done_open}{text}{style.done_close}")

    return "\n".join(lines).rstrip() + "\n"


def _render_verbose(view: RunProgressView) -> str:
    lines: list[str] = [_legacy_header(view)]
    rows: list[tuple[str, str]] = [("phase", _phase_text(view))]
    branch_text = _branch_text(view)
    if branch_text:
        rows.append(("branch", branch_text))
    if view.env:
        rows.append(("env", view.env))
    if view.runner_name:
        rows.append(("runner", view.runner_name))
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

    lines.append("")
    for key, value in rows:
        lines.append(f"{key}: {value}")

    if view.artifacts:
        lines.append("")
        lines.append(f"artifacts ({len(view.artifacts)}):")
        for artifact in view.artifacts[-5:]:
            label = artifact.get("label") or artifact.get("artifact_kind") or "artifact"
            path = artifact.get("path", "")
            lines.append(f"  - {label} -> {path}")

    return "\n".join(lines).rstrip() + "\n"


def _compact_header(view: RunProgressView) -> str:
    """Sticky one-line header: runner · env · branch ← base.

    Empty when none of the fields are populated yet (fresh event), in
    which case the body falls back to a single status line.
    """
    bits: list[str] = []
    if view.runner_name:
        bits.append(view.runner_name)
    if view.env:
        bits.append(view.env)
    if view.branch_name:
        bn = view.branch_name
        if view.display_base and view.display_base != view.branch_name:
            bn = f"{view.branch_name} ← {view.display_base}"
        bits.append(bn)
    return " · ".join(bits)


def _phase_label(entry: PhaseEntry, multi_attempt: bool) -> str:
    """Display label for a phase entry.

    Running entries get an attempt suffix only when the run actually had
    multiple attempts — single-attempt runs read as plain ``running``.
    """
    if entry.name == "running" and multi_attempt and entry.attempt:
        return f"running (attempt {entry.attempt})"
    return entry.name


def _terminal_extra(view: RunProgressView, entry: PhaseEntry) -> str:
    """Extra suffix for the terminal entry (delivered/failed/conflict)."""
    parts: list[str] = []
    if entry.name == "delivered" and view.push_commits and view.push_ok:
        plural = "" if view.push_commits == 1 else "s"
        parts.append(f"pushed {view.push_commits} commit{plural}")
    elif entry.name == "delivered" and not view.push_ok:
        parts.append("push failed")
    return " · ".join(parts)


def _legacy_header(view: RunProgressView) -> str:
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
    if view.branch_name and view.display_base:
        return f"{view.branch_name} <- {view.display_base}"
    if view.branch_name:
        return view.branch_name
    return ""


# ── Time helpers ────────────────────────────────────────────────────


_ISO_FORMATS = ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).astimezone(timezone.utc)
    except ValueError:
        for fmt in _ISO_FORMATS:
            try:
                return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def _to_iso(when: datetime) -> str:
    """Format a datetime as microsecond-precision UTC ISO 8601.

    Microsecond precision matches the resolution emitted by
    ``conversations._now_iso`` so callers that round-trip the value
    through :func:`_elapsed_seconds` get sub-second accuracy back.
    Second-precision rounding here historically lost up to ~1s on the
    chat-card running counter.
    """
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    utc = when.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond:06d}Z"


def _elapsed_seconds(start: str | None, end: str | None) -> float | None:
    a = _parse_iso(start)
    b = _parse_iso(end)
    if a is None or b is None:
        return None
    delta = (b - a).total_seconds()
    return max(0.0, delta)


def _format_duration(seconds: float) -> str:
    """Format a duration as ``Xs`` / ``Xm Yys`` / ``Xh Yym``.

    Optimised for the chat card: short for sub-minute (``42s``),
    minute+seconds for the typical agent run (``4m 02s``), hour+minutes
    when things really drag (``1h 23m``).
    """
    secs = int(round(seconds))
    if secs < 60:
        return f"{secs}s"
    minutes, remainder = divmod(secs, 60)
    if minutes < 60:
        if remainder == 0:
            return f"{minutes}m"
        return f"{minutes}m {remainder:02d}s"
    hours, remainder_minutes = divmod(minutes, 60)
    return f"{hours}h {remainder_minutes:02d}m"


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

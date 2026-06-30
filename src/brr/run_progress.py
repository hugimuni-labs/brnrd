"""Run progress — gate-agnostic projection over conversation log records.

Conversation logs (under ``.brr/conversations/<safe-key>/``, one
``<event-id>.jsonl`` per pipeline) capture durable facts the daemon
emits about an event-led run: the event arrival, the run row, non-heartbeat
lifecycle update packets, and artifact records. This module
folds those records into a compact ``RunProgressView`` that gates and
local diagnostics can render the same way.

The projection is read-only and does not mutate conversation state.
Renderers should treat it as the canonical view of a single run's
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
    "run_created": "preparing",
    "env_prepared": "preparing",
    "container_started": "running",
    "attempt_started": "running",
    "attempt_failed": "running",
    "retrying": "running",
    "run_started": "running",
    "artifact_created": "running",
    "interim_response": "running",
    "finalizing": "finalizing",
    "container_preserved": "finalizing",
    "push_started": "delivering",
    "push_done": "delivered",
    "done": "delivered",
    "failed": "failed",
    "conflict": "conflict",
    # ``card_composed`` annotates the card body but does not advance the
    # phase — it can land in any phase (preparing, running, finalizing)
    # while the resident narrates what it is doing.
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
class AttemptEntry:
    """One runner attempt and its terminal reason, for the card ledger."""

    number: int
    runner: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    status: str = "running"
    reason: str | None = None
    failure_kind: str | None = None
    fallback_runner: str | None = None
    will_retry: bool = False
    will_fallback: bool = False
    needs_relay_consent: bool = False
    relay_candidate: str | None = None


@dataclass
class RunProgressView:
    """Snapshot of a single run's execution, derived from conversation
    records.

    Renderers (gate cards, local diagnostics) consume this struct.
    Treat fields as advisory — older logs may lack newer record types.
    """

    conversation_key: str
    run_id: str | None
    phase: str = "queued"
    state: str = "active"
    branch_name: str | None = None
    display_base: str | None = None
    repo_label: str | None = None
    env: str | None = None
    runner_name: str | None = None
    attempt: int = 0
    started_at: str | None = None
    updated_at: str | None = None
    detail: str = ""
    interim_count: int = 0
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    container_ids: list[str] = field(default_factory=list)
    response_path: str | None = None
    run_state_path: str | None = None
    run_state_url: str | None = None
    error: str | None = None
    event_id: str | None = None
    phase_history: list[PhaseEntry] = field(default_factory=list)
    attempt_history: list[AttemptEntry] = field(default_factory=list)
    push_commits: int | None = None
    push_ok: bool = True
    push_error: str | None = None
    view_url: str | None = None
    sync_summary: str | None = None
    # Agent-composed card note. When set, ``render_text`` surfaces this
    # under the live phase line (or, on a terminal phase, just above the
    # terminal entry) so the resident can narrate what its progress
    # actually is — daemon owns the lifecycle scaffolding, agent owns
    # the narration. See ``kb/design-managed-delivery.md`` for the
    # relay-not-store stance the seam preserves: brnrd still only edits
    # a card it does not author or store.
    agent_card_text: str | None = None
    agent_card_updated_at: str | None = None
    # Success-signal axis (§8 re-alignment): which signal closed the run
    # — current_reply | other_reply | outbound | commit | internal —
    # and the delivery shape so the card can reflect multi-thread answers.
    success_signal: str | None = None
    replies_current: int = 0
    replies_other: int = 0
    outbound_messages: int = 0
    committed: bool = False
    # Failure-kind axis (§8 re-alignment): operational failures render
    # distinctly from a hypothetical agent partial. Values:
    # timed_out | runner_error | no_output. None until a ``failed`` packet.
    failure_kind: str | None = None

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


def project_run(
    brr_dir: Path,
    conversation_key: str,
    run_id: str,
) -> RunProgressView | None:
    """Project the latest progress for a given (conversation, run)."""
    if not conversation_key:
        return None
    records = conversations_mod.read_records(brr_dir, conversation_key)
    if not records:
        return None
    return _project(records, conversation_key=conversation_key, run_id=run_id)


def project_conversation_latest(
    brr_dir: Path,
    conversation_key: str,
) -> RunProgressView | None:
    """Project the most recent run's progress for a conversation.

    Useful when a gate wants to show "what is currently happening" in
    a thread without tracking individual run IDs.
    """
    if not conversation_key:
        return None
    records = conversations_mod.read_records(brr_dir, conversation_key)
    latest_run_id = _latest_run_id(records)
    if latest_run_id is None:
        return None
    return _project(
        records, conversation_key=conversation_key, run_id=latest_run_id,
    )


def _latest_run_id(records: list[dict[str, Any]]) -> str | None:
    for record in reversed(records):
        tid = record.get("run_id")
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


def _open_attempt(view: RunProgressView, attempt: int, ts: str | None) -> None:
    existing = _find_attempt(view, attempt)
    if existing is not None:
        existing.started_at = existing.started_at or ts
        if not existing.runner:
            existing.runner = view.runner_name
        return
    view.attempt_history.append(
        AttemptEntry(number=attempt, runner=view.runner_name, started_at=ts),
    )


def _find_attempt(view: RunProgressView, attempt: int | None) -> AttemptEntry | None:
    if not isinstance(attempt, int):
        return None
    for entry in view.attempt_history:
        if entry.number == attempt:
            return entry
    return None


def _latest_attempt(view: RunProgressView) -> AttemptEntry | None:
    return view.attempt_history[-1] if view.attempt_history else None


def _set_current_attempt_runner(view: RunProgressView, runner: object) -> None:
    if not isinstance(runner, str) or not runner.strip():
        return
    latest = _latest_attempt(view)
    if latest is not None and not latest.runner:
        latest.runner = runner


def _record_attempt_failure(
    view: RunProgressView,
    record: dict[str, Any],
    ts: str | None,
) -> None:
    attempt = record.get("attempt")
    if not isinstance(attempt, int):
        attempt = view.attempt or 1
    entry = _find_attempt(view, attempt)
    if entry is None:
        entry = AttemptEntry(number=attempt, runner=view.runner_name)
        view.attempt_history.append(entry)
    entry.ended_at = ts
    entry.status = "failed"
    reason = record.get("reason")
    if isinstance(reason, str) and reason.strip():
        entry.reason = reason.strip()
    failure_kind = record.get("failure_kind")
    if isinstance(failure_kind, str) and failure_kind.strip():
        entry.failure_kind = failure_kind.strip()
    fallback = record.get("fallback_runner")
    if isinstance(fallback, str) and fallback.strip():
        entry.fallback_runner = fallback.strip()
    entry.will_retry = bool(record.get("will_retry"))
    entry.will_fallback = bool(record.get("will_fallback"))
    entry.needs_relay_consent = bool(record.get("needs_relay_consent"))
    relay_candidate = record.get("relay_candidate")
    if isinstance(relay_candidate, str) and relay_candidate.strip():
        entry.relay_candidate = relay_candidate.strip()


def _record_terminal_attempt_failure(
    view: RunProgressView,
    record: dict[str, Any],
    ts: str | None,
) -> None:
    latest = _latest_attempt(view)
    if latest is None or latest.status == "failed":
        return
    latest.status = "failed"
    latest.ended_at = ts
    failure_kind = record.get("failure_kind")
    if isinstance(failure_kind, str) and failure_kind.strip():
        latest.failure_kind = failure_kind.strip()
    elif view.failure_kind:
        latest.failure_kind = view.failure_kind


def _mark_latest_attempt_succeeded(view: RunProgressView, ts: str | None) -> None:
    latest = _latest_attempt(view)
    if latest is None:
        return
    if latest.status == "running":
        latest.status = "succeeded"
        latest.ended_at = latest.ended_at or ts


def _project(
    records: list[dict[str, Any]],
    *,
    conversation_key: str,
    run_id: str,
) -> RunProgressView:
    view = RunProgressView(
        conversation_key=conversation_key,
        run_id=run_id,
    )

    last_ts: str | None = None
    for record in records:
        if record.get("run_id") != run_id:
            # Per-run cards must never borrow anonymous records from the
            # rest of the conversation. Older task-era logs lack run_id;
            # treating them as "maybe current" can make a new card replay
            # a whole thread's history.
            continue
        kind = record.get("kind")
        ts = record.get("ts")
        if ts and record.get("run_id") == run_id:
            view.updated_at = ts
            last_ts = ts

        if kind == "run":
            view.branch_name = record.get("branch_name") or view.branch_name
            view.display_base = (
                record.get("target_branch")
                or view.display_base
            )
            view.repo_label = record.get("repo_label") or view.repo_label
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

        if ptype == "run_created":
            view.repo_label = record.get("repo_label") or view.repo_label
            view.env = record.get("env") or view.env
            view.event_id = record.get("event_id") or view.event_id
            run_state_path = record.get("run_state_path")
            if isinstance(run_state_path, str) and run_state_path:
                view.run_state_path = run_state_path
            run_state_url = record.get("run_state_url")
            if isinstance(run_state_url, str) and run_state_url:
                view.run_state_url = run_state_url
            _open_phase(view, "preparing", ts)
        elif ptype == "env_prepared":
            view.repo_label = record.get("repo_label") or view.repo_label
            view.env = record.get("env") or view.env
            view.branch_name = record.get("branch_name") or view.branch_name
            view.display_base = (
                record.get("target_branch")
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
                _open_attempt(view, attempt, ts)
            view.started_at = view.started_at or ts
            _open_phase(view, "running", ts, attempt=attempt or view.attempt or 1)
        elif ptype == "run_started":
            view.started_at = view.started_at or ts
            view.attempt = view.attempt or 1
            view.runner_name = record.get("runner") or view.runner_name
            _set_current_attempt_runner(view, view.runner_name)
            view.branch_name = record.get("branch") or view.branch_name
            view.display_base = (
                record.get("target_branch")
                or view.display_base
            )
        elif ptype == "attempt_failed":
            reason = record.get("reason")
            relay_detail = ""
            if record.get("needs_relay_consent"):
                candidate = str(record.get("relay_candidate") or "").strip()
                relay_detail = (
                    f"; relay available: {candidate}"
                    if candidate else "; relay available"
                )
            if reason:
                view.detail = (
                    f"attempt {record.get('attempt', view.attempt)} "
                    f"failed: {reason}{relay_detail}"
                )
            elif relay_detail:
                view.detail = relay_detail.lstrip("; ")
            _record_attempt_failure(view, record, ts)
        elif ptype == "retrying":
            attempt = record.get("attempt")
            if isinstance(attempt, int):
                view.attempt = attempt
            runner_name = record.get("runner")
            if isinstance(runner_name, str) and runner_name.strip():
                view.runner_name = runner_name
                from_runner = record.get("from_runner")
                if isinstance(from_runner, str) and from_runner.strip():
                    view.detail = (
                        f"fallback {from_runner} -> {runner_name} "
                        f"(attempt {view.attempt})"
                    )
                else:
                    view.detail = f"retry on {runner_name} (attempt {view.attempt})"
            else:
                view.detail = f"retry attempt {view.attempt}"
        elif ptype == "artifact_created":
            label = record.get("label") or record.get("kind") or "artifact"
            view.detail = f"artifact: {label}"
        elif ptype == "interim_response":
            # The resident shipped a mid-flight reply (multi-response
            # protocol). The body is streamed to the user by the gate's
            # own delivery loop; here it only annotates the live card.
            view.interim_count += 1
            target = record.get("target_event")
            if target:
                view.detail = f"answered a folded-in event ({target})"
            else:
                view.detail = f"shipped interim reply (#{view.interim_count})"
        elif ptype == "card_composed":
            # The resident rewrote its ``.card`` narration. We keep the
            # latest text only (rewrites replace previous text — the
            # agent's own notion of "what's happening now"). An empty
            # body clears the note so the agent can withdraw it.
            text = record.get("text")
            if isinstance(text, str):
                stripped = text.strip()
                view.agent_card_text = stripped or None
                view.agent_card_updated_at = ts
        elif ptype == "heartbeat":
            # Compatibility for older logs that persisted heartbeat
            # records. New heartbeats are daemon-only liveness/card
            # packets and do not enter conversation memory.
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
            failure_kind = record.get("failure_kind")
            if isinstance(failure_kind, str) and failure_kind:
                view.failure_kind = failure_kind
            elif timed_out:
                view.failure_kind = "timed_out"
            elif exit_code not in (None, "", 0):
                view.failure_kind = "runner_error"
            else:
                view.failure_kind = view.failure_kind or "no_output"
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
            _record_terminal_attempt_failure(view, record, ts)
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
            signal = record.get("success_signal")
            if isinstance(signal, str) and signal:
                view.success_signal = signal
            for key in ("replies_current", "replies_other", "outbound_messages"):
                val = record.get(key)
                if isinstance(val, int):
                    setattr(view, key, val)
            if "committed" in record:
                view.committed = bool(record["committed"])
            _mark_latest_attempt_succeeded(view, ts)
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
    "run_created",
    "env_prepared",
    "container_started",
    "container_preserved",
    "run_started",
    "attempt_started",
    "attempt_failed",
    "retrying",
    "artifact_created",
    "heartbeat",
    # Agent-composed narration: when the resident updates its ``.card``
    # control file, the daemon emits this packet so the gate re-renders
    # the live card with the new note (see ``_render_compact``).
    "card_composed",
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
    run_started_at = history[0].started_at if history else view.started_at

    if view.sync_summary:
        # Surfaces the daemon's pre-run fetch+ff outcome on the card so
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
        label = _phase_label(entry, multi_attempt, view)

        if is_terminal:
            total_elapsed = _elapsed_seconds(run_started_at, entry.started_at)
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

    attempt_lines = _format_attempt_ledger(view)
    if attempt_lines:
        lines.extend(attempt_lines)

    note = _format_agent_note(view.agent_card_text)
    if note:
        lines.append(note)

    return "\n".join(lines).rstrip() + "\n"


# Soft cap on the agent's card narration. Keeps a runaway resident from
# flooding a single chat card; the gate's own overflow guard catches
# anything that still slips through. 1024 chars is enough for a paragraph
# or two — the same order of magnitude as a typical interim reply.
_AGENT_CARD_MAX_CHARS = 1024


def _format_agent_note(text: str | None) -> str:
    """Render the agent's ``.card`` narration as the live card's tail.

    Empty / whitespace-only notes return an empty string so the card
    silently drops back to the daemon-rendered phase log. The first line
    is prefixed ``note: `` so the chat reader sees who is talking;
    subsequent lines keep the agent's own line breaks.
    """
    if not text:
        return ""
    body = text.strip()
    if not body:
        return ""
    if len(body) > _AGENT_CARD_MAX_CHARS:
        body = body[:_AGENT_CARD_MAX_CHARS].rstrip() + "…"
    parts = body.splitlines()
    head, *rest = parts
    rendered = [f"note: {head}"]
    rendered.extend(rest)
    return "\n".join(rendered)


def _format_attempt_ledger(view: RunProgressView) -> list[str]:
    failed = [entry for entry in view.attempt_history if entry.reason]
    if not failed:
        return []
    lines = ["attempts:"]
    for entry in failed:
        label = f"attempt {entry.number}"
        if entry.runner:
            label += f" ({entry.runner})"
        status = _attempt_status_label(entry)
        reason = _trim_attempt_reason(entry.reason)
        line = f"- {label}: {status}"
        if reason and reason != status:
            line += f" - {reason}"
        if entry.fallback_runner:
            line += f" -> {entry.fallback_runner}"
        if entry.needs_relay_consent:
            candidate = entry.relay_candidate or "brnrd relay"
            line += f" · relay available: {candidate}"
        lines.append(line)
    return lines


def _attempt_status_label(entry: AttemptEntry) -> str:
    if entry.failure_kind:
        return {
            "timed_out": "timed out",
            "quota_exhausted": "quota exhausted",
            "auth_error": "auth failed",
            "provider_error": "provider failed",
            "runner_error": "runner failed",
            "no_output": "no reply",
        }.get(entry.failure_kind, "failed")
    return "failed"


def _trim_attempt_reason(reason: str | None, *, limit: int = 140) -> str:
    text = " ".join(str(reason or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _render_verbose(view: RunProgressView) -> str:
    lines: list[str] = [_legacy_header(view)]
    rows: list[tuple[str, str]] = [("phase", _phase_text(view))]
    if view.repo_label:
        rows.append(("repo", view.repo_label))
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
    if view.agent_card_text:
        # First line only — the verbose form is one row per key, and
        # the agent's narration is best read on the compact card. This
        # row is the dev-side breadcrumb that a narration was set.
        head = view.agent_card_text.splitlines()[0] if view.agent_card_text else ""
        if head:
            rows.append(("note", head))
    if view.error:
        rows.append(("error", view.error))
    if view.container_ids:
        rows.append(("containers", ", ".join(view.container_ids)))
    if view.response_path and view.is_terminal:
        rows.append(("response", view.response_path))
    if view.run_state_url:
        # Web-visible link to the durable run-state object — the form a remote
        # chat reader can actually open.
        rows.append(("run_state", view.run_state_url))
    elif view.run_state_path:
        # No forge projection yet (local-only dominion): show the basename, not
        # the absolute host path, which is meaningless off the daemon's machine.
        rows.append(("run_state", Path(view.run_state_path).name))

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
    if view.repo_label:
        bits.append(view.repo_label)
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


def _phase_label(entry: PhaseEntry, multi_attempt: bool,
                 view: RunProgressView | None = None) -> str:
    """Display label for a phase entry.

    Running entries get an attempt suffix only when the run actually had
    multiple attempts — single-attempt runs read as plain ``running``.
    Failed entries get an operational-failure rename when the daemon
    classified the failure: ``timed out`` (runner timeout), ``runner
    failed`` (non-zero exit), ``no reply`` (clean exit with no output of
    any kind). §8 re-alignment: distinct from a normal partial.
    """
    if entry.name == "running" and multi_attempt and entry.attempt:
        return f"running (attempt {entry.attempt})"
    if entry.name == "failed" and view is not None and view.failure_kind:
        return {
            "timed_out": "timed out",
            "quota_exhausted": "quota exhausted",
            "auth_error": "auth failed",
            "provider_error": "provider failed",
            "runner_error": "runner failed",
            "no_output": "no reply",
        }.get(view.failure_kind, entry.name)
    return entry.name


def _terminal_extra(view: RunProgressView, entry: PhaseEntry) -> str:
    """Extra suffix for the terminal entry (delivered/failed/conflict).

    Surfaces the §8 re-alignment: a single run that answered multiple
    threads or sent out-of-bound messages is reflected here, not
    collapsed to the current-thread reply. Push status remains on its
    own segment, joined with ``·``.
    """
    parts: list[str] = []
    if entry.name == "delivered":
        # Multi-thread / out-of-bound delivery count first — what the
        # user most needs to know about a co-maintainer wake.
        thread_extra = _delivery_summary(view)
        if thread_extra:
            parts.append(thread_extra)
        if view.push_commits and view.push_ok:
            plural = "" if view.push_commits == 1 else "s"
            parts.append(f"pushed {view.push_commits} commit{plural}")
        elif not view.push_ok:
            parts.append("push failed")
    return " · ".join(parts)


def _delivery_summary(view: RunProgressView) -> str:
    """Compact reflection of where this run delivered to.

    Honest about the multi-thread shape the §8 re-alignment introduces:
    a wake that answered the current thread *and* folded in another, or
    sent a `gate:` message, or committed without replying, no longer
    reads as a single "delivered" line that hides the rest.

    Returns the empty string for the common one-thread case so the
    terminal line stays uncluttered.
    """
    extra_threads = view.replies_other
    outbound = view.outbound_messages
    current = view.replies_current
    if view.success_signal == "internal":
        return ""
    if view.success_signal == "commit" and not (current or extra_threads or outbound):
        # Pure commit-success on an addressed event is rare today (we
        # still emit a synthesized terminal note for non-internal events
        # without a body), but when it does happen the card should say so.
        return "committed; no reply"
    bits: list[str] = []
    threads = (1 if current else 0) + extra_threads
    if threads > 1:
        bits.append(f"delivered to {threads} threads")
    if outbound > 0:
        plural = "" if outbound == 1 else "s"
        bits.append(f"sent {outbound} out-of-bound message{plural}")
    return " · ".join(bits)


def _legacy_header(view: RunProgressView) -> str:
    bits = ["brr"]
    if view.run_id:
        bits.append(view.run_id)
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


def run_id_from_packet(packet: Any) -> str | None:
    """Extract a run ID from an UpdatePacket (or compatible mapping)."""
    payload = getattr(packet, "payload", None)
    if payload is None and isinstance(packet, dict):
        payload = packet
    if not isinstance(payload, dict):
        return None
    tid = payload.get("run_id")
    if tid:
        return str(tid)
    return None

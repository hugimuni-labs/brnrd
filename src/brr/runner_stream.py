"""Streaming runner — brr drives claude's stream-json loop and reads it.

Tier 2 boundary injection for claude is **not** hooks: Claude Code's
settings-file lifecycle hooks never fire under headless ``claude --print``
(see ``kb/design-runner-back-channel.md``). The verified mechanism is brr
*driving* the stream itself: ``claude --print --input-format stream-json
--output-format stream-json --verbose`` is an interactive loop — brr writes
newline-delimited JSON user messages on stdin (kept open) and reads JSON
events on stdout. brr owns the message loop, so it can weave a portal delta
in as a user message at a tool boundary without any harness callback.

This module is **step 1** of ``kb/plan-streaming-runner-injection.md``: the
stream-json client — Popen wiring, the NDJSON event parser, and the
tool-boundary detector — proven against recorded event fixtures. It does the
reading and exposes the boundary seam (``on_boundary``); the *injection* and
outbox drain that ride that seam are step 2, and routing claude onto this
path (the ``stream:`` profile flag) is step 3. Until then the blocking
``runner.invoke_runner`` path stays the default for every run, untouched.

The event schema is a Claude Code surface that can shift across versions, so
the parser degrades safely: an unparseable or unknown line is ignored, never
fatal, and result capture never depends on an optional field.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

# stream-json flags brr adds to a profile's base command to drive the loop,
# each as the argv tokens to append when the flag is absent. ``--print`` is
# already in the claude profile; these turn its single-shot print into a
# bidirectional NDJSON stream. ``--verbose`` is required by the CLI whenever
# ``--output-format stream-json`` is used with ``--print``.
_STREAM_FLAGS: tuple[tuple[str, ...], ...] = (
    ("--input-format", "stream-json"),
    ("--output-format", "stream-json"),
    ("--verbose",),
)


# ── Profile opt-in ───────────────────────────────────────────────────────


def stream_flavour(name: str, repo_root: Path | None = None) -> str | None:
    """Return the runner's declared streaming *dialect*, or None.

    A profile opts into the streaming-driven path with a ``stream: <flavour>``
    field (today only ``claude``). Absent → the run takes the blocking
    ``--print`` path unchanged. This reads declared intent from the profile;
    it is the switch that keeps the most load-bearing surface (every run)
    safe while the new path proves itself.
    """
    from . import runner

    profile = runner._load_profiles(repo_root).get(name) or {}
    flavour = profile.get("stream")
    if not flavour:
        return None
    flavour = str(flavour).strip().lower()
    return flavour or None


def build_stream_cmd(
    runner_name: str, cfg: dict[str, Any], repo_root: Path | None = None
) -> list[str]:
    """Build the streaming argv for *runner_name*.

    Starts from the profile's (or ``runner_cmd``'s) base command — the same
    source ``runner._build_cmd`` uses — but does **not** append the prompt as
    a final argument: in stream-json input mode the prompt is sent as the
    first user message on stdin, not on argv. The stream-json flags are added
    only when absent, so a custom command that already declares them is left
    as the user wrote it.
    """
    from . import runner

    custom = cfg.get("runner_cmd")
    if custom:
        base = list(custom) if isinstance(custom, list) else shlex.split(str(custom))
        # A custom command may carry a ``{prompt}`` placeholder; in streaming
        # mode there is no argv prompt, so drop a lone placeholder token.
        base = [part for part in base if part != "{prompt}"]
    else:
        profile = runner._load_profiles(repo_root).get(runner_name)
        if profile:
            base = shlex.split(str(profile.get("cmd", runner_name)))
        else:
            base = [runner_name]

    for flag_tokens in _STREAM_FLAGS:
        if flag_tokens[0] not in base:
            base.extend(flag_tokens)
    return base


# ── Event model ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StreamEvent:
    """One parsed NDJSON event off the runner's stdout stream."""

    type: str
    data: dict[str, Any]

    def _content_blocks(self) -> list[dict[str, Any]]:
        message = self.data.get("message")
        if not isinstance(message, dict):
            return []
        content = message.get("content")
        if not isinstance(content, list):
            return []
        return [block for block in content if isinstance(block, dict)]

    @property
    def tool_uses(self) -> list[dict[str, Any]]:
        """``tool_use`` blocks from an assistant message (empty otherwise)."""
        if self.type != "assistant":
            return []
        return [b for b in self._content_blocks() if b.get("type") == "tool_use"]

    @property
    def tool_results(self) -> list[dict[str, Any]]:
        """``tool_result`` blocks from a user message (empty otherwise)."""
        if self.type != "user":
            return []
        return [b for b in self._content_blocks() if b.get("type") == "tool_result"]

    @property
    def is_result(self) -> bool:
        return self.type == "result"

    @property
    def result_text(self) -> str | None:
        if self.type != "result":
            return None
        text = self.data.get("result")
        return text if isinstance(text, str) else None


def parse_event(line: str) -> StreamEvent | None:
    """Parse one NDJSON line into a :class:`StreamEvent`, or None.

    Returns None for blank lines, malformed JSON, non-objects, or objects
    without a string ``type`` — the stream is a versioned external surface,
    so anything unexpected is skipped rather than allowed to crash the run.
    """
    line = line.strip()
    if not line:
        return None
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    event_type = data.get("type")
    if not isinstance(event_type, str):
        return None
    return StreamEvent(type=event_type, data=data)


def iter_events(lines: Iterable[str]) -> Iterator[StreamEvent]:
    """Yield parsed events from an iterable of NDJSON lines, skipping noise."""
    for line in lines:
        event = parse_event(line)
        if event is not None:
            yield event


# ── Boundary detection ───────────────────────────────────────────────────


@dataclass(frozen=True)
class StreamBoundary:
    """A completed tool boundary — the seam where injection rides (step 2).

    Fired once per user event that completes at least one prior tool call.
    ``index`` is the 1-based boundary count within the run.
    """

    index: int
    tool_names: list[str]
    tool_use_ids: list[str]


@dataclass
class StreamOutcome:
    """The result of consuming a stream to its terminal ``result`` event."""

    result_text: str | None = None
    is_error: bool = False
    boundary_count: int = 0
    tool_use_count: int = 0
    result_event: dict[str, Any] | None = None
    saw_result: bool = False


def consume_stream(
    lines: Iterable[str],
    *,
    on_boundary: Callable[[StreamBoundary], None] | None = None,
) -> StreamOutcome:
    """Drive over a runner stream, detecting tool boundaries and the result.

    A **tool boundary** is an assistant ``tool_use`` followed by its matching
    ``tool_result`` (carried by a later ``user`` event). Each user event that
    completes one or more pending tool calls fires ``on_boundary`` once — the
    natural post-tool seam where step 2 drains the outbox and injects a
    portal delta. The terminal ``result`` event's text is captured as the
    run's reply (the same contract as Tier 1 stdout capture).

    Pure and side-effect-free apart from the ``on_boundary`` callback, so it
    is exercised directly over recorded fixtures in tests and over a live
    ``proc.stdout`` in :func:`run_stream`.
    """
    pending: dict[str, str] = {}
    outcome = StreamOutcome()
    for event in iter_events(lines):
        if event.type == "assistant":
            for block in event.tool_uses:
                tool_id = block.get("id")
                if isinstance(tool_id, str):
                    pending[tool_id] = str(block.get("name") or "")
                    outcome.tool_use_count += 1
        elif event.type == "user":
            completed_ids: list[str] = []
            completed_names: list[str] = []
            for block in event.tool_results:
                tool_id = block.get("tool_use_id")
                if not isinstance(tool_id, str):
                    continue
                completed_ids.append(tool_id)
                # A replayed stream may carry a tool_result whose tool_use we
                # never saw; still a real boundary, just with an unknown name.
                completed_names.append(pending.pop(tool_id, ""))
            if completed_ids:
                outcome.boundary_count += 1
                if on_boundary is not None:
                    on_boundary(
                        StreamBoundary(
                            index=outcome.boundary_count,
                            tool_names=completed_names,
                            tool_use_ids=completed_ids,
                        )
                    )
        elif event.type == "result":
            outcome.saw_result = True
            outcome.result_event = event.data
            outcome.is_error = bool(event.data.get("is_error"))
            if event.result_text is not None:
                outcome.result_text = event.result_text
    return outcome


# ── stdin user-message framing ───────────────────────────────────────────


def user_message_json(text: str) -> str:
    """A stream-json user message line (newline-terminated).

    This is how the prompt is delivered in stream-json input mode and, in
    step 2, how a portal delta or a relayed user follow-up is injected
    mid-loop. The framing matches the verified spike (2026-06-26).
    """
    payload = {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }
    return json.dumps(payload) + "\n"


# ── Live driver ──────────────────────────────────────────────────────────


def run_stream(
    runner_name: str,
    invocation: "Any",
    cfg: dict[str, Any] | None = None,
    *,
    on_boundary: Callable[[StreamBoundary], None] | None = None,
):
    """Drive a streaming runner subprocess and capture its final reply.

    Mirrors :func:`runner.invoke_runner`'s contract (returns a
    :class:`runner.RunnerResult`) but over the stream-json loop: it writes
    the prompt as the first stdin user message, keeps stdin open, reads
    events to the terminal ``result``, and captures that as the response.

    The active subprocess is registered in ``runner._active_proc`` under
    ``runner._proc_lock`` — the *same* handle ``runner.kill_active`` reads —
    so the daemon's budget/shutdown kill still works unchanged. stderr is
    drained on a side thread so a chatty runner can't fill the pipe and
    deadlock the stdout read loop.
    """
    from . import runner

    cfg = cfg or {}
    cmd = build_stream_cmd(runner_name, cfg, invocation.repo_root)

    proc_env: dict[str, str] | None = None
    if invocation.env:
        proc_env = os.environ.copy()
        proc_env.update({str(k): str(v) for k, v in invocation.env.items()})

    stderr_chunks: list[str] = []
    returncode = 0
    outcome = StreamOutcome()

    def _drain_stderr(stream: Any) -> None:
        try:
            for line in stream:
                stderr_chunks.append(line)
        except (OSError, ValueError):
            pass

    try:
        with runner._proc_lock:
            runner._active_proc = subprocess.Popen(
                cmd,
                cwd=invocation.cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=proc_env,
            )
        proc = runner._active_proc
        stderr_thread = threading.Thread(
            target=_drain_stderr, args=(proc.stderr,), daemon=True
        )
        stderr_thread.start()

        # Send the prompt as the first user message; keep stdin open so a
        # boundary callback (step 2) can inject further messages mid-loop.
        proc.stdin.write(user_message_json(invocation.prompt))
        proc.stdin.flush()

        outcome = consume_stream(proc.stdout, on_boundary=on_boundary)

        try:
            proc.stdin.close()
        except OSError:
            pass
        returncode = proc.wait()
        stderr_thread.join(timeout=5)
    except FileNotFoundError:
        stderr_chunks.append(f"executable '{cmd[0]}' not found on PATH")
        returncode = 127
    finally:
        with runner._proc_lock:
            runner._active_proc = None

    stdout = outcome.result_text or ""
    if outcome.is_error and returncode == 0:
        # A terminal result flagged is_error is a runner-level failure even
        # when the process exits 0; surface it as a non-zero result.
        returncode = 1
    stderr = "".join(stderr_chunks)

    if invocation.response_path and returncode == 0 and stdout.strip():
        runner._write_response_file(invocation.response_path, stdout)

    return runner.RunnerResult(
        invocation=invocation,
        runner_name=runner_name,
        command=cmd,
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
        trace_dir=None,
        artifacts=[
            runner.RunnerArtifactRecord(
                path=spec.path,
                label=spec.label or str(spec.path),
                exists=spec.path.exists(),
            )
            for spec in invocation.required_artifacts
        ],
    )

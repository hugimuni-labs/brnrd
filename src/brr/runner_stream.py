"""Streaming runner — brr drives runner event streams and reads them.

Tier 2 boundary injection is runner-specific. Claude Code's settings-file
lifecycle hooks never fire under headless ``claude --print`` (see
``kb/design-runner-back-channel.md``), so brr *drives* Claude's stream-json
loop itself: stdin stays open, stdout emits JSON events, and brr can weave
portal deltas in as user messages at tool boundaries.

Codex exposes a different surface: ``codex exec --json`` emits JSONL events
for a single turn and records a ``thread_id`` that can be resumed. brr streams
that event feed to capture command boundaries and final text; when a pending
user follow-up is still live at the terminal turn, brr launches a
``codex exec resume --json <thread_id> ...`` follow-up with the folded-in body.
That preserves prompt responsiveness without pretending Codex has Claude's
persistent stdin loop.

This module is **steps 1–2** of ``kb/plan-streaming-runner-injection.md``:

- *Step 1* (the stream-json client): Popen wiring, the NDJSON event parser,
  and the tool-boundary detector, proven against recorded event fixtures.
- *Step 2* (persistent session + boundary injection): the driver runs a
  **multi-turn** session — ``--print`` is **stripped** (see
  :func:`build_stream_cmd`), because ``claude --print`` is single-turn and
  exits on the first ``result`` with no stop-control. With ``--print`` gone
  the process stays alive after a ``result`` waiting on stdin, so the driver
  owns the close: at each tool boundary it weaves the daemon's portal delta
  in as a user message (the inbound-delivery channel — pending events reach
  the resident without it polling ``inbox.json``), and at each terminal
  ``result`` it either folds a still-pending event into a fresh turn
  (stop-control) or closes stdin to end the run. The injection policy is
  :class:`StreamInjectionPolicy`, reusing :func:`hooks.format_delta` so the
  streaming path and the hook path render the same capsule.

Routing a runner onto this path is via the ``stream:`` profile flag; until a
profile opts in, the blocking ``runner.invoke_runner`` path stays the default
for every run, untouched.

The event schemas are external CLI surfaces that can shift across versions, so
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

# Stream flags brr adds to a profile's base command, each as the argv tokens to
# append when the flag is absent. Claude's flags turn the CLI into a
# bidirectional NDJSON loop; Codex's ``--json`` emits JSONL for the exec turn.
# ``--verbose`` is required by Claude Code to emit ``--output-format
# stream-json`` events headlessly.
_CLAUDE_STREAM_FLAGS: tuple[tuple[str, ...], ...] = (
    ("--input-format", "stream-json"),
    ("--output-format", "stream-json"),
    ("--verbose",),
)
_CODEX_STREAM_FLAGS: tuple[tuple[str, ...], ...] = (("--json",),)
_STREAM_FLAGS_BY_FLAVOUR: dict[str, tuple[tuple[str, ...], ...]] = {
    "claude": _CLAUDE_STREAM_FLAGS,
    "codex": _CODEX_STREAM_FLAGS,
}

# Flags stripped from the base command when streaming. ``--print`` / ``-p``
# makes Claude *single-turn*: it exits on the first ``result`` regardless of
# stdin, so there is no stop-control and no way to fold a late event in. The
# Claude streaming driver runs a persistent multi-turn session instead and
# owns the close itself (see the module docstring and :func:`run_stream`).
_DROP_FLAGS_BY_FLAVOUR: dict[str, frozenset[str]] = {
    "claude": frozenset({"--print", "-p"}),
    "codex": frozenset(),
}


# ── Profile opt-in ───────────────────────────────────────────────────────


def stream_flavour(name: str, repo_root: Path | None = None) -> str | None:
    """Return the runner's declared streaming *dialect*, or None.

    A profile opts into the streaming-driven path with a ``stream: <flavour>``
    field (currently ``claude`` or ``codex``). Absent → the run takes the blocking
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


def _effective_stream_flavour(runner_name: str, repo_root: Path | None = None) -> str:
    """Return the stream dialect to use for a direct streaming invocation."""
    flavour = stream_flavour(runner_name, repo_root)
    if flavour is not None:
        return flavour
    return runner_name if runner_name in _STREAM_FLAGS_BY_FLAVOUR else "claude"


def build_stream_cmd(
    runner_name: str, cfg: dict[str, Any], repo_root: Path | None = None
) -> list[str]:
    """Build the streaming argv for *runner_name*.

    Starts from the profile's (or ``runner_cmd``'s) base command — the same
    source ``runner._build_cmd`` uses — but does **not** append the prompt as
    a final argument. Claude sends the prompt as the first user message on
    stdin; Codex appends it later in the driver because the same base argv is
    reused for ``exec resume``. Flavour-specific single-shot flags (for
    Claude, ``--print`` / ``-p``) are stripped, and stream flags are added only
    when absent, so a custom command that already declares them is left as the
    user wrote it.
    """
    from . import runner

    flavour = _effective_stream_flavour(runner_name, repo_root)
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

    drop_flags = _DROP_FLAGS_BY_FLAVOUR.get(flavour, frozenset())
    base = [tok for tok in base if tok not in drop_flags]
    for flag_tokens in _STREAM_FLAGS_BY_FLAVOUR.get(flavour, ()):
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
        return self.type in {"result", "turn.completed", "turn.failed"}

    @property
    def result_text(self) -> str | None:
        if self.type != "result":
            return None
        text = self.data.get("result")
        return text if isinstance(text, str) else None

    @property
    def thread_id(self) -> str | None:
        if self.type != "thread.started":
            return None
        thread_id = self.data.get("thread_id")
        return thread_id if isinstance(thread_id, str) else None

    @property
    def item(self) -> dict[str, Any]:
        item = self.data.get("item")
        return item if isinstance(item, dict) else {}

    @property
    def item_id(self) -> str | None:
        item_id = self.item.get("id")
        return item_id if isinstance(item_id, str) else None

    @property
    def item_type(self) -> str | None:
        item_type = self.item.get("type")
        return item_type if isinstance(item_type, str) else None

    @property
    def command_text(self) -> str:
        command = self.item.get("command")
        return command if isinstance(command, str) else ""

    @property
    def agent_message_text(self) -> str | None:
        if self.type != "item.completed" or self.item_type != "agent_message":
            return None
        text = self.item.get("text")
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
    result_count: int = 0
    thread_id: str | None = None
    error_text: str | None = None


def consume_stream(
    lines: Iterable[str],
    *,
    on_boundary: Callable[[StreamBoundary], None] | None = None,
    on_result: Callable[[StreamOutcome], bool] | None = None,
) -> StreamOutcome:
    """Drive over a runner stream, detecting tool boundaries and results.

    A **tool boundary** is an assistant ``tool_use`` followed by its matching
    ``tool_result`` (carried by a later ``user`` event). Each user event that
    completes one or more pending tool calls fires ``on_boundary`` once — the
    natural post-tool seam where the driver injects a portal delta.

    Each terminal ``result`` event updates the captured reply and fires
    ``on_result(outcome)``. In a persistent (no-``--print``) session the
    process does **not** exit at a ``result`` — it waits on stdin — so the
    callback is the stop-control seam: returning ``False`` stops consuming
    (the driver then closes stdin to end the run), while returning anything
    else keeps the loop reading the turn a folded-in injection produced.
    Without a callback the loop runs to stream end, single-turn-style.

    Pure and side-effect-free apart from the callbacks, so it is exercised
    directly over recorded fixtures in tests and over a live ``proc.stdout``
    in :func:`run_stream`.
    """
    pending: dict[str, str] = {}
    outcome = StreamOutcome()
    for event in iter_events(lines):
        if event.type == "thread.started":
            outcome.thread_id = event.thread_id or outcome.thread_id
        elif event.type == "error":
            message = event.data.get("message")
            if isinstance(message, str):
                outcome.error_text = message
            outcome.is_error = True
        elif event.type == "assistant":
            for block in event.tool_uses:
                tool_id = block.get("id")
                if isinstance(tool_id, str):
                    pending[tool_id] = str(block.get("name") or "")
                    outcome.tool_use_count += 1
        elif event.type == "item.started" and event.item_type == "command_execution":
            item_id = event.item_id
            if item_id:
                pending[item_id] = event.command_text
                outcome.tool_use_count += 1
        elif event.type == "item.completed" and event.item_type == "agent_message":
            if event.agent_message_text is not None:
                outcome.result_text = event.agent_message_text
        elif event.type == "item.completed" and event.item_type == "command_execution":
            item_id = event.item_id
            if item_id:
                outcome.boundary_count += 1
                if on_boundary is not None:
                    on_boundary(
                        StreamBoundary(
                            index=outcome.boundary_count,
                            tool_names=[pending.pop(item_id, event.command_text)],
                            tool_use_ids=[item_id],
                        )
                    )
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
        elif event.is_result:
            outcome.saw_result = True
            outcome.result_event = event.data
            outcome.is_error = event.type == "turn.failed" or bool(
                event.data.get("is_error")
            )
            if event.type == "turn.failed":
                error = event.data.get("error")
                if isinstance(error, dict) and isinstance(error.get("message"), str):
                    outcome.error_text = error["message"]
                elif isinstance(error, str):
                    outcome.error_text = error
            outcome.result_count += 1
            if event.result_text is not None:
                outcome.result_text = event.result_text
            if on_result is not None and on_result(outcome) is False:
                break
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


# ── Inbound-delivery policy ──────────────────────────────────────────────

# What a boundary/result hook calls to weave a message into the live stream.
Injector = Callable[[str], None]


def _read_portal(path: Path | None) -> dict[str, Any]:
    """Read the daemon-written portal-state JSON, or ``{}`` (best-effort)."""
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _first_pending_event(payload: dict[str, Any]) -> dict[str, Any] | None:
    """The first foldable pending event from a portal-state payload, or None."""
    inbound = payload.get("inbound") if isinstance(payload.get("inbound"), dict) else {}
    events = inbound.get("events") if isinstance(inbound.get("events"), list) else []
    for ev in events:
        if isinstance(ev, dict) and str(ev.get("body") or "").strip():
            return ev
    return None


def _fold_in_message(event: dict[str, Any]) -> str:
    """Frame a newly-arrived event as the user's own relayed words.

    The 2026-06-26 spike found framing is load-bearing: a delta framed as a
    coercive daemon interrupt is perceived but *refused* (correct injection
    defense), while the same content relayed as the user's genuine words is
    acted on. So the fold-in carries the event **body verbatim** under a
    neutral, non-imperative relay header — not the operational summary.
    """
    source = str(event.get("source") or "user").strip() or "user"
    body = str(event.get("body") or "").strip()
    return f"(folded-in follow-up from the user via {source}:)\n\n{body}"


@dataclass
class StreamInjectionPolicy:
    """The inbound-delivery policy that rides the stream's seams.

    This is what closes the gap the maintainer named: pending events reach
    the resident *while it runs*, without it remembering to poll
    ``inbox.json``. It reads the same daemon-written ``portal-state.json``
    the hooks path reads and renders the same capsule via
    :func:`hooks.format_delta`:

    - **At each tool boundary** — touch the daemon's ``.flush`` signal so the
      heartbeat drains the outbox promptly (outbound replies reach the user at
      the boundary, not at the next timer tick), then inject a
      ``change_token``-gated operational delta — a shift in pending events /
      budget / delivery is woven in as an informational user message, and an
      unchanged state injects nothing.
    - **At the terminal result** — touch ``.flush`` again, then, if a foldable
      event is still pending, fold its **body verbatim** into a fresh turn once
      (stop-control, mirroring the hook ``Stop`` block) so the resident
      addresses it in the same thought; otherwise signal the driver to close.

    The ``.flush`` touch reuses the existing daemon flush mechanism (the hooks
    post-tool path touches the same file): the daemon stays the sole outbox
    drainer, so the streaming driver never couples to daemon internals.

    Priming (:meth:`prime_from_portal`) seeds ``last_token`` from the portal
    as it stood at run start, because the prompt/bundle already carried that
    snapshot — only *later* changes should be injected mid-run.
    """

    portal_state_path: Path | None = None
    flush_signal_path: Path | None = None
    last_token: Any = None
    folded_once: bool = False

    def prime_from_portal(self) -> None:
        self.last_token = _read_portal(self.portal_state_path).get("change_token")

    def _touch_flush(self) -> None:
        """Ask the daemon to drain the outbox now (best-effort)."""
        if self.flush_signal_path is None:
            return
        try:
            self.flush_signal_path.touch()
        except OSError:
            pass

    def on_boundary(self, boundary: StreamBoundary, inject: Injector) -> None:
        from . import hooks

        self._touch_flush()
        payload = _read_portal(self.portal_state_path)
        token = payload.get("change_token")
        if token is None or token == self.last_token:
            return
        delta = hooks.format_delta(payload)
        if delta:
            inject(delta)
        self.last_token = token

    def on_result(self, outcome: StreamOutcome, inject: Injector) -> bool:
        self._touch_flush()
        payload = _read_portal(self.portal_state_path)
        event = _first_pending_event(payload)
        if event is not None and not self.folded_once:
            inject(_fold_in_message(event))
            self.folded_once = True
            return True  # keep the session alive for the folded-in turn
        return False  # nothing foldable — driver closes stdin, run ends


# ── Live driver ──────────────────────────────────────────────────────────


def _proc_env(invocation: "Any") -> dict[str, str] | None:
    if not invocation.env:
        return None
    proc_env = os.environ.copy()
    proc_env.update({str(k): str(v) for k, v in invocation.env.items()})
    return proc_env


def _artifact_records(invocation: "Any") -> list["Any"]:
    from . import runner

    return [
        runner.RunnerArtifactRecord(
            path=spec.path,
            label=spec.label or str(spec.path),
            exists=spec.path.exists(),
        )
        for spec in invocation.required_artifacts
    ]


def _merge_outcome(target: StreamOutcome, source: StreamOutcome) -> None:
    if source.result_text is not None:
        target.result_text = source.result_text
    target.is_error = source.is_error
    target.boundary_count += source.boundary_count
    target.tool_use_count += source.tool_use_count
    target.result_event = source.result_event or target.result_event
    target.saw_result = target.saw_result or source.saw_result
    target.result_count += source.result_count
    target.thread_id = source.thread_id or target.thread_id
    target.error_text = source.error_text or target.error_text


def _drain_stderr(stream: Any, chunks: list[str]) -> None:
    try:
        for line in stream:
            chunks.append(line)
    except (OSError, ValueError):
        pass


def _make_default_policy(invocation: "Any") -> StreamInjectionPolicy:
    from . import hooks

    env = invocation.env or {}
    portal_env = env.get("BRR_PORTAL_STATE")
    outbox_env = env.get("BRR_OUTBOX_DIR")
    flush_path = Path(outbox_env) / hooks.FLUSH_SIGNAL_NAME if outbox_env else None
    return StreamInjectionPolicy(
        Path(portal_env) if portal_env else None,
        flush_signal_path=flush_path,
    )


def _result_from_outcome(
    runner_name: str,
    invocation: "Any",
    cmd: list[str],
    outcome: StreamOutcome,
    stderr_chunks: list[str],
    returncode: int,
):
    from . import runner

    stdout = outcome.result_text or ""
    if outcome.is_error and returncode == 0:
        # A terminal result flagged as error is a runner-level failure even
        # when the process exits 0; surface it as a non-zero result.
        returncode = 1
    stderr = "".join(stderr_chunks)
    if outcome.error_text:
        if stderr and not stderr.endswith("\n"):
            stderr += "\n"
        stderr += outcome.error_text

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
        artifacts=_artifact_records(invocation),
    )


def _codex_resume_cmd(base_cmd: list[str], thread_id: str, prompt: str) -> list[str]:
    """Build ``codex exec resume`` from a ``codex exec --json`` base argv."""
    if len(base_cmd) >= 2 and base_cmd[0] == "codex" and base_cmd[1] == "exec":
        return [base_cmd[0], base_cmd[1], "resume", *base_cmd[2:], thread_id, prompt]
    return [*base_cmd, prompt]


def _run_codex_stream(
    runner_name: str,
    invocation: "Any",
    cfg: dict[str, Any],
    *,
    on_boundary: Callable[[StreamBoundary, Injector], None] | None = None,
    on_result: Callable[[StreamOutcome, Injector], bool] | None = None,
):
    """Drive Codex's one-turn JSONL stream, resuming once for fold-in."""
    from . import runner

    base_cmd = build_stream_cmd(runner_name, cfg, invocation.repo_root)
    proc_env = _proc_env(invocation)
    policy: StreamInjectionPolicy | None = None
    if on_boundary is None and on_result is None:
        policy = _make_default_policy(invocation)
        policy.prime_from_portal()

        def boundary_cb(boundary: StreamBoundary, _inject: Injector) -> None:
            # Codex exec is a single-turn stream: there is no live stdin channel
            # to weave an operational delta into. The boundary still matters for
            # outbound responsiveness, so reuse the same flush signal.
            policy._touch_flush()

        on_boundary = boundary_cb
        on_result = policy.on_result

    stderr_chunks: list[str] = []
    returncode = 0
    outcome = StreamOutcome()
    cmd = [*base_cmd, invocation.prompt]
    reported_cmd = cmd
    turns = 0

    while True:
        turns += 1
        resume_prompts: list[str] = []

        def inject_for_resume(text: str) -> None:
            resume_prompts.append(text)

        try:
            with runner._proc_lock:
                runner._active_proc = subprocess.Popen(
                    cmd,
                    cwd=invocation.cwd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    env=proc_env,
                )
            proc = runner._active_proc
            stderr_thread = threading.Thread(
                target=_drain_stderr, args=(proc.stderr, stderr_chunks), daemon=True
            )
            stderr_thread.start()

            boundary_cb = (
                (lambda b: on_boundary(b, inject_for_resume))
                if on_boundary is not None else None
            )
            result_cb = (
                (lambda o: on_result(o, inject_for_resume))
                if on_result is not None else None
            )
            turn_outcome = consume_stream(
                proc.stdout, on_boundary=boundary_cb, on_result=result_cb
            )
            try:
                for _ in proc.stdout:
                    pass
            except (OSError, ValueError):
                pass
            turn_returncode = proc.wait()
            stderr_thread.join(timeout=5)
        except FileNotFoundError:
            stderr_chunks.append(f"executable '{cmd[0]}' not found on PATH")
            returncode = 127
            break
        finally:
            with runner._proc_lock:
                runner._active_proc = None

        _merge_outcome(outcome, turn_outcome)
        returncode = turn_returncode
        if returncode != 0 or turn_outcome.is_error:
            break

        resume_prompt = resume_prompts[-1] if resume_prompts else None
        thread_id = turn_outcome.thread_id or outcome.thread_id
        if not resume_prompt or not thread_id or turns >= 2:
            break
        cmd = _codex_resume_cmd(base_cmd, thread_id, resume_prompt)

    return _result_from_outcome(
        runner_name, invocation, reported_cmd, outcome, stderr_chunks, returncode
    )


def run_stream(
    runner_name: str,
    invocation: "Any",
    cfg: dict[str, Any] | None = None,
    *,
    on_boundary: Callable[[StreamBoundary, Injector], None] | None = None,
    on_result: Callable[[StreamOutcome, Injector], bool] | None = None,
):
    """Drive a persistent streaming runner subprocess and capture its reply.

    Mirrors :func:`runner.invoke_runner`'s contract (returns a
    :class:`runner.RunnerResult`) but over the stream-json loop: it writes
    the prompt as the first stdin user message, keeps stdin open, and reads
    events while weaving portal deltas back in at each tool boundary (the
    inbound-delivery channel). The session is **persistent** — ``--print``
    is stripped, so the process does not exit at a ``result``; the driver
    closes stdin to end the run once nothing more is pending.

    ``on_boundary`` / ``on_result`` receive an :data:`Injector` bound to the
    live ``proc.stdin``. When neither is given, the default
    :class:`StreamInjectionPolicy` is built from the run env's
    ``BRR_PORTAL_STATE`` and used for both seams. Tests pass explicit
    callbacks to drive the seams deterministically.

    The active subprocess is registered in ``runner._active_proc`` under
    ``runner._proc_lock`` — the *same* handle ``runner.kill_active`` reads —
    so the daemon's budget/shutdown kill still works unchanged. stderr is
    drained on a side thread so a chatty runner can't fill the pipe and
    deadlock the stdout read loop.
    """
    from . import runner

    cfg = cfg or {}
    flavour = _effective_stream_flavour(runner_name, invocation.repo_root)
    if flavour == "codex":
        return _run_codex_stream(
            runner_name,
            invocation,
            cfg,
            on_boundary=on_boundary,
            on_result=on_result,
        )

    cmd = build_stream_cmd(runner_name, cfg, invocation.repo_root)
    proc_env = _proc_env(invocation)

    # Default inbound-delivery policy when the caller wires no explicit seams:
    # weave the daemon's portal delta in at each boundary and fold a pending
    # event in at the terminal result. The portal path rides the run env (the
    # same handle the hooks path reads).
    policy: StreamInjectionPolicy | None = None
    if on_boundary is None and on_result is None:
        policy = _make_default_policy(invocation)
        on_boundary = policy.on_boundary
        on_result = policy.on_result

    stderr_chunks: list[str] = []
    returncode = 0
    outcome = StreamOutcome()

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
            target=_drain_stderr, args=(proc.stderr, stderr_chunks), daemon=True
        )
        stderr_thread.start()

        def inject(text: str) -> None:
            try:
                proc.stdin.write(user_message_json(text))
                proc.stdin.flush()
            except (OSError, ValueError):
                pass

        # Seed the delta gate from the portal as it stood at run start; the
        # prompt already carried that snapshot, so only later changes inject.
        if policy is not None:
            policy.prime_from_portal()

        # Send the prompt as the first user message; keep stdin open so the
        # boundary/result seams can inject further messages mid-loop.
        proc.stdin.write(user_message_json(invocation.prompt))
        proc.stdin.flush()

        boundary_cb = (
            (lambda b: on_boundary(b, inject)) if on_boundary is not None else None
        )
        result_cb = (
            (lambda o: on_result(o, inject)) if on_result is not None else None
        )
        outcome = consume_stream(
            proc.stdout, on_boundary=boundary_cb, on_result=result_cb
        )

        # End the persistent session: close stdin (claude exits on EOF), then
        # drain any trailing stdout so a full pipe can't block ``wait``.
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            for _ in proc.stdout:
                pass
        except (OSError, ValueError):
            pass
        returncode = proc.wait()
        stderr_thread.join(timeout=5)
    except FileNotFoundError:
        stderr_chunks.append(f"executable '{cmd[0]}' not found on PATH")
        returncode = 127
    finally:
        with runner._proc_lock:
            runner._active_proc = None

    return _result_from_outcome(
        runner_name, invocation, cmd, outcome, stderr_chunks, returncode
    )

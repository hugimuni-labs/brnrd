"""Streaming runner — brr drives claude's stream-json loop and reads it.

Tier 2 boundary injection for claude is **not** hooks: Claude Code's
settings-file lifecycle hooks never fire under headless ``claude --print``
(see ``kb/design-runner-back-channel.md``). The verified mechanism is brr
*driving* the stream itself: ``claude --print --input-format stream-json
--output-format stream-json --verbose`` is an interactive loop — brr writes
newline-delimited JSON user messages on stdin (kept open) and reads JSON
events on stdout. brr owns the message loop, so it can weave a portal delta
in as a user message at a tool boundary without any harness callback.

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

Routing claude onto this path (the ``stream:`` profile flag, the daemon
heartbeat/budget wiring) is step 3; until a profile opts in, the blocking
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
# each as the argv tokens to append when the flag is absent. These turn
# claude's single-shot invocation into a bidirectional NDJSON stream.
# ``--verbose`` is required by the CLI to emit ``--output-format stream-json``
# events headlessly.
_STREAM_FLAGS: tuple[tuple[str, ...], ...] = (
    ("--input-format", "stream-json"),
    ("--output-format", "stream-json"),
    ("--verbose",),
)

# Flags stripped from the base command when streaming. ``--print`` / ``-p``
# makes claude *single-turn*: it exits on the first ``result`` regardless of
# stdin, so there is no stop-control and no way to fold a late event in. The
# streaming driver runs a persistent multi-turn session instead and owns the
# close itself (see the module docstring and :func:`run_stream`).
_DROP_FLAGS: frozenset[str] = frozenset({"--print", "-p"})


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
    first user message on stdin, not on argv. ``--print`` / ``-p`` is
    **stripped** (it forces a single-turn session, see :data:`_DROP_FLAGS`),
    and the stream-json flags are added only when absent, so a custom command
    that already declares them is left as the user wrote it.
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

    base = [tok for tok in base if tok not in _DROP_FLAGS]
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
    result_count: int = 0


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
    cmd = build_stream_cmd(runner_name, cfg, invocation.repo_root)

    proc_env: dict[str, str] | None = None
    if invocation.env:
        proc_env = os.environ.copy()
        proc_env.update({str(k): str(v) for k, v in invocation.env.items()})

    # Default inbound-delivery policy when the caller wires no explicit seams:
    # weave the daemon's portal delta in at each boundary and fold a pending
    # event in at the terminal result. The portal path rides the run env (the
    # same handle the hooks path reads).
    policy: StreamInjectionPolicy | None = None
    if on_boundary is None and on_result is None:
        from . import hooks

        env = invocation.env or {}
        portal_env = env.get("BRR_PORTAL_STATE")
        outbox_env = env.get("BRR_OUTBOX_DIR")
        flush_path = (
            Path(outbox_env) / hooks.FLUSH_SIGNAL_NAME if outbox_env else None
        )
        policy = StreamInjectionPolicy(
            Path(portal_env) if portal_env else None,
            flush_signal_path=flush_path,
        )
        on_boundary = policy.on_boundary
        on_result = policy.on_result

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

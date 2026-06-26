"""Tests for the streaming runner client.

The fixture mirrors Claude Code's ``--output-format stream-json`` schema as
exercised by the verified spike (2026-06-26): an ``init`` system event, an
assistant message that carries one or more ``tool_use`` blocks, a ``user``
event carrying the matching ``tool_result``, and a terminal ``result`` event.
The Codex fixtures mirror codex-cli 0.141.0 ``exec --json``: a thread id, a
turn, command execution items, agent-message items, and a terminal
``turn.completed``. Live probes validate both schemas; these tests pin the
parsing/boundary contract the rest of the build rests on.
"""

from __future__ import annotations

import json
from pathlib import Path

from brr import runner, runner_stream


def _line(obj: dict) -> str:
    return json.dumps(obj)


def _assistant_tool_use(tool_id: str, name: str, text: str = "") -> str:
    content: list[dict] = []
    if text:
        content.append({"type": "text", "text": text})
    content.append({"type": "tool_use", "id": tool_id, "name": name, "input": {}})
    return _line({"type": "assistant", "message": {"role": "assistant", "content": content}})


def _user_tool_result(tool_id: str, output: str = "ok") -> str:
    return _line(
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": tool_id, "content": output}
                ],
            },
        }
    )


def _result(text: str, is_error: bool = False) -> str:
    return _line(
        {"type": "result", "subtype": "success", "is_error": is_error, "result": text}
    )


def _codex_thread(thread_id: str = "019f04b5-77c8") -> str:
    return _line({"type": "thread.started", "thread_id": thread_id})


def _codex_turn_started() -> str:
    return _line({"type": "turn.started"})


def _codex_command_started(item_id: str, command: str = "printf ok") -> str:
    return _line(
        {
            "type": "item.started",
            "item": {
                "id": item_id,
                "type": "command_execution",
                "command": command,
                "aggregated_output": "",
                "exit_code": None,
                "status": "in_progress",
            },
        }
    )


def _codex_command_completed(
    item_id: str, command: str = "printf ok", output: str = "ok\n"
) -> str:
    return _line(
        {
            "type": "item.completed",
            "item": {
                "id": item_id,
                "type": "command_execution",
                "command": command,
                "aggregated_output": output,
                "exit_code": 0,
                "status": "completed",
            },
        }
    )


def _codex_agent_message(text: str) -> str:
    return _line(
        {
            "type": "item.completed",
            "item": {"id": "item_msg", "type": "agent_message", "text": text},
        }
    )


def _codex_turn_completed() -> str:
    return _line({"type": "turn.completed", "usage": {"input_tokens": 1}})


def _codex_turn_failed(message: str) -> str:
    return _line({"type": "turn.failed", "error": {"message": message}})


# A representative two-tool session.
SESSION = [
    _line({"type": "system", "subtype": "init", "model": "claude-opus-4-8"}),
    _assistant_tool_use("toolu_1", "Bash", text="running step one"),
    _user_tool_result("toolu_1", "step-one"),
    _assistant_tool_use("toolu_2", "Read"),
    _user_tool_result("toolu_2", "file contents"),
    _line({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "All done."}]}}),
    _result("Final summary: ran two tools."),
]


CODEX_SESSION = [
    _codex_thread("thread-1"),
    _codex_turn_started(),
    _codex_command_started("item_0", "printf CODEX_TOOL_OK"),
    _codex_command_completed("item_0", "printf CODEX_TOOL_OK", "CODEX_TOOL_OK\n"),
    _codex_agent_message("DONE"),
    _codex_turn_completed(),
]


# ── parse_event ──────────────────────────────────────────────────────────


def test_parse_event_valid():
    ev = runner_stream.parse_event(_result("hi"))
    assert ev is not None
    assert ev.type == "result"
    assert ev.result_text == "hi"


def test_parse_event_skips_noise():
    assert runner_stream.parse_event("") is None
    assert runner_stream.parse_event("   ") is None
    assert runner_stream.parse_event("not json at all") is None
    assert runner_stream.parse_event("[1, 2, 3]") is None  # not an object
    assert runner_stream.parse_event(_line({"no": "type"})) is None
    assert runner_stream.parse_event(_line({"type": 5})) is None  # non-string type


def test_iter_events_drops_unparseable_lines():
    lines = ["", "garbage", _result("x"), "{bad json"]
    events = list(runner_stream.iter_events(lines))
    assert [e.type for e in events] == ["result"]


# ── StreamEvent accessors ────────────────────────────────────────────────


def test_tool_use_and_result_accessors():
    asst = runner_stream.parse_event(_assistant_tool_use("toolu_9", "Grep"))
    assert [b["name"] for b in asst.tool_uses] == ["Grep"]
    assert asst.tool_results == []  # assistant carries no tool_result

    usr = runner_stream.parse_event(_user_tool_result("toolu_9"))
    assert [b["tool_use_id"] for b in usr.tool_results] == ["toolu_9"]
    assert usr.tool_uses == []


def test_accessors_tolerate_missing_fields():
    ev = runner_stream.parse_event(_line({"type": "assistant"}))
    assert ev.tool_uses == []
    ev2 = runner_stream.parse_event(_line({"type": "assistant", "message": "oops"}))
    assert ev2.tool_uses == []


# ── consume_stream ───────────────────────────────────────────────────────


def test_consume_stream_full_session():
    boundaries: list[runner_stream.StreamBoundary] = []
    outcome = runner_stream.consume_stream(SESSION, on_boundary=boundaries.append)

    assert outcome.saw_result is True
    assert outcome.result_text == "Final summary: ran two tools."
    assert outcome.is_error is False
    assert outcome.boundary_count == 2
    assert outcome.tool_use_count == 2

    assert [b.index for b in boundaries] == [1, 2]
    assert boundaries[0].tool_names == ["Bash"]
    assert boundaries[0].tool_use_ids == ["toolu_1"]
    assert boundaries[1].tool_names == ["Read"]


def test_consume_stream_codex_json_session():
    boundaries: list[runner_stream.StreamBoundary] = []
    outcome = runner_stream.consume_stream(CODEX_SESSION, on_boundary=boundaries.append)

    assert outcome.saw_result is True
    assert outcome.thread_id == "thread-1"
    assert outcome.result_text == "DONE"
    assert outcome.is_error is False
    assert outcome.boundary_count == 1
    assert outcome.tool_use_count == 1
    assert boundaries[0].tool_use_ids == ["item_0"]
    assert boundaries[0].tool_names == ["printf CODEX_TOOL_OK"]


def test_consume_stream_codex_error_message():
    outcome = runner_stream.consume_stream(
        [
            _line({"type": "error", "message": "model unsupported"}),
            _codex_turn_failed("same failure"),
        ]
    )

    assert outcome.is_error is True
    assert outcome.error_text == "same failure"


def test_consume_stream_no_callback_still_counts():
    outcome = runner_stream.consume_stream(SESSION)
    assert outcome.boundary_count == 2


def test_consume_stream_is_error_propagates():
    lines = [_result("partial", is_error=True)]
    outcome = runner_stream.consume_stream(lines)
    assert outcome.is_error is True
    assert outcome.result_text == "partial"


def test_consume_stream_tolerates_orphan_tool_result():
    # A tool_result with no preceding tool_use is still a real boundary,
    # just with an unknown name (replayed / truncated streams).
    boundaries: list[runner_stream.StreamBoundary] = []
    lines = [_user_tool_result("toolu_unseen"), _result("done")]
    outcome = runner_stream.consume_stream(lines, on_boundary=boundaries.append)
    assert outcome.boundary_count == 1
    assert boundaries[0].tool_names == [""]


def test_consume_stream_ignores_interleaved_garbage():
    lines = ["", "junk", *SESSION, "{broken"]
    outcome = runner_stream.consume_stream(lines)
    assert outcome.boundary_count == 2
    assert outcome.result_text == "Final summary: ran two tools."


# Noise events the REAL claude v2.1.191 stream-json surface interleaves,
# captured from a live haiku session (2026-06-26): rate-limit pings,
# per-turn ``thinking_tokens`` system events, and assistant messages whose
# content carries ``thinking`` blocks alongside ``text`` / ``tool_use``.
# The parser must skip every one of them — this pins that the boundary count
# and result capture are unaffected by the live schema, not just the clean
# synthetic fixture above.
REAL_NOISE = [
    _line({"type": "rate_limit_event", "tier": "default"}),
    _line({"type": "system", "subtype": "thinking_tokens", "count": 42}),
    _line({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "thinking", "thinking": "Let me echo ALPHA."}]}}),
]


def test_consume_stream_tolerates_real_cli_noise():
    lines = [
        _line({"type": "system", "subtype": "init", "model": "claude-haiku"}),
        *REAL_NOISE,
        _assistant_tool_use("toolu_a", "Bash", text=""),
        _user_tool_result("toolu_a", "ALPHA"),
        _line({"type": "system", "subtype": "thinking_tokens", "count": 7}),
        _line({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "done"},
            {"type": "text", "text": "Done."}]}}),
        _result("Done."),
    ]
    outcome = runner_stream.consume_stream(lines)
    assert outcome.boundary_count == 1
    assert outcome.tool_use_count == 1
    assert outcome.saw_result is True
    assert outcome.result_text == "Done."


def test_thinking_block_is_not_a_tool_use():
    # An assistant ``thinking`` block must not be miscounted as a tool_use.
    ev = runner_stream.parse_event(
        _line({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "pondering"}]}})
    )
    assert ev.tool_uses == []


def test_consume_stream_no_result_event():
    # A truncated stream (killed mid-run) yields no result text but doesn't crash.
    outcome = runner_stream.consume_stream(SESSION[:3])
    assert outcome.saw_result is False
    assert outcome.result_text is None
    assert outcome.boundary_count == 1


# ── consume_stream on_result seam (persistent stop-control) ──────────────


def test_consume_stream_on_result_stops_when_false():
    seen: list[str | None] = []
    lines = [_result("first"), _result("second")]

    def cb(outcome: runner_stream.StreamOutcome) -> bool:
        seen.append(outcome.result_text)
        return False  # close after the first result

    outcome = runner_stream.consume_stream(lines, on_result=cb)
    assert outcome.result_count == 1
    assert outcome.result_text == "first"
    assert seen == ["first"]


def test_consume_stream_on_result_continue_reads_next_turn():
    # Returning True keeps consuming — the turn a fold-in injection produced.
    lines = [
        _result("first"),
        _assistant_tool_use("t", "Bash"),
        _user_tool_result("t"),
        _result("second"),
    ]
    calls: list[str | None] = []

    def cb(outcome: runner_stream.StreamOutcome) -> bool:
        calls.append(outcome.result_text)
        return True

    outcome = runner_stream.consume_stream(lines, on_result=cb)
    assert outcome.result_count == 2
    assert outcome.result_text == "second"
    assert outcome.boundary_count == 1
    assert calls == ["first", "second"]


# ── StreamInjectionPolicy ────────────────────────────────────────────────


def _write_portal(path: Path, **payload) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _bnd() -> runner_stream.StreamBoundary:
    return runner_stream.StreamBoundary(index=1, tool_names=[], tool_use_ids=[])


def test_injection_policy_injects_pending_on_first_boundary(tmp_path):
    portal = tmp_path / "portal.json"
    _write_portal(
        portal,
        change_token="t1",
        attention={"pending_event_count": 1, "pending_outbox_file_count": 0},
        inbound={"events": [{"id": "e1", "source": "telegram", "summary": "hi"}]},
    )
    policy = runner_stream.StreamInjectionPolicy(portal)
    injected: list[str] = []
    policy.on_boundary(_bnd(), injected.append)
    assert len(injected) == 1 and "pending" in injected[0]
    # Same token → no second injection (change_token gate).
    policy.on_boundary(_bnd(), injected.append)
    assert len(injected) == 1


def test_injection_policy_prime_suppresses_unchanged_then_injects_on_move(tmp_path):
    portal = tmp_path / "portal.json"
    _write_portal(
        portal,
        change_token="t1",
        attention={"pending_event_count": 0, "pending_outbox_file_count": 0},
    )
    policy = runner_stream.StreamInjectionPolicy(portal)
    policy.prime_from_portal()  # the snapshot the prompt already carried
    injected: list[str] = []
    policy.on_boundary(_bnd(), injected.append)
    assert injected == []  # nothing changed since the prompt
    # A new event arrives mid-run → token moves → delta injects.
    _write_portal(
        portal,
        change_token="t2",
        attention={"pending_event_count": 1, "pending_outbox_file_count": 0},
        inbound={"events": [{"id": "e2", "source": "telegram", "summary": "later"}]},
    )
    policy.on_boundary(_bnd(), injected.append)
    assert len(injected) == 1 and "later" in injected[0]


def test_injection_policy_folds_pending_body_verbatim_once(tmp_path):
    portal = tmp_path / "portal.json"
    _write_portal(
        portal,
        change_token="t1",
        attention={"pending_event_count": 2, "pending_outbox_file_count": 0},
        inbound={"events": [
            {"id": "e1", "source": "telegram",
             "summary": "do x truncated...", "body": "please also do x in full"},
        ]},
    )
    policy = runner_stream.StreamInjectionPolicy(portal)
    injected: list[str] = []
    outcome = runner_stream.StreamOutcome(result_text="done")
    assert policy.on_result(outcome, injected.append) is True  # fold the turn
    assert len(injected) == 1
    # The verbatim body is relayed as the user's words (not the op summary),
    # under a neutral non-imperative header.
    assert "please also do x in full" in injected[0]
    assert "via telegram" in injected[0]
    # Once-only: the next result closes the session even with work pending.
    assert policy.on_result(outcome, injected.append) is False
    assert len(injected) == 1


def test_injection_policy_no_foldable_body_closes(tmp_path):
    # A pending event with no body (or none at all) is not folded — there's no
    # verbatim user message to relay, so the driver closes the session.
    portal = tmp_path / "portal.json"
    _write_portal(
        portal, change_token="t1",
        attention={"pending_event_count": 1},
        inbound={"events": [{"id": "e1", "source": "telegram", "summary": "s"}]},
    )
    policy = runner_stream.StreamInjectionPolicy(portal)
    injected: list[str] = []
    assert policy.on_result(runner_stream.StreamOutcome(), injected.append) is False
    assert injected == []


def test_injection_policy_touches_flush_at_boundary_and_result(tmp_path):
    # Both seams ask the daemon to drain the outbox promptly via the shared
    # .flush signal (the daemon stays the sole drainer).
    portal = tmp_path / "portal.json"
    _write_portal(portal, change_token="t1", attention={"pending_event_count": 0})
    flush = tmp_path / ".flush"
    policy = runner_stream.StreamInjectionPolicy(portal, flush_signal_path=flush)
    assert not flush.exists()
    policy.on_boundary(_bnd(), lambda _t: None)
    assert flush.exists()
    flush.unlink()
    policy.on_result(runner_stream.StreamOutcome(), lambda _t: None)
    assert flush.exists()


def test_injection_policy_missing_portal_is_quiet(tmp_path):
    policy = runner_stream.StreamInjectionPolicy(tmp_path / "absent.json")
    injected: list[str] = []
    policy.prime_from_portal()
    policy.on_boundary(_bnd(), injected.append)
    assert policy.on_result(runner_stream.StreamOutcome(), injected.append) is False
    assert injected == []


# ── build_stream_cmd ─────────────────────────────────────────────────────


def test_build_stream_cmd_from_bundled_claude_profile():
    cmd = runner_stream.build_stream_cmd("claude", {})
    assert cmd[0] == "claude"
    assert "--input-format" in cmd and "stream-json" in cmd
    assert "--output-format" in cmd
    assert "--verbose" in cmd
    # The prompt is NOT appended as an argv token in streaming mode.
    assert cmd[-1] != "{prompt}"


def test_build_stream_cmd_from_bundled_codex_profile():
    cmd = runner_stream.build_stream_cmd("codex", {})
    assert cmd[:2] == ["codex", "exec"]
    assert "--json" in cmd
    assert cmd.count("--json") == 1
    # The prompt is appended by the Codex driver so the base argv can also
    # become `codex exec resume ...`.
    assert "{prompt}" not in cmd


def test_build_stream_cmd_infers_codex_flags_without_stream_field(monkeypatch):
    # Direct run_stream callers may name codex even when a project override has
    # not yet grown `stream: codex`; don't fall back to Claude stream flags.
    monkeypatch.setattr(
        runner,
        "_load_profiles",
        lambda repo_root=None: {"codex": {"cmd": "codex exec"}},
    )
    cmd = runner_stream.build_stream_cmd("codex", {})
    assert "--json" in cmd
    assert "--input-format" not in cmd


def test_build_stream_cmd_does_not_duplicate_existing_flags():
    cmd = runner_stream.build_stream_cmd(
        "x", {"runner_cmd": "claude --print --input-format stream-json"}
    )
    assert cmd.count("--input-format") == 1
    # Missing flags are still added.
    assert "--output-format" in cmd
    assert "--verbose" in cmd


def test_build_stream_cmd_drops_prompt_placeholder():
    cmd = runner_stream.build_stream_cmd("x", {"runner_cmd": ["mytool", "{prompt}"]})
    assert "{prompt}" not in cmd
    assert cmd[0] == "mytool"


def test_build_stream_cmd_strips_print_flag(monkeypatch):
    # --print forces a single-turn session; streaming runs persistent, so it
    # must be stripped while other profile flags survive.
    monkeypatch.setattr(
        runner,
        "_load_profiles",
        lambda repo_root=None: {
            "claude": {"cmd": "claude --print --dangerously-skip-permissions"}
        },
    )
    cmd = runner_stream.build_stream_cmd("claude", {})
    assert "--print" not in cmd
    assert "--dangerously-skip-permissions" in cmd
    assert "--input-format" in cmd


def test_build_stream_cmd_strips_short_print_flag():
    cmd = runner_stream.build_stream_cmd("x", {"runner_cmd": "claude -p"})
    assert "-p" not in cmd
    assert "--output-format" in cmd


# ── stream_flavour ───────────────────────────────────────────────────────


def test_stream_flavour_on_bundled_claude_profile():
    # Step 3 wired claude onto the streaming path: the bundled profile opts in.
    assert runner_stream.stream_flavour("claude") == "claude"


def test_stream_flavour_on_bundled_codex_profile():
    assert runner_stream.stream_flavour("codex") == "codex"


def test_stream_flavour_absent_on_bare_alias_profiles():
    # The --bare API-only aliases stay on the blocking path (no stream:).
    assert runner_stream.stream_flavour("claude-bare-api-only") is None


def test_stream_flavour_reads_field(monkeypatch):
    monkeypatch.setattr(
        runner, "_load_profiles", lambda repo_root=None: {"claude": {"stream": "Claude"}}
    )
    assert runner_stream.stream_flavour("claude") == "claude"
    assert runner_stream.stream_flavour("missing") is None


# ── user_message_json ────────────────────────────────────────────────────


def test_user_message_json_framing():
    raw = runner_stream.user_message_json("hello there")
    assert raw.endswith("\n")
    obj = json.loads(raw)
    assert obj["type"] == "user"
    assert obj["message"]["role"] == "user"
    assert obj["message"]["content"][0]["text"] == "hello there"


# ── run_stream live driver (fake subprocess) ─────────────────────────────


class _FakeStdin:
    def __init__(self) -> None:
        self.written: list[str] = []
        self.closed = False

    def write(self, text: str) -> None:
        self.written.append(text)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class _FakePopen:
    def __init__(self, stdout_lines: list[str], stderr_lines: list[str] | None = None):
        self.stdin = _FakeStdin()
        self.stdout = iter(stdout_lines)
        self.stderr = iter(stderr_lines or [])
        self.returncode = 0
        self._waited = False

    def wait(self, timeout=None):
        self._waited = True
        return self.returncode


def _make_invocation(tmp_path: Path, response_path: Path | None = None):
    return runner.RunnerInvocation(
        kind="run",
        label="t",
        prompt="do the thing",
        cwd=tmp_path,
        repo_root=tmp_path,
        response_path=str(response_path) if response_path else None,
    )


def test_run_stream_captures_result_and_writes_response(tmp_path, monkeypatch):
    response_path = tmp_path / "resp.md"
    fake = _FakePopen([line + "\n" for line in SESSION], ["progress\n"])
    monkeypatch.setattr(runner_stream.subprocess, "Popen", lambda *a, **k: fake)

    boundaries: list[runner_stream.StreamBoundary] = []
    result = runner_stream.run_stream(
        "claude",
        _make_invocation(tmp_path, response_path),
        {},
        on_boundary=lambda b, inject: boundaries.append(b),
    )

    assert result.stdout == "Final summary: ran two tools."
    assert result.returncode == 0
    assert result.ok
    assert response_path.read_text() == "Final summary: ran two tools."
    # Prompt was sent as the first stdin user message; stdin was closed.
    assert json.loads(fake.stdin.written[0])["message"]["content"][0]["text"] == "do the thing"
    assert fake.stdin.closed is True
    assert len(boundaries) == 2
    assert result.stderr == "progress\n"
    # Active-proc handle is cleared after the run (kill_active stays safe).
    assert runner._active_proc is None


def test_run_stream_codex_captures_json_result(tmp_path, monkeypatch):
    response_path = tmp_path / "resp.md"
    fake = _FakePopen([line + "\n" for line in CODEX_SESSION], ["progress\n"])
    captured: dict = {}
    monkeypatch.setattr(
        runner,
        "_load_profiles",
        lambda repo_root=None: {"codex": {"cmd": "codex exec"}},
    )

    def _fake_popen(cmd, *args, **kwargs):
        captured["cmd"] = cmd
        captured["stdin"] = kwargs.get("stdin")
        return fake

    monkeypatch.setattr(runner_stream.subprocess, "Popen", _fake_popen)

    result = runner_stream.run_stream(
        "codex",
        _make_invocation(tmp_path, response_path),
        {},
    )

    assert result.stdout == "DONE"
    assert result.returncode == 0
    assert result.ok
    assert response_path.read_text() == "DONE"
    assert captured["cmd"][-1] == "do the thing"
    assert "--json" in captured["cmd"]
    assert captured["stdin"] is runner_stream.subprocess.DEVNULL
    assert result.stderr == "progress\n"
    assert runner._active_proc is None


def test_run_stream_codex_resumes_once_for_folded_pending_event(
    tmp_path, monkeypatch
):
    portal = tmp_path / "portal-state.json"
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _write_portal(portal, change_token="t0", attention={"pending_event_count": 0})

    first = _FakePopen([
        _codex_thread("thread-9") + "\n",
        _codex_agent_message("FIRST") + "\n",
        _codex_turn_completed() + "\n",
    ])
    second = _FakePopen([
        _codex_agent_message("SECOND") + "\n",
        _codex_turn_completed() + "\n",
    ])
    fakes = iter([first, second])
    commands: list[list[str]] = []
    monkeypatch.setattr(
        runner,
        "_load_profiles",
        lambda repo_root=None: {"codex": {"cmd": "codex exec", "stream": "codex"}},
    )

    def _fake_popen(cmd, *args, **kwargs):
        commands.append(cmd)
        if len(commands) == 1:
            _write_portal(
                portal,
                change_token="t1",
                attention={
                    "pending_event_count": 1,
                    "pending_outbox_file_count": 0,
                },
                inbound={
                    "events": [
                        {
                            "id": "e1",
                            "source": "telegram",
                            "summary": "later",
                            "body": "please handle the follow-up",
                        }
                    ]
                },
            )
        return next(fakes)

    monkeypatch.setattr(runner_stream.subprocess, "Popen", _fake_popen)
    invocation = runner.RunnerInvocation(
        kind="run",
        label="t",
        prompt="go",
        cwd=tmp_path,
        repo_root=tmp_path,
        env={
            "BRR_PORTAL_STATE": str(portal),
            "BRR_OUTBOX_DIR": str(outbox),
        },
    )

    result = runner_stream.run_stream("codex", invocation, {})

    assert result.stdout == "SECOND"
    assert len(commands) == 2
    assert commands[0][-1] == "go"
    assert commands[1][:3] == ["codex", "exec", "resume"]
    assert "thread-9" in commands[1]
    assert "please handle the follow-up" in commands[1][-1]
    assert (outbox / ".flush").exists()


def test_run_stream_codex_surfaces_structured_error(tmp_path, monkeypatch):
    fake = _FakePopen([
        _line({"type": "error", "message": "bad model"}) + "\n",
        _codex_turn_failed("bad model") + "\n",
    ])
    monkeypatch.setattr(
        runner,
        "_load_profiles",
        lambda repo_root=None: {"codex": {"cmd": "codex exec", "stream": "codex"}},
    )
    monkeypatch.setattr(runner_stream.subprocess, "Popen", lambda *a, **k: fake)

    result = runner_stream.run_stream("codex", _make_invocation(tmp_path), {})

    assert result.returncode == 1
    assert "bad model" in result.stderr


def test_run_stream_missing_binary(tmp_path, monkeypatch):
    def _boom(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(runner_stream.subprocess, "Popen", _boom)
    result = runner_stream.run_stream("claude", _make_invocation(tmp_path), {})
    assert result.returncode == 127
    assert "not found on PATH" in result.stderr


def test_run_stream_error_result_marks_failure(tmp_path, monkeypatch):
    lines = [_result("oops", is_error=True) + "\n"]
    fake = _FakePopen(lines)
    monkeypatch.setattr(runner_stream.subprocess, "Popen", lambda *a, **k: fake)
    result = runner_stream.run_stream("claude", _make_invocation(tmp_path), {})
    assert result.returncode == 1
    assert not result.ok


def test_run_stream_default_policy_injects_changed_portal(tmp_path, monkeypatch):
    # The default policy (no explicit callbacks) reads BRR_PORTAL_STATE and
    # weaves a delta in at the boundary when a new event arrives mid-run.
    states = iter(
        [
            {"change_token": "t0"},  # prime: the snapshot the prompt carried
            {  # a follow-up landed by the first tool boundary
                "change_token": "t1",
                "attention": {"pending_event_count": 1, "pending_outbox_file_count": 0},
                "inbound": {
                    "events": [
                        {"id": "e9", "source": "telegram", "summary": "new follow-up"}
                    ]
                },
            },
        ]
    )
    last: dict = {}

    def fake_read(path):
        nonlocal last
        try:
            last = next(states)
        except StopIteration:
            pass
        return last

    monkeypatch.setattr(runner_stream, "_read_portal", fake_read)
    fake = _FakePopen([line + "\n" for line in SESSION])
    monkeypatch.setattr(runner_stream.subprocess, "Popen", lambda *a, **k: fake)

    invocation = runner.RunnerInvocation(
        kind="run",
        label="t",
        prompt="go",
        cwd=tmp_path,
        repo_root=tmp_path,
        env={"BRR_PORTAL_STATE": str(tmp_path / "portal-state.json")},
    )
    runner_stream.run_stream("claude", invocation, {})

    texts = [
        json.loads(w)["message"]["content"][0]["text"] for w in fake.stdin.written
    ]
    assert texts[0] == "go"  # the prompt is the first stdin message
    assert any("new follow-up" in t for t in texts[1:])  # the delta was woven in
    assert fake.stdin.closed is True

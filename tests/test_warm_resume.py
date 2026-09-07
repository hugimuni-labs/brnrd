"""THE WARM RESUME — the claude half of a native seat resume.

A parked claude seat keeps its ``session_id`` (from the ``--output-format
json`` envelope) and reopens it with ``claude --resume <id>`` on release,
instead of a fresh boot. Recovery path for a *forced* process end (daemon
reload, quota wall, crash) — ``brnrd await`` remains the resting state.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from brr import daemon, runner
from brr.run import Run


def test_extract_claude_session_id_reads_the_raw_envelope():
    env = json.dumps({"type": "result", "session_id": "abc-123", "result": "hi"})
    assert runner._extract_claude_session_id("claude-fable", env) == "abc-123"
    assert runner._extract_claude_session_id("claude", "plain prose") is None
    assert runner._extract_claude_session_id("codex", env) is None
    assert runner._extract_claude_session_id("claude", json.dumps({"result": "x"})) is None


def test_insert_claude_resume_lands_after_the_executable_and_never_twice():
    tmpl = ["claude", "-p", "--output-format", "json", "{prompt}"]
    out = runner._insert_claude_resume(tmpl, "abc-123")
    assert out == ["claude", "--resume", "abc-123", "-p", "--output-format", "json", "{prompt}"]
    assert runner._insert_claude_resume(out, "other") == out
    assert runner._insert_claude_resume([], "x") == []


def _task(shell: str, env: str = "host", **meta) -> Run:
    t = Run(id="run-x", event_id="evt-x", body="", status="running", env=env)
    t.meta.update({"runner_shell": shell, **meta})
    return t


def test_native_session_id_picks_the_shells_own_fact():
    assert daemon._native_session_id_for(_task("codex", codex_thread_id="t1")) == "t1"
    assert daemon._native_session_id_for(_task("claude", claude_session_id="s1")) == "s1"
    # cross-wired ids never leak across shells
    assert daemon._native_session_id_for(_task("claude", codex_thread_id="t1")) is None
    assert daemon._native_session_id_for(_task("codex", claude_session_id="s1")) is None


def test_claude_native_is_host_only():
    """Claude Code keys sessions by cwd; a worktree root is gone at resume time."""
    assert daemon._native_session_id_for(
        _task("claude", env="worktree", claude_session_id="s1")
    ) is None
    assert daemon._native_session_id_for(
        _task("claude", env="host", claude_session_id="s1")
    ) == "s1"


def test_resume_session_refused_across_a_shell_switch():
    task = _task("claude", resume_native_session_id="codex-thread",
                 resume_native_provider="codex")
    choice = SimpleNamespace(shell="claude", name="claude-fable")
    assert daemon._resume_session_for_runner(task, choice) is None
    assert "Shell changed" in task.meta["resume_cold_reason"]
    same = _task("codex", resume_native_session_id="codex-thread",
                 resume_native_provider="codex")
    assert daemon._resume_session_for_runner(
        same, SimpleNamespace(shell="codex", name="codex")
    ) == "codex-thread"


def test_turn_end_park_arms_native_for_a_host_claude_seat():
    task = _task("claude", env="host", claude_session_id="s1")
    hold = daemon._park_seat_on_turn_end(task, {"seat.park_on_turn_end": True})
    assert hold["native_session_id"] == "s1"
    assert hold["resume_kind"] == "native"
    assert hold["provider"] == "claude"

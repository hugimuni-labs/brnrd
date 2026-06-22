"""Tests for the runner hooks back channel (``brr hook <phase>``)."""

from __future__ import annotations

import json

from brr import hooks


def _portal(tmp_path, *, token="t1", pending=0, events=None):
    payload = {
        "run": {"id": "run-1", "event_id": "evt-1", "phase": "running"},
        "attention": {
            "pending_event_count": pending,
            "pending_outbox_file_count": 0,
        },
        "inbound": {"events": events or []},
        "outbound": {
            "replies_current": 0,
            "replies_other": 0,
            "outbound_messages": 0,
        },
        "budget": {"elapsed_seconds": 10, "budget_seconds": 3600},
        "change_token": token,
    }
    path = tmp_path / "portal-state.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _env(tmp_path, flavour="claude"):
    return {
        "BRR_RUN_ID": "run-1",
        "BRR_EVENT_ID": "evt-1",
        "BRR_RUNNER": flavour,
        "BRR_OUTBOX_DIR": str(tmp_path),
        "BRR_PORTAL_STATE": str(tmp_path / "portal-state.json"),
    }


def test_post_tool_touches_flush_and_injects_on_change(tmp_path):
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    out, code = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))
    assert code == 0
    # Flush signal dropped for the daemon to drain on.
    assert (tmp_path / hooks.FLUSH_SIGNAL_NAME).exists()
    # Claude rendering carries the injected delta.
    ctx = out["hookSpecificOutput"]
    assert ctx["hookEventName"] == "PostToolUse"
    assert "pending" in ctx["additionalContext"]
    assert "evt-2" in ctx["additionalContext"]


def test_post_tool_no_reinject_when_token_unchanged(tmp_path):
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    env = _env(tmp_path)
    first, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    assert "hookSpecificOutput" in first
    # Same token → no second injection (would be noise).
    second, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    assert "hookSpecificOutput" not in second


def test_post_tool_reinjects_when_token_moves(tmp_path):
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    env = _env(tmp_path)
    hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    _portal(tmp_path, token="t2", pending=2,
            events=[{"id": "evt-3", "source": "telegram", "summary": "again"}])
    out, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    assert "evt-3" in out["hookSpecificOutput"]["additionalContext"]


def test_stop_blocks_once_when_pending(tmp_path):
    _portal(tmp_path, token="t1", pending=2,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    env = _env(tmp_path)
    first, code = hooks.run_hook(hooks.PHASE_STOP, "{}", env)
    assert code == 0
    assert first["decision"] == "block"
    assert "pending" in first["reason"]
    # Second stop must not block forever — the nudge fired once.
    second, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", env)
    assert second.get("decision") != "block"


def test_stop_does_not_block_when_nothing_pending(tmp_path):
    _portal(tmp_path, token="t1", pending=0)
    out, code = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    assert out.get("decision") != "block"
    assert code == 0


def test_session_start_seeds(tmp_path):
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_SESSION_START, "{}", _env(tmp_path))
    # Seed injects even with nothing pending (it's the initial capsule).
    assert "seed" in out["hookSpecificOutput"]["additionalContext"]


def test_codex_block_renders_continue_false(tmp_path):
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    out, code = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path, "codex"))
    assert out["continue"] is False
    assert out["stopReason"]
    assert code == 0


def test_gemini_block_uses_deny_exit_2(tmp_path):
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    out, code = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path, "gemini"))
    assert out["decision"] == "deny"
    assert code == 2


def test_unknown_flavour_returns_neutral(tmp_path):
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    out, code = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path, "custom"))
    assert "inject" in out and "block" in out
    assert code == 0


def test_unknown_phase_is_noop(tmp_path):
    out, code = hooks.run_hook("before-model", "{}", _env(tmp_path))
    assert out == {}
    assert code == 0


def test_missing_portal_state_is_graceful(tmp_path):
    # No portal-state.json written — post-tool still flushes, no inject.
    out, code = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))
    assert code == 0
    assert (tmp_path / hooks.FLUSH_SIGNAL_NAME).exists()
    assert "hookSpecificOutput" not in out

"""Tests for the runner hooks back channel (``brr hook <phase>``)."""

from __future__ import annotations

import json

from brr import hooks


def _portal(tmp_path, *, token="t1", pending=0, events=None, scm=None,
            resources=None):
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
    if scm is not None:
        payload["scm"] = scm
    if resources is not None:
        payload["resources"] = resources
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
    # Claude rendering carries the injected delta. Post-tool maps to
    # PostToolBatch for claude (once per tool batch).
    ctx = out["hookSpecificOutput"]
    assert ctx["hookEventName"] == "PostToolBatch"
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


def test_stop_injects_affirmative_zero_pending_signal(tmp_path):
    # "Knowing there's no events explicitly is also a signal": the closeout
    # boundary renders unconditionally, even with nothing pending and the
    # token unchanged, so the resident gets an explicit all-clear, not silence.
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "closeout" in ctx
    assert "0 pending event(s)" in ctx


def test_stop_surfaces_unpushed_and_modified_scm(tmp_path):
    _portal(
        tmp_path, token="t1", pending=0,
        scm={"known": True, "branch": "brr/run-x",
             "unpushed_commits": 2, "modified_files": 3},
    )
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "2 commit(s) not pushed" in ctx
    assert "3 modified file(s)" in ctx
    assert "brr/run-x" in ctx


def test_seed_surfaces_scm_when_dirty(tmp_path):
    _portal(
        tmp_path, token="t1", pending=0,
        scm={"known": True, "branch": "brr/run-x",
             "unpushed_commits": 1, "modified_files": 0},
    )
    out, _ = hooks.run_hook(hooks.PHASE_SESSION_START, "{}", _env(tmp_path))
    assert "1 commit(s) not pushed" in out["hookSpecificOutput"]["additionalContext"]


def test_stop_silent_scm_when_clean(tmp_path):
    _portal(
        tmp_path, token="t1", pending=0,
        scm={"known": True, "branch": "brr/run-x",
             "unpushed_commits": 0, "modified_files": 0},
    )
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    assert "scm:" not in out["hookSpecificOutput"]["additionalContext"]


def test_post_tool_never_renders_scm(tmp_path):
    # SCM posture is a boundary signal; mid-run it must stay quiet even when
    # the token moves, so editing churn doesn't spam a push reminder.
    _portal(
        tmp_path, token="t1", pending=1,
        events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}],
        scm={"known": True, "branch": "brr/run-x",
             "unpushed_commits": 2, "modified_files": 3},
    )
    out, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))
    assert "scm:" not in out["hookSpecificOutput"]["additionalContext"]


def test_scm_unknown_is_silent(tmp_path):
    _portal(
        tmp_path, token="t1", pending=0,
        scm={"known": False, "branch": None,
             "unpushed_commits": 0, "modified_files": 0},
    )
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    assert "scm:" not in out["hookSpecificOutput"]["additionalContext"]


def test_seed_surfaces_resources_with_known_quota_and_placeholders(tmp_path):
    _portal(
        tmp_path, token="t1", pending=0,
        resources={
            "quota": {"status": "known", "summary": "weekly 42% - resets 3d"},
            "cost": {"status": "unavailable"},
            "coexisting_runs": {"status": "unavailable"},
            "remote_scm": {"status": "unavailable"},
        },
    )
    out, _ = hooks.run_hook(hooks.PHASE_SESSION_START, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "resources:" in ctx
    assert "quota=weekly 42% - resets 3d" in ctx
    assert "cost=unavailable" in ctx
    assert "coexisting-runs=unavailable" in ctx
    assert "remote-scm=unavailable" in ctx


def test_post_tool_never_renders_resources(tmp_path):
    # Like scm:, the work-status line is a seed/stop boundary signal — mid-run
    # it must stay quiet even when the token moves.
    _portal(
        tmp_path, token="t1", pending=1,
        events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}],
        resources={"quota": {"status": "unavailable"},
                   "cost": {"status": "unavailable"},
                   "coexisting_runs": {"status": "unavailable"},
                   "remote_scm": {"status": "unavailable"}},
    )
    out, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))
    assert "resources:" not in out["hookSpecificOutput"]["additionalContext"]


def test_codex_block_renders_continue_false(tmp_path):
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    out, code = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path, "codex"))
    assert out["continue"] is False
    assert out["stopReason"]
    assert code == 0


def test_codex_injects_via_hookspecificoutput(tmp_path):
    # Codex accepts the same hookSpecificOutput.additionalContext envelope as
    # claude (fire-verified 2026-06-27), and post-tool maps to PostToolUse
    # (codex has no PostToolBatch).
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    out, code = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path, "codex"))
    ctx = out["hookSpecificOutput"]
    assert ctx["hookEventName"] == "PostToolUse"
    assert "evt-2" in ctx["additionalContext"]
    assert code == 0


def test_stop_folds_pending_body_verbatim(tmp_path):
    # A foldable pending event (carries a body) makes the Stop block relay the
    # body verbatim as the user's words, not the generic nudge.
    _portal(tmp_path, token="t1", pending=1, events=[{
        "id": "evt-2", "source": "telegram", "summary": "do the thing",
        "body": "please also rename the widget",
    }])
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    assert out["decision"] == "block"
    assert "please also rename the widget" in out["reason"]
    assert "folded-in follow-up" in out["reason"]


def test_codex_hook_args_wellformed(tmp_path, monkeypatch):
    monkeypatch.setattr(hooks.shutil, "which", lambda _name: "/usr/bin/brr")
    assert hooks.codex_hook_capability() is True
    args = hooks.codex_hook_args("brr")
    # Three -c overrides, one per phase, each a single argv token.
    assert args.count("-c") == 3
    joined = " ".join(args)
    assert "hooks.PostToolUse=" in joined
    assert "hooks.Stop=" in joined
    assert "hooks.SessionStart=" in joined
    # Omitted matcher is intentional: Codex treats it as match-all for
    # supported events, so every tool/stop/session boundary reaches brr.
    assert "matcher" not in joined
    assert 'command="brr hook post-tool"' in joined


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


# ── Config generation (brr-managed per-run native hook config) ───────────


def test_hook_config_supported_only_claude_today():
    # ``hook_config_supported`` is the *settings-file* install gate. Codex is
    # hooks-capable but installs via argv (codex_hook_args), so it is excluded
    # here; Gemini has no emitter yet.
    assert hooks.hook_config_supported("claude") is True
    assert hooks.hook_config_supported("codex") is False
    assert hooks.hook_config_supported("gemini") is False
    assert hooks.hook_config_supported(None) is False
    assert hooks.hook_config_supported("") is False


def test_install_hook_config_writes_wellformed_claude_settings(tmp_path):
    path = hooks.install_hook_config("claude", tmp_path, brr_bin="brr")
    assert path == tmp_path / ".claude" / "settings.local.json"
    settings = json.loads(path.read_text(encoding="utf-8"))
    hook_block = settings["hooks"]
    # All three abstract phases map to their native claude event names,
    # each invoking ``brr hook <phase>`` — the keystone the wiring relies on.
    assert set(hook_block) == {"PostToolBatch", "Stop", "SessionStart"}
    cmds = {
        name: entries[0]["hooks"][0]["command"]
        for name, entries in hook_block.items()
    }
    assert cmds["PostToolBatch"] == "brr hook post-tool"
    assert cmds["Stop"] == "brr hook stop"
    assert cmds["SessionStart"] == "brr hook session-start"


def test_install_hook_config_merges_and_preserves_user_keys(tmp_path):
    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir()
    (settings_dir / "settings.local.json").write_text(
        json.dumps(
            {
                "permissions": {"allow": ["Bash(ls)"]},
                "hooks": {"PreToolUse": [{"hooks": []}]},
            }
        ),
        encoding="utf-8",
    )
    path = hooks.install_hook_config("claude", tmp_path)
    settings = json.loads(path.read_text(encoding="utf-8"))
    # User's non-hook keys survive untouched...
    assert settings["permissions"] == {"allow": ["Bash(ls)"]}
    # ...a user hook brr doesn't own is preserved alongside brr's phases.
    assert "PreToolUse" in settings["hooks"]
    assert "PostToolBatch" in settings["hooks"]


def test_install_hook_config_unsupported_flavour_is_noop(tmp_path):
    assert hooks.install_hook_config("codex", tmp_path) is None
    assert not (tmp_path / ".claude").exists()


def test_hook_capability_precheck(tmp_path, monkeypatch):
    # Pretend brr is on PATH so the precheck's only variables are flavour /
    # cwd writability.
    monkeypatch.setattr(hooks.shutil, "which", lambda _name: "/usr/bin/brr")
    assert hooks.hook_capability("claude", tmp_path) is True
    # Unsupported flavour → degrade.
    assert hooks.hook_capability("codex", tmp_path) is False
    assert hooks.hook_capability(None, tmp_path) is False
    # Missing cwd → degrade.
    assert hooks.hook_capability("claude", tmp_path / "nope") is False
    # brr not invocable → degrade.
    monkeypatch.setattr(hooks.shutil, "which", lambda _name: None)
    assert hooks.hook_capability("claude", tmp_path) is False

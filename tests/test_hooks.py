"""Tests for the runner hooks back channel (``brnrd hook <phase>``)."""

from __future__ import annotations

import json

from brr import hooks


def _portal(tmp_path, *, token="t1", pending=0, events=None, scm=None,
            resources=None, budget=None, outbound=None, card=None):
    payload = {
        "run": {"id": "run-1", "event_id": "evt-1", "phase": "running"},
        "attention": {
            "pending_event_count": pending,
            "pending_outbox_file_count": 0,
        },
        "inbound": {"events": events or []},
        "outbound": outbound or {
            "replies_current": 0,
            "replies_other": 0,
            "outbound_messages": 0,
        },
        "budget": budget or {"elapsed_seconds": 10, "budget_seconds": 3600},
        "change_token": token,
    }
    if scm is not None:
        payload["scm"] = scm
    if resources is not None:
        payload["resources"] = resources
    if card is not None:
        payload["card"] = card
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


def test_post_tool_pending_events_are_framed_as_action_not_telemetry(tmp_path):
    # 2026-07-05: a maintainer caught two same-thread follow-ups sitting
    # unacknowledged on the outward-facing .card for 8 minutes despite the
    # count appearing in every batch — the bare number reads as ambient
    # telemetry, not something to act on. Non-zero pending now carries an
    # explicit verb; the zero-pending line stays the plain affirmative.
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    out, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "Address each below" in ctx

    _portal(tmp_path, token="t2", pending=0)
    out2, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    ctx2 = out2["hookSpecificOutput"]["additionalContext"]
    assert "0 pending event(s)" in ctx2
    assert "Address each below" not in ctx2


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


def test_post_tool_surfaces_stale_card(tmp_path):
    # 2026-07-05: the card is the one live surface a watching user sees
    # between replies; unlike SCM, a stale note is a mid-run failure, so it
    # must render at post-tool, not just at closeout.
    _portal(
        tmp_path, token="t1", pending=0,
        card={"active": True, "text": "old note", "age_seconds": 400,
              "stale": True},
    )
    out, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "no change in 400s" in ctx
    assert "rewrite .card" in ctx


def test_post_tool_silent_when_card_fresh(tmp_path):
    _portal(
        tmp_path, token="t1", pending=0,
        card={"active": True, "text": "fresh note", "age_seconds": 5,
              "stale": False},
    )
    out, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))
    assert "hookSpecificOutput" not in out


def test_seed_surfaces_resources_with_known_quota_and_gaps(tmp_path):
    _portal(
        tmp_path, token="t1", pending=0,
        resources={
            "quota": {"status": "known", "summary": "weekly 42% - resets 3d"},
            "spend": {"status": "unimplemented",
                      "note": "no spend collector for this medium yet"},
            "context_window": {"status": "unimplemented",
                               "note": "no context-window collector for this "
                                       "medium yet"},
            "coexisting_runs": {"status": "unimplemented",
                                "note": "single-flight per dominion"},
            "remote_scm": {"status": "absent", "pr_state": "none",
                           "branch": "brr/x",
                           "note": "no PR recorded for this branch yet"},
        },
    )
    out, _ = hooks.run_hook(hooks.PHASE_SESSION_START, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "resources:" in ctx
    assert "quota=weekly 42% - resets 3d" in ctx
    # The gaps read as named states with their reason, not a flat "unavailable".
    assert "spend=unimplemented (no spend collector for this medium yet)" in ctx
    assert "coexisting-runs=unimplemented" in ctx
    assert "remote-scm=absent (no PR recorded for this branch yet)" in ctx
    assert "unavailable" not in ctx


def test_seed_surfaces_open_pr_posture(tmp_path):
    _portal(
        tmp_path, token="t1", pending=0,
        resources={
            "quota": {"status": "absent", "note": "no snapshot for this medium"},
            "spend": {"status": "unimplemented"},
            "context_window": {"status": "unimplemented"},
            "coexisting_runs": {"status": "unimplemented"},
            "remote_scm": {"status": "known", "pr_state": "open",
                           "pr_number": "207", "branch": "brr/x"},
        },
    )
    out, _ = hooks.run_hook(hooks.PHASE_SESSION_START, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "remote-scm=PR #207" in ctx
    assert "quota=absent (no snapshot for this medium)" in ctx


def test_post_tool_renders_resources_when_injection_fires(tmp_path):
    # Quota is a live wall, so when a post-tool boundary injects a portal-state
    # update it carries the work-status line too.
    _portal(
        tmp_path, token="t1", pending=1,
        events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}],
        resources={"quota": {"status": "absent"},
                   "spend": {"status": "unimplemented"},
                   "context_window": {"status": "unimplemented"},
                   "coexisting_runs": {"status": "unimplemented"},
                   "remote_scm": {"status": "absent"}},
    )
    out, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))
    assert "resources:" in out["hookSpecificOutput"]["additionalContext"]


def test_post_tool_can_inject_resource_only_update(tmp_path):
    _portal(
        tmp_path, token="t1", pending=0,
        resources={
            "quota": {"status": "known", "summary": "week 55% left"},
            "spend": {"status": "unimplemented"},
            "context_window": {"status": "unimplemented"},
            "coexisting_runs": {"status": "unimplemented"},
            "remote_scm": {"status": "absent"},
        },
    )
    out, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "resources:" in ctx
    assert "quota=week 55% left" in ctx


def test_stop_flags_no_outbound_messages(tmp_path):
    # Affirmative-empty: a closeout with nothing sent surfaces the absence.
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "no outbound messages sent yet" in ctx


def test_stop_silent_on_outbound_when_something_sent(tmp_path):
    _portal(
        tmp_path, token="t1", pending=0,
        outbound={"replies_current": 1, "replies_other": 0,
                  "outbound_messages": 1},
    )
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "no outbound messages sent yet" not in ctx
    assert "delivery so far" in ctx


def test_long_running_surfaced_when_over_soft_budget(tmp_path):
    _portal(
        tmp_path, token="t1", pending=0,
        budget={"elapsed_seconds": 4000, "budget_seconds": 3600,
                "long_running": True},
    )
    out, _ = hooks.run_hook(hooks.PHASE_SESSION_START, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "running long" in ctx


def test_long_running_quiet_within_budget(tmp_path):
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_SESSION_START, "{}", _env(tmp_path))
    assert "running long" not in out["hookSpecificOutput"]["additionalContext"]


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
    monkeypatch.setattr(hooks.shutil, "which", lambda _name: "/usr/bin/brnrd")
    assert hooks.codex_hook_capability() is True
    args = hooks.codex_hook_args()
    # Three -c overrides, one per phase, each a single argv token.
    assert args.count("-c") == 3
    joined = " ".join(args)
    assert "hooks.PostToolUse=" in joined
    assert "hooks.Stop=" in joined
    assert "hooks.SessionStart=" in joined
    # Omitted matcher is intentional: Codex treats it as match-all for
    # supported events, so every tool/stop/session boundary reaches brnrd.
    assert "matcher" not in joined
    assert 'command="brnrd hook post-tool"' in joined


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
    path = hooks.install_hook_config("claude", tmp_path)
    assert path == tmp_path / ".claude" / "settings.local.json"
    settings = json.loads(path.read_text(encoding="utf-8"))
    hook_block = settings["hooks"]
    # All three abstract phases map to their native claude event names,
    # each invoking ``brnrd hook <phase>`` — the keystone the wiring relies on.
    assert set(hook_block) == {"PostToolBatch", "Stop", "SessionStart"}
    cmds = {
        name: entries[0]["hooks"][0]["command"]
        for name, entries in hook_block.items()
    }
    assert cmds["PostToolBatch"] == "brnrd hook post-tool"
    assert cmds["Stop"] == "brnrd hook stop"
    assert cmds["SessionStart"] == "brnrd hook session-start"
    # statusLine is a TUI footer and does not fire under daemon --print runs,
    # so brr must not register a dead collector by default.
    assert "statusLine" not in settings


def test_install_hook_config_preserves_user_statusline(tmp_path):
    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir()
    (settings_dir / "settings.local.json").write_text(
        json.dumps({"statusLine": {"type": "command", "command": "my-bar"}}),
        encoding="utf-8",
    )
    path = hooks.install_hook_config("claude", tmp_path)
    settings = json.loads(path.read_text(encoding="utf-8"))
    # A user's own footer setting is preserved while brr's hooks still install.
    assert settings["statusLine"]["command"] == "my-bar"
    assert "PostToolBatch" in settings["hooks"]


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
    # Pretend brnrd is on PATH so the precheck's only variables are flavour /
    # cwd writability.
    monkeypatch.setattr(hooks.shutil, "which", lambda _name: "/usr/bin/brnrd")
    assert hooks.hook_capability("claude", tmp_path) is True
    # Unsupported flavour → degrade.
    assert hooks.hook_capability("codex", tmp_path) is False
    assert hooks.hook_capability(None, tmp_path) is False
    # Missing cwd → degrade.
    assert hooks.hook_capability("claude", tmp_path / "nope") is False
    # brnrd not invocable → degrade.
    monkeypatch.setattr(hooks.shutil, "which", lambda _name: None)
    assert hooks.hook_capability("claude", tmp_path) is False

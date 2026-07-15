"""Tests for the runner hooks back channel (``brnrd hook <phase>``)."""

from __future__ import annotations

import json
import subprocess

from brr import hooks


def _portal(tmp_path, *, token="t1", pending=0, events=None, scm=None,
            resources=None, budget=None, outbound=None, card=None,
            task_classification=None, current_event="evt-1"):
    # ``current_event`` mirrors production: the daemon always writes the key,
    # set for an addressed run and None for an unaddressed one (a scheduled
    # wake). Pass ``current_event=None`` to model the unaddressed shape — the
    # fixture must be able to express both, or a guard that depends on the
    # distinction can be "green" against a portal state that cannot occur.
    payload = {
        "run": {"id": "run-1", "event_id": "evt-1", "phase": "running"},
        "attention": {
            "pending_event_count": pending,
            "pending_outbox_file_count": 0,
        },
        "inbound": {"current_event": current_event, "events": events or []},
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
    if task_classification is not None:
        payload["task_classification"] = task_classification
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


def test_stop_reblocks_on_a_new_pending_event_after_an_earlier_fold_in(tmp_path):
    # 2026-07-08 (#282 follow-up): ``stop_blocked`` used to be a one-shot
    # bool that never reset, so only the *first* pending follow-up a run
    # ever saw got fold-in-blocked — a second, genuinely new follow-up
    # arriving later in the same run's lifetime rode along as inert
    # context instead of forcing the resident to address it before
    # exiting. Token-scoping the latch should let a distinct new pending
    # snapshot re-block even though an earlier one already consumed a
    # block.
    _portal(tmp_path, token="t1", pending=1, events=[{
        "id": "evt-2", "source": "telegram", "summary": "first",
        "body": "first follow-up",
    }])
    env = _env(tmp_path)
    first, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", env)
    assert first["decision"] == "block"
    assert "first follow-up" in first["reason"]

    # The first follow-up got folded in and addressed; portal now clean.
    _portal(tmp_path, token="t2", pending=0)
    quiet, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", env)
    assert quiet.get("decision") != "block"

    # A second, distinct follow-up arrives later in the same run.
    _portal(tmp_path, token="t3", pending=1, events=[{
        "id": "evt-3", "source": "telegram", "summary": "second",
        "body": "second follow-up",
    }])
    second, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", env)
    assert second["decision"] == "block"
    assert "second follow-up" in second["reason"]


def test_stop_does_not_reinject_identical_context_on_unchanged_token(tmp_path):
    # #282: after a fully clean, fully-delivered closeout (0 pending, token
    # unchanged), Claude Code's Stop hook kept re-firing 10-15+ times with
    # byte-identical state because the closeout render was unconditional on
    # *every* fire, not just the first one to see this snapshot — non-empty
    # ``additionalContext`` on every fire reads to the CLI as "still
    # something to weave in". The runner already has the affirmative
    # all-clear text in-context from the prior Stop fire; a repeat fire on
    # the same token should get an empty result (a real "nothing to add,
    # stop cleanly" signal) instead of the same text again.
    _portal(tmp_path, token="t1", pending=0)
    env = _env(tmp_path)
    first, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", env)
    assert "0 pending event(s)" in first["hookSpecificOutput"]["additionalContext"]

    second, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", env)
    assert "hookSpecificOutput" not in second
    assert second.get("decision") != "block"

    # A genuinely new snapshot (token moves) still renders — the gate is
    # per-token, not "only ever once for the whole run".
    _portal(tmp_path, token="t2", pending=0)
    third, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", env)
    assert "0 pending event(s)" in third["hookSpecificOutput"]["additionalContext"]


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


def test_stop_nudges_unwritten_task_classification(tmp_path):
    # 2026-07-08: a card-staleness-style forcing function, requested directly
    # after a run caught itself nearly closing out without writing this
    # control file — the miss is silent otherwise (no error, a `run_ledger`
    # row's task_classification just stays null forever).
    _portal(tmp_path, token="t1", pending=0,
            task_classification={"written": False})
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert ".task-classification" in ctx
    assert "not written yet" in ctx


def test_stop_silent_when_task_classification_written(tmp_path):
    _portal(tmp_path, token="t1", pending=0,
            task_classification={"written": True})
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert ".task-classification" not in ctx


def test_post_tool_never_renders_task_classification_nudge(tmp_path):
    # Unlike the card, an unwritten control file mid-run is not itself a
    # problem — it legitimately gets written anytime before closeout — so
    # this stays a stop-only boundary signal, same shape as SCM.
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}],
            task_classification={"written": False})
    out, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert ".task-classification" not in ctx


def test_seed_never_renders_task_classification_nudge(tmp_path):
    _portal(tmp_path, token="t1", pending=0,
            task_classification={"written": False})
    out, _ = hooks.run_hook(hooks.PHASE_SESSION_START, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert ".task-classification" not in ctx


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


def test_seed_surfaces_recorded_pr_posture(tmp_path):
    _portal(
        tmp_path, token="t1", pending=0,
        resources={
            "quota": {"status": "absent", "note": "no snapshot for this medium"},
            "spend": {"status": "unimplemented"},
            "context_window": {"status": "unimplemented"},
            "coexisting_runs": {"status": "unimplemented"},
            "remote_scm": {"status": "known", "pr_state": "recorded",
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
    assert "current event has no reply yet" in ctx


def test_stop_reply_guard_silent_on_an_unaddressed_run(tmp_path):
    # A scheduled wake has no current event, so "the current event has no
    # reply" is not a warning there — it is a false statement. The guard must
    # only assert a fact the run can be proven wrong about.
    _portal(tmp_path, token="t1", pending=0, current_event=None)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "current event has no reply yet" not in ctx


def test_stop_silent_on_outbound_when_something_sent(tmp_path):
    _portal(
        tmp_path, token="t1", pending=0,
        outbound={"replies_current": 1, "replies_other": 0,
                  "outbound_messages": 1},
    )
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "current event has no reply yet" not in ctx
    assert "delivery so far" in ctx


def test_stop_keeps_current_reply_guard_after_other_delivery(tmp_path):
    _portal(
        tmp_path, token="t1", pending=0,
        outbound={"replies_current": 0, "replies_other": 0,
                  "outbound_messages": 1},
    )
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "delivery so far" in ctx
    assert "current event has no reply yet" in ctx


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


# ── The closeout guard (`hooks.next_move`) ───────────────────────────────
#
# The contract `next_move` failed 0/6 across *both* arms of the drift bench —
# prose and mounted alike. Position could not fix it, because position was never
# the problem: the contract is read at wake and spent 60 turns later, at the one
# moment the model is busy ending. This is the escalation ladder's last rung —
# a contract prose cannot keep becomes code that cannot fail silently.


def _armed(tmp_path, flavour="claude"):
    env = _env(tmp_path, flavour)
    env["BRR_NEXT_MOVE_GUARD"] = "1"
    return env


def _stdin(reply=None, **extra):
    payload = dict(extra)
    if reply is not None:
        payload["last_assistant_message"] = reply
    return json.dumps(payload)


def test_closeout_grammar_is_the_products_and_the_bench_reads_it():
    """One grammar, one place. A probe with its own copy measures a contract
    nothing enforces — and the two drift the first time anyone tightens one."""
    from brr import bench

    assert bench.hooks.closeout_state is hooks.closeout_state


def test_guard_blocks_a_reply_that_ends_on_nothing(tmp_path):
    _portal(tmp_path, token="t1", pending=0)
    out, code = hooks.run_hook(
        hooks.PHASE_STOP,
        _stdin("I refactored the module and the tests pass."),
        _armed(tmp_path),
    )
    assert code == 0
    assert out["decision"] == "block"
    assert "ends on nothing" in out["reason"]


def test_guard_passes_every_closeout_the_contract_names(tmp_path):
    for reply in (
        "...\n\n**done** — committed abc1234 on brr/x",
        "...\n\ncontinuing — arms still running",
        "...\n\nblocked — needs the API token",
        "Which way?\n\n1. cut it\n2. keep the flag\n\nI'd take (1).",
    ):
        _portal(tmp_path, token="t1", pending=0)
        (tmp_path / hooks.HOOK_STATE_NAME).unlink(missing_ok=True)
        out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(reply), _armed(tmp_path))
        assert out.get("decision") != "block", reply


def test_guard_is_silent_without_the_artifact(tmp_path):
    """No `last_assistant_message` (codex today) → no assertion.

    The doctrine: a guard may only assert something the run can be proven wrong
    about. A guard that nags on a proxy it could not read is the exact bug class
    this repo spent the week killing — a status derived from an artifact, but not
    from *the* artifact.
    """
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _armed(tmp_path))
    assert out.get("decision") != "block"


def test_guard_is_off_unless_armed(tmp_path):
    """Default off — the control arm the bench measures against."""
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin("ends on nothing"), _env(tmp_path))
    assert out.get("decision") != "block"


def test_guard_never_loops(tmp_path):
    """#282 is the standing scar: a hook that re-fires into a run with nothing
    left to do burns the budget. Block once, then let the run end."""
    _portal(tmp_path, token="t1", pending=0)
    env = _armed(tmp_path)
    first, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin("no closeout"), env)
    assert first["decision"] == "block"
    second, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin("still no closeout"), env)
    assert second.get("decision") != "block"


def test_guard_respects_the_shells_own_loop_breaker(tmp_path):
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(
        hooks.PHASE_STOP,
        _stdin("no closeout", stop_hook_active=True),
        _armed(tmp_path),
    )
    assert out.get("decision") != "block"


# ── Escalated artifact obligations (card / task-classification) ───────────

_GOOD_REPLY = "wired it up.\n\n**done** — committed abc1234 on brr/x"


def _armed_obl(tmp_path, obligations="card,classification", flavour="claude"):
    env = _armed(tmp_path, flavour)
    env["BRR_CLOSEOUT_OBLIGATIONS"] = obligations
    return env


def test_guard_blocks_when_card_missing(tmp_path):
    """A closeout with a clean reply but no `.card` still blocks — the card is
    the surface the user watched the whole run."""
    _portal(tmp_path, token="t1", pending=0)
    (tmp_path / hooks.TASK_CLASSIFICATION_NAME).write_text("bugfix", encoding="utf-8")
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY), _armed_obl(tmp_path))
    assert out["decision"] == "block"
    assert ".card" in out["reason"]


def test_guard_blocks_when_classification_missing(tmp_path):
    _portal(tmp_path, token="t1", pending=0)
    (tmp_path / hooks.CARD_NAME).write_text("progress", encoding="utf-8")
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY), _armed_obl(tmp_path))
    assert out["decision"] == "block"
    assert ".task-classification" in out["reason"]


def test_guard_blank_artifact_counts_as_unwritten(tmp_path):
    """An empty / whitespace-only control file is not a written obligation."""
    _portal(tmp_path, token="t1", pending=0)
    (tmp_path / hooks.CARD_NAME).write_text("   \n", encoding="utf-8")
    (tmp_path / hooks.TASK_CLASSIFICATION_NAME).write_text("x", encoding="utf-8")
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY), _armed_obl(tmp_path))
    assert out["decision"] == "block"
    assert ".card" in out["reason"]


def test_guard_capsule_lists_every_unmet_at_once(tmp_path):
    """Reply ends on nothing AND both artifacts missing → one capsule naming
    all three, not three chained Stop blocks (#282 loop safety)."""
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin("just prose"), _armed_obl(tmp_path))
    assert out["decision"] == "block"
    reason = out["reason"]
    assert "ends on nothing" in reason
    assert ".card" in reason
    assert ".task-classification" in reason


def test_guard_passes_when_every_obligation_met(tmp_path):
    _portal(tmp_path, token="t1", pending=0)
    (tmp_path / hooks.CARD_NAME).write_text("progress", encoding="utf-8")
    (tmp_path / hooks.TASK_CLASSIFICATION_NAME).write_text("bugfix", encoding="utf-8")
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY), _armed_obl(tmp_path))
    assert out.get("decision") != "block"


def test_artifact_obligations_are_off_unless_armed(tmp_path):
    """Files missing, but only next_move armed (no BRR_CLOSEOUT_OBLIGATIONS) →
    the artifact checks stay silent, the control arm the bench measures."""
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY), _armed(tmp_path))
    assert out.get("decision") != "block"


def test_artifact_block_never_loops(tmp_path):
    """Fires once, then lets the run end — even if the file is never written."""
    _portal(tmp_path, token="t1", pending=0)
    env = _armed_obl(tmp_path)
    first, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY), env)
    assert first["decision"] == "block"
    second, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY), env)
    assert second.get("decision") != "block"


def test_a_waiting_user_outranks_the_shape_of_the_reply(tmp_path):
    """Pending events block first. A user's actual message beats the formatting
    of a reply that is about to be rewritten anyway."""
    _portal(tmp_path, token="t1", pending=1, events=[
        {"id": "evt-9", "source": "telegram", "body": "one more thing"},
    ])
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin("no closeout"), _armed(tmp_path))
    assert out["decision"] == "block"
    assert "one more thing" in out["reason"]
    assert "ends on nothing" not in out["reason"]


# ── The SCM closeout obligation (host work-loss block) ────────────────────


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True,
                   capture_output=True, text=True)


def _seeded_repo(tmp_path):
    """A git repo on `main` with one seed commit. Returns (repo_dir)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "seed")
    return repo


def _armed_scm(tmp_path, repo, obligations="scm", seed="main"):
    """Arm the SCM obligation with card+classification already satisfied, so a
    block can only come from the SCM clause. Outbox lives in `tmp_path`."""
    (tmp_path / hooks.CARD_NAME).write_text("progress", encoding="utf-8")
    (tmp_path / hooks.TASK_CLASSIFICATION_NAME).write_text("bugfix", encoding="utf-8")
    env = _armed_obl(tmp_path, obligations=obligations)
    env["BRR_REPO_DIR"] = str(repo)
    env["BRR_SEED_REF"] = seed
    return env


def test_scm_blocks_on_uncommitted_changes(tmp_path):
    """A host checkout with modified files at Stop loses work — block."""
    repo = _seeded_repo(tmp_path)
    (repo / "wip.txt").write_text("half-done\n", encoding="utf-8")
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY),
                            _armed_scm(tmp_path, repo))
    assert out["decision"] == "block"
    assert "uncommitted" in out["reason"]


def test_scm_blocks_on_unpushed_commits_with_receipt(tmp_path):
    """Committed on a branch but never pushed → block, with the diffstat
    receipt the maintainer asked for (`N commit(s) +x/−y on <branch>`)."""
    repo = _seeded_repo(tmp_path)
    _git(repo, "switch", "-qc", "brr/work")
    (repo / "feature.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "feature")
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY),
                            _armed_scm(tmp_path, repo))
    assert out["decision"] == "block"
    reason = out["reason"]
    assert "not pushed" in reason
    assert "1 commit(s) +3/" in reason  # diffstat receipt
    assert "brr/work" in reason


def test_scm_receipt_includes_pr_number_when_present(tmp_path):
    """When a `.pr` handle exists, the receipt names it — produce, not scold."""
    repo = _seeded_repo(tmp_path)
    _git(repo, "switch", "-qc", "brr/work")
    (repo / "feature.py").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "feature")
    (repo / "leftover.txt").write_text("dirty\n", encoding="utf-8")  # force a gap
    (tmp_path / ".pr").write_text("#42\n", encoding="utf-8")
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY),
                            _armed_scm(tmp_path, repo))
    assert out["decision"] == "block"
    assert "PR #42" in out["reason"]


def test_scm_silent_when_committed_and_pushed(tmp_path):
    """Nothing modified, nothing ahead of the seed → no work at risk, silent."""
    repo = _seeded_repo(tmp_path)
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY),
                            _armed_scm(tmp_path, repo))
    assert out.get("decision") != "block"


def test_scm_does_not_block_missing_pr_when_pushed(tmp_path):
    """Committed AND pushed (upstream at HEAD) but no `.pr` → NOT a block.

    The legit PR handoff (`gate: forge`) is a post-Stop action, so a missing
    PR on an already-pushed branch is not assertable work-loss. Deliberately
    out of the hard block.
    """
    repo = _seeded_repo(tmp_path)
    bare = tmp_path / "remote.git"
    _git(repo, "init", "-q", "--bare", str(bare))
    _git(repo, "switch", "-qc", "brr/work")
    (repo / "feature.py").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "feature")
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "-u", "origin", "brr/work")
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY),
                            _armed_scm(tmp_path, repo))
    assert out.get("decision") != "block"


def test_scm_silent_when_repo_dir_unset(tmp_path):
    """`scm` armed but no BRR_REPO_DIR (a worktree run) → unassertable, silent —
    the daemon only wires the repo dir for the host environment."""
    repo = _seeded_repo(tmp_path)
    (repo / "wip.txt").write_text("half\n", encoding="utf-8")
    env = _armed_scm(tmp_path, repo)
    del env["BRR_REPO_DIR"]
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY), env)
    assert out.get("decision") != "block"

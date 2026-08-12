"""Tests for the runner hooks back channel (``brnrd hook <phase>``)."""

from __future__ import annotations

import datetime
import hashlib
import json
import subprocess
import threading
import time

from brr import card, hooks, portals


def _portal(tmp_path, *, token="t1", pending=0, events=None, scm=None, produce=None,
            resources=None, budget=None, outbound=None, card=None,
            name=None, current_event="evt-1", current_event_replyable=True,
            notices=None, schedule=None):
    # ``current_event`` mirrors production: the daemon always writes the key,
    # set for an addressed run and None for an unaddressed one (a scheduled
    # wake). Pass ``current_event=None`` to model the unaddressed shape — the
    # fixture must be able to express both, or a guard that depends on the
    # distinction can be "green" against a portal state that cannot occur.
    #
    # ``current_event_replyable`` is the daemon's mechanical gate-ownership
    # fact (#562): a schedule wake carries a current event that no gate owns,
    # so ``current_event`` alone cannot express that shape. Pass False to
    # model it.
    payload = {
        "run": {"id": "run-1", "event_id": "evt-1", "phase": "running"},
        "attention": {
            "pending_event_count": pending,
            "pending_outbox_file_count": 0,
        },
        "inbound": {
            "current_event": current_event,
            "current_event_replyable": current_event_replyable,
            "events": events or [],
        },
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
    if produce is not None:
        payload["produce"] = produce
    if resources is not None:
        payload["resources"] = resources
    if card is not None:
        payload["card"] = card
    if notices is not None:
        payload["notices"] = notices
    if name is not None:
        payload["name"] = name
    if schedule is not None:
        payload["schedule"] = schedule
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


def test_stop_waits_for_flush_ack_then_reads_fresh_portal(tmp_path):
    """Stop decision is downstream of promotion, not racing runner exit."""
    _portal(tmp_path, token="before", pending=1, events=[
        {"id": "evt-2", "source": "telegram", "body": "already answered"},
    ])
    env = _env(tmp_path)
    env["BRR_FLUSH_SYNC"] = "1"

    def broker():
        flush = tmp_path / hooks.FLUSH_SIGNAL_NAME
        deadline = time.monotonic() + 2
        while not flush.exists() and time.monotonic() < deadline:
            time.sleep(0.005)
        token = flush.read_text(encoding="utf-8").strip()
        _portal(tmp_path, token="after", pending=0)
        (tmp_path / hooks.FLUSH_ACK_NAME).write_text(token, encoding="utf-8")

    thread = threading.Thread(target=broker)
    thread.start()
    out, code = hooks.run_hook(hooks.PHASE_STOP, "{}", env)
    thread.join(timeout=2)
    assert code == 0
    assert out.get("decision") != "block"
    assert "0 pending event(s)" in out["hookSpecificOutput"]["additionalContext"]


def test_post_tool_no_reinject_when_token_unchanged(tmp_path):
    """Rewritten for #1116's typed channel — the rule moved, deliberately.

    It used to hold with a *pending event* in the payload, which is the one
    case where it must not: an obligation repeats because nothing was
    *done*, not because nothing happened, and byte-identical is the
    signature of both. #963 argued exactly this and could not act on it
    because the channel was untyped; now it is, so an unmet obligation
    bypasses the token gate and the dedupe on purpose.

    What the test is *for* — an unchanged portal produces no second
    injection — is unchanged, and now asserted on the payload where it is
    actually true: nothing pending.
    """
    # Laden enough to render (a quota reading is a *vital* — the
    # maintainer's named exemption: no discharge, but must stay legible),
    # and carrying nothing anyone owes anyone.
    _portal(
        tmp_path, token="t1", pending=0, events=[],
        resources={"quota": {"status": "known", "summary": "session 80% left"}},
    )
    env = _env(tmp_path)
    first, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    assert "hookSpecificOutput" in first
    # Same token, nothing owed → no second injection (would be noise).
    second, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    assert "hookSpecificOutput" not in second


def test_an_unmet_obligation_repeats_until_it_is_discharged(tmp_path):
    """The other half, and the whole point of typing the channel (#1116).

    The maintainer's rule: *every notification is actionable and
    turn-off-able, unless it is a necessary tick update.* A pending event
    is actionable — so it must keep asking until it is answered, even
    though the portal token has not moved and the bytes are identical to
    last boundary. Suppressing it is the failure #963 named and could not
    prevent.
    """
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    env = _env(tmp_path)
    first, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    second, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    assert "hookSpecificOutput" in first
    assert "hookSpecificOutput" in second, "an unmet obligation went quiet"
    assert "evt-2" in second["hookSpecificOutput"]["additionalContext"]


def test_post_tool_reinjects_when_token_moves(tmp_path):
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    env = _env(tmp_path)
    hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    _portal(tmp_path, token="t2", pending=2,
            events=[{"id": "evt-3", "source": "telegram", "summary": "again"}])
    out, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    assert "evt-3" in out["hookSpecificOutput"]["additionalContext"]


def test_content_identical_block_is_suppressed_when_portal_token_moves(tmp_path):
    resources = {
        "quota": {"status": "known", "summary": "week 80% left"},
    }
    env = _env(tmp_path)
    _portal(tmp_path, token="t1", resources=resources)
    first, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    assert "q W80" in first["hookSpecificOutput"]["additionalContext"]

    # The portal snapshot moved, but the exact block the resident would see
    # did not. The content is the authority; a broad snapshot token is not.
    _portal(tmp_path, token="t2", resources=resources)
    second, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    assert "hookSpecificOutput" not in second


def test_content_changed_block_is_always_resent(tmp_path):
    env = _env(tmp_path)
    _portal(tmp_path, token="t1", resources={
        "quota": {"status": "known", "summary": "week 80% left"},
    })
    hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)

    _portal(tmp_path, token="t2", resources={
        "quota": {"status": "known", "summary": "week 79% left"},
    })
    changed, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    assert "hookSpecificOutput" in changed, changed
    assert "q W79" in changed["hookSpecificOutput"]["additionalContext"]


def test_content_block_never_seen_is_always_sent(tmp_path):
    _portal(tmp_path, token="t1", resources={
        "quota": {"status": "known", "summary": "week 80% left"},
    })
    first, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))
    assert "hookSpecificOutput" in first, first
    assert "q W80" in first["hookSpecificOutput"]["additionalContext"]
    state = json.loads((tmp_path / hooks.HOOK_STATE_NAME).read_text())
    assert hooks.PENDING_INJECT_KEY in state
    assert hooks.LAST_INJECT_KEY not in state


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
    # per-token, not "only ever once for the whole run". Content dedupe is
    # deliberately *not* layered on top of this one: the closeout render
    # carries obligations, and an unchanged obligation is unmet, not stale.
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


def test_post_tool_compresses_produce_into_the_bar_total(tmp_path):
    # #513: post-tool now compresses produce into the bar's `⚒<n>` total
    # rather than the composed "- produce: ..." breakdown — a dense mid-run
    # bar earns "how much", not "what" (see the stop test below for "what").
    _portal(
        tmp_path, token="t1", pending=1,
        events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}],
        produce={
            "known": True,
            "counts": {"commit": 2, "branch": 1, "pr": 1, "kb": 1,
                       "issue": 1},
            "latest_commit": "a1b2c3d",
            "branch": "brr/foo",
            "pr": 451,
        },
    )
    out, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "⚒6" in ctx
    assert "- produce:" not in ctx


def test_stop_surfaces_composed_produce_breakdown(tmp_path):
    # Seed/stop stay affirmative, clear prose (#513) — unlike post-tool, the
    # composed breakdown (not just a bar total) still renders there.
    _portal(
        tmp_path, token="t1", pending=0,
        produce={
            "known": True,
            "counts": {"commit": 2, "branch": 1, "pr": 1, "kb": 1,
                       "issue": 1},
            "latest_commit": "a1b2c3d",
            "branch": "brr/foo",
            "pr": 451,
        },
    )
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert (
        "- produce: 2 commit(s) (latest a1b2c3d) · branch brr/foo · "
        "PR #451 · 1 kb page · 1 issue"
    ) in ctx


def test_stop_briefing_carries_the_produce_manifest(tmp_path):
    """The resident reads its own manifest at closeout, not a count line.

    Counts answer "how much"; a resident writing a receipt is asking "what",
    and reconstructing that from memory is exactly how a run names three of
    its four commits (maintainer, 2026-07-19: "make the live accrued relics
    useful for you too"). Same records the node's `## Produce` renders.
    """
    _portal(
        tmp_path, token="t1", pending=0,
        produce={
            "known": True,
            "counts": {"commit": 1, "pr": 1},
            "latest_commit": "a1b2c3d",
            "branch": "brr/foo",
            "pr": 451,
            "records": [
                {"kind": "commit", "sha": "a1b2c3d99", "subject": "do it",
                 "url": "https://forge/c/a1b2c3d"},
                {"kind": "pr", "number": 451, "url": "https://forge/pr/451"},
            ],
        },
    )
    stop, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    ctx = stop["hookSpecificOutput"]["additionalContext"]
    assert "your produce this run" in ctx
    assert "\U0001f528 a1b2c3d do it \u2014 https://forge/c/a1b2c3d" in ctx
    assert "\U0001f500 PR #451 \u2014 https://forge/pr/451" in ctx

    # Mid-run the compression is right: the manifest is a closeout shape, and
    # repeating it at every tool boundary would be noise the reader learns to
    # skip.
    post, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))
    assert "your produce this run" not in (
        (post.get("hookSpecificOutput") or {}).get("additionalContext") or ""
    )


def test_produce_line_is_silent_when_empty(tmp_path):
    _portal(
        tmp_path, token="t1", pending=0,
        produce={"known": True, "counts": {}, "latest_commit": None,
                 "branch": "brr/foo", "pr": None},
    )
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    assert "produce:" not in out["hookSpecificOutput"]["additionalContext"]


def test_produce_only_does_not_open_mid_run_render_gate(tmp_path):
    path = _portal(
        tmp_path, token="t1", pending=0,
        produce={
            "known": True,
            "counts": {"commit": 1, "branch": 1},
            "latest_commit": "a1b2c3d",
            "branch": "brr/foo",
            "pr": None,
        },
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert hooks.format_delta(payload) is None


def test_midrun_nudges_unwritten_run_name_but_stop_does_not(tmp_path):
    _portal(
        tmp_path, token="t1", pending=1,
        events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}],
        name={"written": False}, budget={"elapsed_seconds": 240, "budget_seconds": 3600},
    )
    out, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))
    assert ".name" in out["hookSpecificOutput"]["additionalContext"]

    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    assert ".name" not in out["hookSpecificOutput"]["additionalContext"]


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
    # Quota is a live wall, so when a post-tool boundary injects (here,
    # because of a pending event) the bar carries the `q` quota chip too.
    # spend/context-window/remote-scm stay out of the bar on purpose (#513
    # scopes the compact rendering to actionable, glance-worthy facets; the
    # full facet line remains a seed/stop-only shape — see
    # test_seed_surfaces_resources_with_known_quota_and_gaps).
    _portal(
        tmp_path, token="t1", pending=1,
        events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}],
        resources={"quota": {"status": "known", "summary": "week 42% left"},
                   "spend": {"status": "unimplemented"},
                   "context_window": {"status": "unimplemented"},
                   "coexisting_runs": {"status": "unimplemented"},
                   "remote_scm": {"status": "absent"}},
    )
    out, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "q W42" in ctx
    assert "resources:" not in ctx


def test_post_tool_can_inject_resource_only_update(tmp_path):
    # Quota alone (no pending, no delivery, no stale card) still opens the
    # mid-run gate — a live wall changing is worth a boundary by itself.
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
    assert "q W55" in ctx


def test_stop_flags_no_outbound_messages(tmp_path):
    # Affirmative-empty: a closeout with nothing sent anywhere surfaces the
    # absence — as a warn that names the daemon's static dispatch of the
    # final message, never as an order to re-deliver through the outbox.
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "nothing communicated on any thread yet" in ctx
    assert "dispatches your final message" in ctx


def test_stop_reply_guard_silent_on_an_unaddressed_run(tmp_path):
    # A scheduled wake has no current event, so a waking-thread delivery
    # warning is not merely noisy there — it is a false statement. The guard
    # must only assert a fact the run can be proven wrong about.
    _portal(tmp_path, token="t1", pending=0, current_event=None)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "nothing communicated on any thread" not in ctx
    assert "waking thread itself has no reply" not in ctx


def test_stop_silent_on_outbound_when_something_sent(tmp_path):
    _portal(
        tmp_path, token="t1", pending=0,
        outbound={"replies_current": 1, "replies_other": 0,
                  "outbound_messages": 1},
    )
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "nothing communicated on any thread" not in ctx
    assert "waking thread itself has no reply" not in ctx
    assert "delivery so far" in ctx


def test_stop_informs_when_only_other_threads_answered(tmp_path):
    # Something was communicated, but not on the waking thread: the boundary
    # informs (the daemon will dispatch the final message there) rather than
    # compelling an outbox re-delivery — the double-post trap the old
    # "confirm this run answered the event it owes" imperative set.
    _portal(
        tmp_path, token="t1", pending=0,
        outbound={"replies_current": 0, "replies_other": 0,
                  "outbound_messages": 1},
    )
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "delivery so far" in ctx
    assert "waking thread itself has no reply yet" in ctx
    assert "nothing communicated on any thread" not in ctx


def test_stop_silent_when_gate_less_event_delivered_elsewhere(tmp_path):
    # #562: a schedule wake DOES carry a current event, so the old
    # ``current_event``-only gate passed and the reply nag fired — but the
    # router refuses ``event:`` replies to a source no gate owns, so
    # ``replies_current`` can never leave 0. The nag was un-clearable, and it
    # hit hardest the runs that had already reported on telegram.
    #
    # What this fences is the *nag*, and only the nag. It said "once anything
    # was delivered anywhere, silence is the success state" until #728, which
    # is no longer true of the cell as a whole: a gate-less run that delivered
    # now gets the routing fact, once, from the arm below. The distinction is
    # the point — an obligation with no available remedy must stay gone, while
    # a constant about the run's topology is said once and latched. So this
    # asserts the two *nag* strings stay absent, not that the briefing is
    # silent; see ``test_stop_gate_less_and_delivered_states_the_routing_fact``
    # for what may legitimately appear here.
    _portal(
        tmp_path, token="t1", pending=0, current_event_replyable=False,
        outbound={"replies_current": 0, "replies_other": 0,
                  "outbound_messages": 1},
    )
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "waking thread itself has no reply yet" not in ctx
    assert "nothing communicated on any thread" not in ctx


def test_stop_gate_less_and_silent_names_body_only_stdout(tmp_path):
    # Nothing communicated anywhere is still worth surfacing on a gate-less
    # run — but with the true mechanic, not the addressed-run one: nobody
    # dispatches the final message, it is kept as the run's body only, and
    # the fix is a user gate rather than "end on the reply".
    _portal(tmp_path, token="t1", pending=0, current_event_replyable=False)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "nothing communicated on any thread yet" in ctx
    assert "no gate owns this waking event" in ctx
    assert "body/message store only" in ctx
    assert "gate: telegram" in ctx
    # The addressed-run promise must not leak into the gate-less wording.
    assert "dispatches your final message to the waking thread" not in ctx


def test_stop_gate_owned_event_keeps_addressed_wording(tmp_path):
    # Regression fence for #562: gate-owned events keep both branches
    # byte-for-byte. Silent run → the dispatch promise; delivered-elsewhere
    # run → the waking-thread nag.
    _portal(tmp_path, token="t1", pending=0, current_event_replyable=True)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "nothing communicated on any thread yet" in ctx
    assert "dispatches your final message to the waking thread" in ctx
    assert "no gate owns this waking event" not in ctx

    _portal(
        tmp_path, token="t2", pending=0, current_event_replyable=True,
        outbound={"replies_current": 0, "replies_other": 0,
                  "outbound_messages": 1},
    )
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "waking thread itself has no reply yet" in ctx


def test_stop_missing_replyable_key_keeps_addressed_behavior(tmp_path):
    # An older or partial portal state has no ``current_event_replyable``.
    # Absent is not False: fall back to the historical addressed-run shape
    # rather than inventing a gate-less run out of a missing key.
    path = _portal(tmp_path, token="t1", pending=0)
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["inbound"]["current_event_replyable"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "dispatches your final message to the waking thread" in ctx
    assert "no gate owns this waking event" not in ctx


# ── The gate-less routing fact, all four cells (#728) ────────────────────
#
# ``gate_less`` used to be computed and then read only inside the silence
# arm, so the third delivery line below existed in one cell of a
# two-by-two table. These four pin the whole table on the axis the older
# tests above cannot speak to — they predate the line — while those older
# tests keep pinning the first two arms unchanged.
#
#                      | silent                    | delivered
#   -------------------|---------------------------|-------------------------
#   gate-less          | routing fact via the      | routing fact, its own
#                      | silence arm               | line  ← was the gap
#   gate-owned         | dispatch promise          | waking-thread nag
#
_ROUTING = "routing fact, stated once"
_DELIVERED = {"replies_current": 0, "replies_other": 0, "outbound_messages": 1}


def _stop(tmp_path, **kw):
    _portal(tmp_path, pending=0, **kw)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    return out["hookSpecificOutput"]["additionalContext"]


def _hook_state(tmp_path):
    return json.loads(
        (tmp_path / hooks.HOOK_STATE_NAME).read_text(encoding="utf-8")
    )


def test_stop_gate_less_and_delivered_states_the_routing_fact(tmp_path):
    # The cell #728 is about, and the one that actually loses content: a
    # schedule wake that pinged a gate mid-run and then wrote its real
    # closeout to stdout, where it stages ``status: undeliverable``. The old
    # code said nothing here — ``any_delivery`` was satisfied by the ping.
    ctx = _stop(
        tmp_path, token="t1", current_event_replyable=False,
        outbound=_DELIVERED,
    )
    assert _ROUTING in ctx
    assert "no gate owns this waking event" in ctx
    assert "the closeout is not exempt" in ctx
    # Prior delivery is named as irrelevant rather than treated as a clear:
    # that is the whole correction. A discriminator on "did a gate delivery
    # happen" would have gone quiet on exactly this run.
    assert "however much has already gone out" in ctx
    # And it must not resurrect either arm the older tests fence off here.
    assert "nothing communicated on any thread" not in ctx
    assert "waking thread itself has no reply yet" not in ctx


def test_stop_gate_less_and_silent_keeps_the_fact_in_the_silence_arm(tmp_path):
    # Same fact, already carried by the silence arm since #562. Restating it
    # on its own line in this cell would be the duplication the latch exists
    # to prevent, so the dedicated line stays out of the way.
    ctx = _stop(tmp_path, token="t1", current_event_replyable=False)
    assert "no gate owns this waking event" in ctx
    assert "body/message store only" in ctx
    assert _ROUTING not in ctx


def test_stop_gate_owned_and_delivered_states_no_routing_fact(tmp_path):
    # A gate owns this event: the terminal stream lands, so the fact is
    # false here and asserting it would be the guard lying.
    ctx = _stop(
        tmp_path, token="t1", current_event_replyable=True,
        outbound=_DELIVERED,
    )
    assert _ROUTING not in ctx
    assert "waking thread itself has no reply yet" in ctx


def test_stop_gate_owned_and_silent_states_no_routing_fact(tmp_path):
    ctx = _stop(tmp_path, token="t1", current_event_replyable=True)
    assert _ROUTING not in ctx
    assert "dispatches your final message to the waking thread" in ctx


def test_gate_less_routing_fact_is_stated_once_per_run(tmp_path):
    # The reason this is latched and not merely token-gated: ``outbound`` is
    # part of ``change_token``, so every gate delivery moves the token and
    # re-renders the closeout briefing. Unlatched, a fact that can never be
    # cleared would nag hardest at the runs delivering most — #562's exact
    # signature, which is what made that nag un-clearable and taught the
    # reader to skip the channel.
    first = _stop(
        tmp_path, token="t1", current_event_replyable=False,
        outbound=_DELIVERED,
    )
    assert _ROUTING in first
    second = _stop(
        tmp_path, token="t2", current_event_replyable=False,
        outbound={"replies_current": 0, "replies_other": 0,
                  "outbound_messages": 2},
    )
    # The briefing still renders — only the constant is spent.
    assert "delivery so far" in second
    assert _ROUTING not in second


def test_gate_less_silence_arm_primes_the_routing_latch(tmp_path):
    # A run that was silent at one closeout already read the fact off the
    # silence arm; delivering and stopping again must not tell it twice in
    # different words. One fact, one statement, whichever arm carried it.
    first = _stop(tmp_path, token="t1", current_event_replyable=False)
    assert "no gate owns this waking event" in first
    second = _stop(
        tmp_path, token="t2", current_event_replyable=False,
        outbound=_DELIVERED,
    )
    assert _ROUTING not in second


def test_gate_less_silence_warning_survives_the_routing_latch(tmp_path):
    # The latch spends the *constant*, never the clearable warning beside
    # it. Silence is a real obligation — deliver something and it goes away
    # on its own — so it must keep re-rendering at every closeout.
    _stop(tmp_path, token="t1", current_event_replyable=False)
    second = _stop(tmp_path, token="t2", current_event_replyable=False)
    assert "nothing communicated on any thread yet" in second


# ── #1142: the "no reply yet" line's consecutive-firing streak ──────────
#
# The Stop hook is a fresh subprocess every firing, so a run stuck re-firing
# Stop with no reply ever landing on its waking thread saw the exact same
# reassuring sentence every time — no way to tell "first firing, informative"
# from "fourth firing, a loop" without a persisted counter. These pin the
# counter (:data:`hooks.STOP_NO_REPLY_STREAK_KEY`) and the wording escalation
# past :data:`hooks._STOP_NO_REPLY_ESCALATE_THRESHOLD`.

_NO_REPLY_YET = "waking thread itself has no reply yet"
_VERDICT = "Probable delivery-path problem"


def test_stop_no_reply_streak_counts_and_escalates(tmp_path):
    # Firing #1: informative, state-description wording — same sentence the
    # pre-#1142 tests above already pin, now prefixed with the count.
    first = _stop(tmp_path, token="t1", outbound=_DELIVERED)
    assert "stop boundary #1 · 0 prior terminal messages consumed" in first
    assert _NO_REPLY_YET in first
    assert _VERDICT not in first

    # Firing #2: same waking event, still no reply — past the threshold, so
    # the wording turns into a verdict and points at the working alternative.
    second = _stop(
        tmp_path, token="t2", outbound={
            "replies_current": 0, "replies_other": 0, "outbound_messages": 2,
        },
    )
    assert "stop boundary #2 · 1 prior terminal message consumed" in second
    assert _VERDICT in second
    assert "gate: <name>" in second

    # Firing #3: the count keeps climbing, but the verdict itself does not
    # repeat — #1296's belt-and-suspenders latch (`no_reply_capped`,
    # `hooks.STOP_NO_REPLY_ESCALATED_KEY`). This line used to assert "wording
    # stays escalated" (unbounded repetition past the threshold); that was
    # exactly the bug #1296 measured live — a gate-less self-woken run
    # renagged 7 times in a row with the identical verdict, because nothing
    # here capped the *rendering* independently of the streak count still
    # climbing. The verdict is a one-time statement, not a ticker: firing #2
    # already said it, so firing #3 says nothing new on this line (the rest
    # of the closeout capsule — produce, notices, pending — still renders).
    third = _stop(
        tmp_path, token="t3", outbound={
            "replies_current": 0, "replies_other": 0, "outbound_messages": 3,
        },
    )
    assert "stop boundary #3" not in third
    assert _VERDICT not in third
    # The capsule as a whole is not empty — only the capped line is gone.
    assert "delivery so far" in third


def test_stop_no_reply_streak_resets_when_replied(tmp_path):
    # Build a streak past the threshold...
    _stop(tmp_path, token="t1", outbound=_DELIVERED)
    escalated = _stop(
        tmp_path, token="t2", outbound={
            "replies_current": 0, "replies_other": 0, "outbound_messages": 2,
        },
    )
    assert _VERDICT in escalated

    # ...then the waking thread finally gets its reply: the arm doesn't fire
    # at all (a different elif matches), which is itself the reset — the
    # streak's persisted count must not keep counting a resolved wait.
    replied = _stop(
        tmp_path, token="t3", outbound={
            "replies_current": 1, "replies_other": 0, "outbound_messages": 2,
        },
    )
    assert _NO_REPLY_YET not in replied
    assert _VERDICT not in replied

    # A fresh unanswered wait after that starts back at #1, not #3 — the old
    # streak must not bleed into an unrelated, later wait.
    fresh = _stop(
        tmp_path, token="t4", outbound={
            "replies_current": 0, "replies_other": 0, "outbound_messages": 3,
        },
    )
    assert "stop boundary #1 · 0 prior terminal messages consumed" in fresh
    assert _VERDICT not in fresh


def test_stop_no_reply_streak_resets_on_new_waking_event(tmp_path):
    # Two firings against evt-1 build the streak to #2 (escalated)...
    _stop(tmp_path, token="t1", current_event="evt-1", outbound=_DELIVERED)
    second = _stop(
        tmp_path, token="t2", current_event="evt-1", outbound={
            "replies_current": 0, "replies_other": 0, "outbound_messages": 2,
        },
    )
    assert "stop boundary #2" in second

    # ...a *different* waking event replacing it is a new wait, not a
    # continuation: the streak must restart at #1 even though the run as a
    # whole is still gate-owned and still hasn't replied to anything.
    third = _stop(
        tmp_path, token="t3", current_event="evt-2", outbound={
            "replies_current": 0, "replies_other": 0, "outbound_messages": 3,
        },
    )
    assert "stop boundary #1 · 0 prior terminal messages consumed" in third
    assert _VERDICT not in third


# ── #1296: the escalated verdict has a ceiling, independent of gate_less ─
#
# run-260810-0728-x41g, reconstructed from its own boundaries.jsonl: a
# schedule-thread continuation woken by a `spawn_completed` event, already
# delivered via `gate: telegram` twice this run (any_delivery=True), never
# accrued a reply on its own waking thread (replies_current=0). The daemon's
# `current_event_replyable` was wrong for that run shape (root cause: fixed
# in daemon.py, see `_spawn_parent_still_collecting`) and this same verdict
# line rendered 6 times in a row across Stop firings before the run ended.
# This test does not assume the root-cause fix — it drives the hooks-side
# ceiling directly, so it still passes even if some future bug makes
# `current_event_replyable` wrong again (the requirement's own "belt and
# suspenders" framing).

def test_stop_no_reply_escalation_has_a_ceiling_regardless_of_recurrence(tmp_path):
    outbound = {"replies_current": 0, "replies_other": 0, "outbound_messages": 2}
    first = _stop(tmp_path, token="t1", outbound=_DELIVERED)
    assert _VERDICT not in first  # firing #1: below threshold, reassuring wording

    second = _stop(tmp_path, token="t2", outbound=outbound)
    assert _VERDICT in second  # firing #2: threshold reached, verdict said once

    # Firings #3 through #7 (matching the live run's 6 total escalated
    # firings) must never repeat it — each token still changes (mirroring
    # the live transcript's growing notices/outbound counts), so this is
    # not merely the token-dedupe latch already covered elsewhere.
    for n in range(3, 8):
        rendered = _stop(
            tmp_path, token=f"t{n}",
            outbound={**outbound, "outbound_messages": n},
        )
        assert _VERDICT not in rendered, f"firing #{n} repeated the verdict"
        assert f"stop boundary #{n}" not in rendered

    state = _hook_state(tmp_path)
    assert state[hooks.STOP_NO_REPLY_ESCALATED_KEY] is True
    # The streak itself keeps counting truthfully — only the render is
    # capped, so a reader inspecting hook state directly still sees how long
    # this has actually been going on.
    assert state[hooks.STOP_NO_REPLY_STREAK_KEY] == 7


def test_content_dedupe_never_reaches_the_closeout_channel(tmp_path):
    # The guard for the defect above, stated as its own claim rather than as
    # a side effect of another test: a byte-identical closeout render is what
    # an unmet obligation looks like, so #963's content dedupe must not see
    # the Stop phase at all. Post-tool — the ambient bar #818 measured — is
    # the only channel it governs.
    _portal(tmp_path, token="t1", pending=0, current_event_replyable=False)
    env = _env(tmp_path)
    first, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", env)
    body = first["hookSpecificOutput"]["additionalContext"]
    assert "nothing communicated on any thread yet" in body

    # Same portal, new token, byte-identical render: it lands again.
    _portal(tmp_path, token="t2", pending=0, current_event_replyable=False)
    second, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", env)
    assert second["hookSpecificOutput"]["additionalContext"] == body


def test_routing_latch_is_not_burned_by_an_unrendered_stop(tmp_path):
    # Latched on the render, not the decision. A Stop whose token has not
    # moved injects nothing (#282), and spending the one statement on a
    # boundary the resident never saw turns once-per-run into never.
    (tmp_path / hooks.HOOK_STATE_NAME).write_text(
        json.dumps({"stop_last_token": "t1"}), encoding="utf-8",
    )
    _portal(
        tmp_path, token="t1", pending=0, current_event_replyable=False,
        outbound=_DELIVERED,
    )
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    # A bare result is the "nothing to add, stop cleanly" signal (#282) —
    # there is no additionalContext, so nothing was said and nothing spent.
    assert "hookSpecificOutput" not in out
    assert not _hook_state(tmp_path).get(hooks.GATELESS_ROUTING_KEY)
    later = _stop(
        tmp_path, token="t2", current_event_replyable=False,
        outbound=_DELIVERED,
    )
    assert _ROUTING in later


def test_routing_fact_absent_when_replyable_key_is_missing(tmp_path):
    # Absent is not False, here as in the arms above: a partial portal state
    # must not manufacture a gate-less run, and must not spend the latch.
    path = _portal(tmp_path, token="t1", pending=0, outbound=_DELIVERED)
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["inbound"]["current_event_replyable"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert _ROUTING not in ctx
    assert not _hook_state(tmp_path).get(hooks.GATELESS_ROUTING_KEY)


def test_routing_fact_absent_on_an_unaddressed_run(tmp_path):
    # No waking event at all: the delivery block does not render, so there
    # is no routing fact to state and nothing to latch.
    ctx = _stop(
        tmp_path, token="t1", current_event=None,
        current_event_replyable=False, outbound=_DELIVERED,
    )
    assert _ROUTING not in ctx
    assert not _hook_state(tmp_path).get(hooks.GATELESS_ROUTING_KEY)


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


def test_stop_does_not_nag_for_self_retiring_spawn_completion(tmp_path):
    """The Stop guard and closeout renderer must classify from one source.

    This is the live #990 path: the daemon reports its own completed child as
    observed and self-retiring in the injected closeout.  The same Stop fire
    must not then block on that event as though it were an unmet obligation.
    """
    run_id = "run-1"
    completion_id = "evt-spawn-done"
    _portal(tmp_path, token="t1", pending=1, events=[{
        "id": completion_id,
        "source": "spawn_completed",
        "spawn_parent_run_id": run_id,
        "spawned_by_run": "run-child",
        "spawn_status": "done",
        "summary": "concurrent spawn run-child finished: status=done",
        "body": "concurrent spawn run-child finished: status=done",
    }])

    out, code = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))

    assert code == 0
    assert out.get("decision") != "block"
    assert "0 pending event(s)" in out["hookSpecificOutput"]["additionalContext"]
    assert "1 finished spawn(s) observed" in (
        out["hookSpecificOutput"]["additionalContext"]
    )
    assert "no address needed; will retire at run end" in (
        out["hookSpecificOutput"]["additionalContext"]
    )


def test_stop_nag_selects_action_event_beside_finished_spawn(tmp_path):
    """A finished child cannot mask a real follow-up or become its nag."""
    _portal(tmp_path, token="t1", pending=2, events=[
        {
            "id": "evt-spawn-done",
            "source": "spawn_completed",
            "spawn_parent_run_id": "run-1",
            "spawn_status": "done",
            "body": "child completion is a self-retiring fact",
        },
        {
            "id": "evt-followup",
            "source": "telegram",
            "body": "please answer the actual follow-up",
        },
    ])

    out, code = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))

    assert code == 0
    assert out["decision"] == "block"
    assert "please answer the actual follow-up" in out["reason"]
    assert "child completion is a self-retiring fact" not in out["reason"]
    context = out["hookSpecificOutput"]["additionalContext"]
    assert "1 pending event(s)" in context
    assert "1 finished spawn(s) observed" in context


# ── Pending-event letter chrome ──────────────────────────────────────────
#
# The boundary-injected pending list, reworked after run-260731-1802-j6ke
# measured three live defects: truncation without accounting, verbatim
# refeed of huge bodies at every boundary, and a Stop fold-in that called a
# schedule firing "from the user".


def _aged_iso(seconds_ago: int) -> str:
    stamp = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(
        seconds=seconds_ago
    )
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


_SHORT_BODY = (
    "every word of a short message survives the boundary intact. " * 9
).strip() + " THE-LAST-WORD"

_HUGE_BODY = "## the only tick\n" + (
    "spec line about the tick cadence and its merged director duties\n" * 160
) + "DEEP-TAIL-MARKER"


def test_pending_short_body_renders_inline_with_letter_chrome(tmp_path):
    # Defect 1 was a 600-byte maintainer message cut mid-sentence at ~200
    # chars with no sender, age, or size. A body under the inline ceiling now
    # renders whole, under one chrome row naming all three.
    assert len(_SHORT_BODY.encode()) <= hooks._EVENT_INLINE_BODY_MAX
    _portal(tmp_path, token="t1", pending=1, events=[{
        "id": "evt-1785520000000000000-8jwi", "source": "telegram",
        "summary": "short", "body": _SHORT_BODY,
        "created": _aged_iso(180),
        "telegram_user": "Arseni", "telegram_username": "lapunov",
    }])
    out, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert (
        f"- ✉ evt-1785520000000000000-8jwi · telegram · Arseni (@lapunov) · 3m · "
        f"{len(_SHORT_BODY.encode())} B" in ctx
    )
    # The whole body, never cut — the last word is the proof.
    assert "THE-LAST-WORD" in ctx


def test_pending_event_line_carries_the_full_id_verbatim(tmp_path):
    # #934: an `event:`-addressed reply needs the id verbatim, so the one
    # surface announcing the event must be copy-able — the full nanosecond-
    # stamped id, never the `evt-…8jwi` stub a resident then reconstructs
    # (wrong) from a neighbour's timestamp.
    full_id = "evt-1785589021094471945-i10p"
    _portal(tmp_path, token="t1", pending=1, events=[{
        "id": full_id, "source": "cloud", "body": "a copy-able coordinate",
    }])
    out, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert f"- ✉ {full_id} · cloud" in ctx
    assert "evt-…" not in ctx


def test_pending_long_body_renders_first_line_plus_accounting(tmp_path):
    # Defect 2 was a ~10 KB schedule spec refed whole at every boundary.
    # Over the ceiling: first line + an explicit size + where the full body
    # lives — elision that accounts for itself.
    _portal(tmp_path, token="t1", pending=1, events=[{
        "id": "evt-1785520000000000001-tick", "source": "schedule",
        "summary": "tick", "body": _HUGE_BODY,
        "schedule_id": "the-only-tick",
    }])
    out, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "⏰ evt-1785520000000000001-tick · schedule · the-only-tick" in ctx
    assert "## the only tick" in ctx
    assert "KB total · full body: " in ctx
    assert str(tmp_path / "inbox.json") in ctx
    assert "DEEP-TAIL-MARKER" not in ctx


def test_seen_suppression_collapses_repeat_boundaries(tmp_path):
    # Hooks are fresh subprocesses; the seen ledger persists in the run's
    # hook state. First appearance renders in full, each later boundary with
    # the unchanged body costs one honest line, counting how often.
    ev = {
        "id": "evt-1785520000000000001-tick", "source": "schedule",
        "summary": "tick", "body": _HUGE_BODY,
    }
    env = _env(tmp_path)
    _portal(tmp_path, token="t1", pending=1, events=[ev])
    first, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    assert "KB total" in first["hookSpecificOutput"]["additionalContext"]

    _portal(tmp_path, token="t2", pending=1, events=[ev])
    second, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    ctx2 = second["hookSpecificOutput"]["additionalContext"]
    assert (
        "- ⏰ evt-1785520000000000001-tick · schedule · seen ×1 · unchanged"
        in ctx2
    )
    assert "## the only tick" not in ctx2

    _portal(tmp_path, token="t3", pending=1, events=[ev])
    third, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    assert (
        "seen ×2 · unchanged" in third["hookSpecificOutput"]["additionalContext"]
    )


def test_changed_body_rerenders_in_full_with_a_delta_mark(tmp_path):
    # Suppression is keyed on the body's digest, not the id — an event whose
    # body moved re-renders in full under an explicit Δ mark.
    env = _env(tmp_path)
    _portal(tmp_path, token="t1", pending=1, events=[{
        "id": "evt-2", "source": "telegram", "body": "first wording",
    }])
    hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)

    _portal(tmp_path, token="t2", pending=1, events=[{
        "id": "evt-2", "source": "telegram", "body": "second wording, edited",
    }])
    out, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "Δ changed" in ctx
    assert "second wording, edited" in ctx
    assert "unchanged" not in ctx


def test_stop_fold_in_of_a_schedule_firing_is_labelled_honestly(tmp_path):
    # Defect 3: a schedule firing is the entry's own spec, not the user
    # speaking — the fold-in label must not claim otherwise. The letter
    # policy applies here too: a huge spec folds in as first line +
    # accounting, not a refeed.
    _portal(tmp_path, token="t1", pending=1, events=[{
        "id": "evt-1785520000000000001-tick", "source": "schedule",
        "summary": "tick", "body": _HUGE_BODY,
    }])
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    assert out["decision"] == "block"
    reason = out["reason"]
    assert "schedule firing folded in" in reason
    assert "not a user message" in reason
    assert "from the user" not in reason
    assert "KB total · full body: " in reason
    assert "DEEP-TAIL-MARKER" not in reason


def test_stop_fold_in_of_an_already_shown_body_is_one_line(tmp_path):
    # A post-tool boundary already showed the body in full; the Stop fold-in
    # of the same unchanged body renders the one-line seen form, not a
    # second copy.
    ev = {"id": "evt-2", "source": "telegram", "body": "please rename the widget"}
    env = _env(tmp_path)
    _portal(tmp_path, token="t1", pending=1, events=[ev])
    first, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    assert "please rename the widget" in first["hookSpecificOutput"]["additionalContext"]

    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", env)
    assert out["decision"] == "block"
    assert "✉ evt-2 · telegram · seen ×1 · unchanged" in out["reason"]
    assert "please rename the widget" not in out["reason"]


def test_source_glyphs_cover_the_channel_vocabulary():
    assert hooks._event_glyph("telegram") == "✉"
    assert hooks._event_glyph("schedule") == "⏰"
    assert hooks._event_glyph("github") == "⎇"
    assert hooks._event_glyph("forge") == "⎇"
    assert hooks._event_glyph("spawn_message") == "⚙"
    assert hooks._event_glyph("worker") == "⚙"
    assert hooks._event_glyph("carrier-pigeon") == "✉"


def test_every_pending_event_always_gets_at_least_one_line(tmp_path):
    # Never lossy about what exists: even a bodyless event renders its
    # chrome row and an explicit "(no body)" — silence is not an option.
    _portal(tmp_path, token="t1", pending=1, events=[{
        "id": "evt-9", "source": "telegram",
    }])
    out, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "- ✉ evt-9 · telegram · 0 B" in ctx
    assert "(no body)" in ctx


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


def test_removed_gemini_flavour_uses_custom_neutral_envelope(tmp_path):
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    out, code = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path, "gemini"))
    assert out["block"] is True
    assert out["block_reason"]
    assert code == 0


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


def test_missing_portal_state_post_tool_marks_pending_count_unknown(tmp_path):
    out, code = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))

    assert code == 0
    assert (tmp_path / hooks.FLUSH_SIGNAL_NAME).exists()
    rendered = out["hookSpecificOutput"]["additionalContext"]
    assert "✉?" in rendered
    assert "0 pending event(s)" not in rendered


def test_malformed_portal_state_post_tool_marks_pending_count_unknown(tmp_path):
    (tmp_path / "portal-state.json").write_text("{not json", encoding="utf-8")

    out, code = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))

    assert code == 0
    rendered = out["hookSpecificOutput"]["additionalContext"]
    assert "✉?" in rendered
    assert "0 pending event(s)" not in rendered


def test_missing_portal_state_seed_reports_unknown_pending_count(tmp_path):
    out, code = hooks.run_hook(
        hooks.PHASE_SESSION_START, "{}", _env(tmp_path),
    )

    assert code == 0
    rendered = out["hookSpecificOutput"]["additionalContext"]
    assert "could not count pending event(s)" in rendered
    assert "0 pending event(s)" not in rendered


def test_malformed_portal_state_closeout_reports_unknown_pending_count(tmp_path):
    (tmp_path / "portal-state.json").write_text("{not json", encoding="utf-8")

    out, code = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))

    assert code == 0
    rendered = out["hookSpecificOutput"]["additionalContext"]
    assert "could not count pending event(s)" in rendered
    assert "0 pending event(s)" not in rendered


def test_genuine_zero_pending_count_keeps_affirmative_all_clear(tmp_path):
    _portal(tmp_path, pending=0)

    out, code = hooks.run_hook(
        hooks.PHASE_SESSION_START, "{}", _env(tmp_path),
    )

    assert code == 0
    rendered = out["hookSpecificOutput"]["additionalContext"]
    assert "0 pending event(s)" in rendered
    assert "could not count" not in rendered


# ── Config generation (brr-managed per-run native hook config) ───────────


def test_hook_config_supported_only_claude_today():
    # ``hook_config_supported`` is the *settings-file* install gate. Codex is
    # hooks-capable but installs via argv (codex_hook_args), so it is excluded
    # here.
    assert hooks.hook_config_supported("claude") is True
    assert hooks.hook_config_supported("codex") is False
    assert hooks.hook_config_supported(None) is False
    assert hooks.hook_config_supported("") is False


def test_install_hook_config_writes_wellformed_claude_settings(tmp_path):
    path = hooks.install_hook_config("claude", tmp_path)
    assert path == tmp_path / ".claude" / "settings.local.json"
    settings = json.loads(path.read_text(encoding="utf-8"))
    hook_block = settings["hooks"]
    # All four abstract phases map to their native claude event names, each
    # invoking ``brnrd hook <phase>`` — the keystone the wiring relies on.
    assert set(hook_block) == {"PostToolBatch", "Stop", "SessionStart", "PreToolUse"}
    cmds = {
        name: entries[0]["hooks"][0]["command"]
        for name, entries in hook_block.items()
    }
    assert cmds["PostToolBatch"] == "brnrd hook post-tool"
    assert cmds["Stop"] == "brnrd hook stop"
    assert cmds["SessionStart"] == "brnrd hook session-start"
    assert cmds["PreToolUse"] == "brnrd hook pre-tool"
    # #1184: unlike the other three (unconditional), the rooted-write guard
    # is matcher-scoped to the two tools that take a raw file path — every
    # other tool call never reaches ``brnrd hook pre-tool`` at all.
    assert hook_block["PreToolUse"][0]["matcher"] == "Edit|Write"
    for name in ("PostToolBatch", "Stop", "SessionStart"):
        assert "matcher" not in hook_block[name][0]
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
    # #1184: PreToolUse is the one event brr merges additively rather than
    # replacing outright (the other three are brr's alone) — the user's own
    # entry survives verbatim, first, with brr's rooted-write guard appended
    # after it, so both fire rather than one silently losing the other.
    pre_tool = settings["hooks"]["PreToolUse"]
    assert pre_tool[0] == {"hooks": []}
    assert pre_tool[-1]["hooks"][0]["command"] == "brnrd hook pre-tool"
    assert pre_tool[-1]["matcher"] == "Edit|Write"


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


# ── The rooted-write guard (#1184) ────────────────────────────────────────
#
# `daemon._child_git_pin` (#703) pins a strand's *git* to its own worktree,
# but `Edit`/`Write` never touch git — they take a raw absolute path. This
# `pre-tool` phase closes that gap: refuse a write path rooted in the host
# checkout (``BRR_HOST_ROOT``) but outside the strand's own worktree
# (``BRR_WORK_TREE``).
#
# Deliberately not ``GIT_WORK_TREE`` even though the git pin exports exactly
# that name into the same env dict: every ``brnrd hook <phase>`` invocation
# runs through ``cli.main()``, whose first act (``_drop_inherited_git_pin``)
# pops ``GIT_DIR``/``GIT_WORK_TREE`` from ``os.environ`` before anything else
# runs, so the raw variable never reaches this subprocess in production —
# only the ``BRR_``-namespaced copy the daemon arms alongside it survives.
# Found live: an early cut of this guard read ``GIT_WORK_TREE`` directly and
# passed every unit test here (which hand-build their env dict and so never
# exercise the scrub) while never actually arming in a real `brnrd hook
# pre-tool` subprocess.


def _rooted_env(tmp_path, *, host_root, work_tree, flavour="claude"):
    env = _env(tmp_path, flavour)
    env["BRR_HOST_ROOT"] = str(host_root)
    env["BRR_WORK_TREE"] = str(work_tree)
    return env


def _pre_tool_stdin(tool_name, file_path, **extra):
    payload = {"tool_name": tool_name, "tool_input": {"file_path": file_path, **extra}}
    return json.dumps(payload)


def _rooted_layout(tmp_path):
    host = tmp_path / "host"
    work_tree = host / ".brr" / "worktrees" / "run-1"
    work_tree.mkdir(parents=True)
    return host, work_tree


def test_pre_tool_is_a_noop_when_unarmed(tmp_path):
    # No BRR_HOST_ROOT / BRR_WORK_TREE at all — a resident, a `host` run, or
    # a strand whose git pin found no readable git dir (`_child_git_pin`'s
    # own degrade). Nothing to compare a write path against is not evidence
    # of anything, so this never blocks.
    out, code = hooks.run_hook(
        hooks.PHASE_PRE_TOOL,
        _pre_tool_stdin("Write", "/host/checkout/daemon.py"),
        _env(tmp_path),
    )
    assert code == 0
    assert out == {}


def test_pre_tool_ignores_the_raw_git_work_tree_env_var(tmp_path):
    # #1184 regression: `cli.main()`'s own `_drop_inherited_git_pin` strips
    # `GIT_DIR`/`GIT_WORK_TREE` from `os.environ` before any `brnrd hook
    # <phase>` subprocess runs, so a real `pre-tool` fire never carries the
    # raw variable — only `BRR_WORK_TREE` survives that scrub. A guard keyed
    # on `GIT_WORK_TREE` would pass every other test in this file (they hand-
    # build the env dict and so never exercise the scrub) while staying
    # permanently unarmed in production. `BRR_HOST_ROOT` alone must not be
    # enough to arm it.
    host, work_tree = _rooted_layout(tmp_path)
    env = _env(tmp_path)
    env["BRR_HOST_ROOT"] = str(host)
    env["GIT_WORK_TREE"] = str(work_tree)  # the raw name — must be ignored
    out, code = hooks.run_hook(
        hooks.PHASE_PRE_TOOL,
        _pre_tool_stdin("Write", str(host / "daemon.py")),
        env,
    )
    assert code == 0
    assert out == {}


def test_pre_tool_refuses_a_write_rooted_in_host_outside_worktree(tmp_path):
    host, work_tree = _rooted_layout(tmp_path)
    env = _rooted_env(tmp_path, host_root=host, work_tree=work_tree)
    out, code = hooks.run_hook(
        hooks.PHASE_PRE_TOOL,
        _pre_tool_stdin("Write", str(host / "daemon.py")),
        env,
    )
    assert code == 0
    deny = out["hookSpecificOutput"]
    assert deny["hookEventName"] == "PreToolUse"
    assert deny["permissionDecision"] == "deny"
    assert "#1184" in deny["permissionDecisionReason"]
    assert str(host) in deny["permissionDecisionReason"]
    assert str(work_tree) in deny["permissionDecisionReason"]


def test_pre_tool_refuses_edit_the_same_as_write(tmp_path):
    host, work_tree = _rooted_layout(tmp_path)
    env = _rooted_env(tmp_path, host_root=host, work_tree=work_tree)
    out, code = hooks.run_hook(
        hooks.PHASE_PRE_TOOL,
        _pre_tool_stdin("Edit", str(host / "daemon.py")),
        env,
    )
    assert code == 0
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_pre_tool_allows_a_write_rooted_in_the_worktree(tmp_path):
    host, work_tree = _rooted_layout(tmp_path)
    env = _rooted_env(tmp_path, host_root=host, work_tree=work_tree)
    out, code = hooks.run_hook(
        hooks.PHASE_PRE_TOOL,
        _pre_tool_stdin("Write", str(work_tree / "daemon.py")),
        env,
    )
    assert code == 0
    assert out == {}


def test_pre_tool_allows_a_write_to_the_worktree_root_itself(tmp_path):
    # The boundary case: the path *is* GIT_WORK_TREE, not merely under it.
    host, work_tree = _rooted_layout(tmp_path)
    env = _rooted_env(tmp_path, host_root=host, work_tree=work_tree)
    out, code = hooks.run_hook(
        hooks.PHASE_PRE_TOOL,
        _pre_tool_stdin("Write", str(work_tree)),
        env,
    )
    assert code == 0
    assert out == {}


def test_pre_tool_ignores_tools_other_than_edit_write(tmp_path):
    host, work_tree = _rooted_layout(tmp_path)
    env = _rooted_env(tmp_path, host_root=host, work_tree=work_tree)
    out, code = hooks.run_hook(
        hooks.PHASE_PRE_TOOL,
        _pre_tool_stdin("Read", str(host / "daemon.py")),
        env,
    )
    assert code == 0
    assert out == {}


def test_pre_tool_ignores_a_relative_path(tmp_path):
    # Rootedness is the discriminator, but only an absolute path can be
    # rooted anywhere in particular — a relative path resolves against the
    # runner's own (pinned) cwd, which this predicate cannot second-guess.
    host, work_tree = _rooted_layout(tmp_path)
    env = _rooted_env(tmp_path, host_root=host, work_tree=work_tree)
    out, code = hooks.run_hook(
        hooks.PHASE_PRE_TOOL,
        _pre_tool_stdin("Write", "daemon.py"),
        env,
    )
    assert code == 0
    assert out == {}


def test_pre_tool_ignores_a_path_outside_the_host_checkout_entirely(tmp_path):
    # The documented escape hatch (a strand driving a scratch tree via
    # `env -u GIT_DIR -u GIT_WORK_TREE`) is not this predicate's concern.
    host, work_tree = _rooted_layout(tmp_path)
    env = _rooted_env(tmp_path, host_root=host, work_tree=work_tree)
    scratch = tmp_path / "other-repo" / "file.py"
    out, code = hooks.run_hook(
        hooks.PHASE_PRE_TOOL,
        _pre_tool_stdin("Write", str(scratch)),
        env,
    )
    assert code == 0
    assert out == {}


def test_pre_tool_block_is_silent_for_non_claude_flavours(tmp_path):
    # Codex does not install this phase today — `codex_hook_args` covers
    # only PostToolUse/Stop/SessionStart, the three its own hooks docs
    # verify. Should a `pre-tool` fire reach here anyway, rendering must not
    # assert an unverified block shape for it.
    host, work_tree = _rooted_layout(tmp_path)
    env = _rooted_env(tmp_path, host_root=host, work_tree=work_tree, flavour="codex")
    out, code = hooks.run_hook(
        hooks.PHASE_PRE_TOOL,
        _pre_tool_stdin("Write", str(host / "daemon.py")),
        env,
    )
    assert code == 0
    assert out == {}


def test_pre_tool_never_touches_the_portal_or_hook_state(tmp_path):
    # A filesystem-safety predicate, not a correspondence one (contrast
    # `subagent_neutral`, #1095): no portal read, no `.hook-state.json`
    # write. Malformed/absent portal state must not change the verdict.
    host, work_tree = _rooted_layout(tmp_path)
    env = _rooted_env(tmp_path, host_root=host, work_tree=work_tree)
    out, code = hooks.run_hook(
        hooks.PHASE_PRE_TOOL,
        _pre_tool_stdin("Write", str(host / "daemon.py")),
        env,
    )
    assert code == 0
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert not (tmp_path / hooks.HOOK_STATE_NAME).exists()


def test_pre_tool_allows_write_to_card_in_outbox_dir(tmp_path):
    # A strand's .card control file lives in the shared outbox dir
    # ($BRR_OUTBOX_DIR), not in the worktree. This is by design — the bundle
    # names it explicitly as where the run's card lives (daemon-substrate.md
    # → control files table). The write should be allowed despite the path
    # being outside the worktree. The outbox dir is rooted in the host
    # checkout but outside the strand's worktree.
    host, work_tree = _rooted_layout(tmp_path)
    outbox = host / ".brr" / "outbox" / "run-1"
    outbox.mkdir(parents=True)
    env = _rooted_env(tmp_path, host_root=host, work_tree=work_tree)
    env["BRR_OUTBOX_DIR"] = str(outbox)
    out, code = hooks.run_hook(
        hooks.PHASE_PRE_TOOL,
        _pre_tool_stdin("Write", str(outbox / ".card")),
        env,
    )
    assert code == 0
    assert out == {}


def test_pre_tool_allows_write_to_control_files_in_outbox_dir(tmp_path):
    # All control files (.card, .mood, .keepalive, .name) are allowed.
    host, work_tree = _rooted_layout(tmp_path)
    outbox = host / ".brr" / "outbox" / "run-1"
    outbox.mkdir(parents=True)
    env = _rooted_env(tmp_path, host_root=host, work_tree=work_tree)
    env["BRR_OUTBOX_DIR"] = str(outbox)

    for control_file in [".mood", ".keepalive", ".name"]:
        out, code = hooks.run_hook(
            hooks.PHASE_PRE_TOOL,
            _pre_tool_stdin("Write", str(outbox / control_file)),
            env,
        )
        assert code == 0, f"Failed for {control_file}"
        assert out == {}, f"Failed for {control_file}"


def test_pre_tool_blocks_other_files_in_outbox_dir(tmp_path):
    # The carve-out is specific to the four named control files. A write to
    # some other file inside $BRR_OUTBOX_DIR (not one of .card/.mood/
    # .keepalive/.name) that also happens to be outside the worktree should
    # still be blocked — the carve-out must not become "anything in the
    # outbox dir is exempt."
    host, work_tree = _rooted_layout(tmp_path)
    outbox = host / ".brr" / "outbox" / "run-1"
    outbox.mkdir(parents=True)
    env = _rooted_env(tmp_path, host_root=host, work_tree=work_tree)
    env["BRR_OUTBOX_DIR"] = str(outbox)
    out, code = hooks.run_hook(
        hooks.PHASE_PRE_TOOL,
        _pre_tool_stdin("Write", str(outbox / "other-file.json")),
        env,
    )
    assert code == 0
    deny = out["hookSpecificOutput"]
    assert deny["permissionDecision"] == "deny"
    assert "#1184" in deny["permissionDecisionReason"]


def test_pre_tool_allows_edit_to_control_files_same_as_write(tmp_path):
    # The carve-out applies to both Edit and Write tools.
    host, work_tree = _rooted_layout(tmp_path)
    outbox = host / ".brr" / "outbox" / "run-1"
    outbox.mkdir(parents=True)
    env = _rooted_env(tmp_path, host_root=host, work_tree=work_tree)
    env["BRR_OUTBOX_DIR"] = str(outbox)
    out, code = hooks.run_hook(
        hooks.PHASE_PRE_TOOL,
        _pre_tool_stdin("Edit", str(outbox / ".card")),
        env,
    )
    assert code == 0
    assert out == {}


def test_pre_tool_allows_write_to_pr_and_topics_in_outbox_dir(tmp_path):
    # #1318 regression: `.pr` (`relics._read_pr_control` / the bolt's
    # `remote_scm` facet) and `.topics` (`run_ledger.read_run_topics_control`)
    # are real, documented control files whose only reader is the outbox
    # dir — same shape as `.mood`/`.keepalive`/`.name` in the test above,
    # just added later and missed by the old hand-listed
    # `_CONTROL_FILES`. Before the fix, a strand writing either one here
    # was refused and told to rewrite under `$GIT_WORK_TREE`, where
    # nothing reads it — the PR-relic path a bolt's `produce: attested`
    # depends on stayed empty even after `gh pr create` succeeded.
    host, work_tree = _rooted_layout(tmp_path)
    outbox = host / ".brr" / "outbox" / "run-1"
    outbox.mkdir(parents=True)
    env = _rooted_env(tmp_path, host_root=host, work_tree=work_tree)
    env["BRR_OUTBOX_DIR"] = str(outbox)

    for control_file in [".pr", ".topics"]:
        out, code = hooks.run_hook(
            hooks.PHASE_PRE_TOOL,
            _pre_tool_stdin("Write", str(outbox / control_file)),
            env,
        )
        assert code == 0, f"Failed for {control_file}"
        assert out == {}, f"Failed for {control_file}"


def test_pre_tool_control_file_refusal_does_not_recommend_git_work_tree(tmp_path):
    # #1318: when the refused path's filename matches a known control file
    # (here `.pr`, written straight at the host root rather than under the
    # outbox dir — still refused, since the carve-out only fires inside
    # $BRR_OUTBOX_DIR) the remedy must not tell the strand to rewrite under
    # `$GIT_WORK_TREE` — that is the wrong destination for a file every
    # reader (`relics._read_pr_control`) only ever reads from the outbox
    # dir. The message should name the outbox dir instead.
    host, work_tree = _rooted_layout(tmp_path)
    outbox = host / ".brr" / "outbox" / "run-1"
    outbox.mkdir(parents=True)
    env = _rooted_env(tmp_path, host_root=host, work_tree=work_tree)
    env["BRR_OUTBOX_DIR"] = str(outbox)
    out, code = hooks.run_hook(
        hooks.PHASE_PRE_TOOL,
        _pre_tool_stdin("Write", str(host / ".pr")),
        env,
    )
    assert code == 0
    deny = out["hookSpecificOutput"]
    assert deny["permissionDecision"] == "deny"
    reason = deny["permissionDecisionReason"]
    assert "#1184" in reason
    # The old message unconditionally said "Rewrite the path relative to
    # $GIT_WORK_TREE" — that recommendation must be gone here, even though
    # the reworded message still *names* $GIT_WORK_TREE to say it's the
    # wrong place.
    assert "Rewrite the path relative to $GIT_WORK_TREE" not in reason
    assert str(outbox) in reason


def test_pre_tool_non_control_file_refusal_still_recommends_git_work_tree(tmp_path):
    # The reworded remedy is conditional on the filename, not a blanket
    # rewrite of the message — an ordinary source file rooted in the host
    # checkout keeps the original `$GIT_WORK_TREE` advice, which is correct
    # for it.
    host, work_tree = _rooted_layout(tmp_path)
    env = _rooted_env(tmp_path, host_root=host, work_tree=work_tree)
    out, code = hooks.run_hook(
        hooks.PHASE_PRE_TOOL,
        _pre_tool_stdin("Write", str(host / "daemon.py")),
        env,
    )
    assert code == 0
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "$GIT_WORK_TREE" in reason


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


def test_guard_never_loops_on_the_same_reply(tmp_path):
    """#282 is the standing scar: a hook that re-fires into a run with nothing
    left to do burns the budget. The reply that was blocked, presented again,
    is exactly that loop — silent."""
    _portal(tmp_path, token="t1", pending=0)
    env = _armed(tmp_path)
    first, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin("no closeout"), env)
    assert first["decision"] == "block"
    second, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin("no closeout"), env)
    assert second.get("decision") != "block"


def test_guard_blocks_a_second_distinct_bad_closeout(tmp_path):
    """#981, and the whole point of the latch move. `run-260802-0001-9qgz`
    was blocked for a false `continuing` at 00:21:45Z, worked thirty more
    minutes across fourteen more Stop boundaries, and ended at 00:51:37Z on
    a *different* false `continuing` — unblocked, because one bit had been
    spent half an hour earlier on a claim it had already fixed."""
    _portal(tmp_path, token="t1", pending=0)
    env = _armed(tmp_path)
    first, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin("no closeout"), env)
    assert first["decision"] == "block"
    second, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin("still no closeout"), env)
    assert second["decision"] == "block", "a new reply is a new claim, not a re-nag"


def test_guard_stops_arguing_after_the_cap(tmp_path):
    """The bound #282 actually needs. Distinct replies re-arm the guard, so
    without a cap a resident that cannot produce a closeout would be blocked
    forever; three is where the exchange ends whatever it says next."""
    _portal(tmp_path, token="t1", pending=0)
    env = _armed(tmp_path)
    for attempt in range(hooks._CLOSEOUT_BLOCK_CAP):
        out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(f"no closeout {attempt}"), env)
        assert out["decision"] == "block", attempt
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin("still no closeout"), env)
    assert out.get("decision") != "block", "the cap ends it, however new the reply"


def test_guard_keeps_one_block_when_the_shell_hands_over_no_reply(tmp_path):
    """A Shell that hands over no `last_assistant_message` leaves "the claim
    changed" unassertable, and the doctrine here is silence over a guess: the
    key never varies, so such a run keeps exactly the old single-block
    behaviour rather than re-firing on an empty reply forever."""
    (tmp_path / hooks.CARD_NAME).unlink(missing_ok=True)
    _portal(tmp_path, token="t1", pending=0)
    env = _armed_obl(tmp_path, obligations="card")
    first, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(), env)
    assert first["decision"] == "block"
    second, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(), env)
    assert second.get("decision") != "block"


def test_guard_respects_the_shells_own_loop_breaker(tmp_path):
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(
        hooks.PHASE_STOP,
        _stdin("no closeout", stop_hook_active=True),
        _armed(tmp_path),
    )
    assert out.get("decision") != "block"


# ── Escalated artifact obligations (card) ─────────────────────────────────

_GOOD_REPLY = "wired it up.\n\n**done** — committed abc1234 on brr/x"


def _armed_obl(tmp_path, obligations="card", flavour="claude"):
    env = _armed(tmp_path, flavour)
    env["BRR_CLOSEOUT_OBLIGATIONS"] = obligations
    return env


def test_guard_blocks_when_card_missing(tmp_path):
    """A closeout with a clean reply but no `.card` still blocks — the card is
    the surface the user watched the whole run."""
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY), _armed_obl(tmp_path))
    assert out["decision"] == "block"
    assert ".card" in out["reason"]


def test_guard_blank_artifact_counts_as_unwritten(tmp_path):
    """An empty / whitespace-only control file is not a written obligation."""
    _portal(tmp_path, token="t1", pending=0)
    (tmp_path / hooks.CARD_NAME).write_text("   \n", encoding="utf-8")
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY), _armed_obl(tmp_path))
    assert out["decision"] == "block"
    assert ".card" in out["reason"]


def test_guard_capsule_lists_every_unmet_at_once(tmp_path):
    """Reply ends on nothing AND the card missing → one capsule naming both,
    not two chained Stop blocks (#282 loop safety)."""
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin("just prose"), _armed_obl(tmp_path))
    assert out["decision"] == "block"
    reason = out["reason"]
    assert "ends on nothing" in reason
    assert ".card" in reason


def test_guard_passes_when_every_obligation_met(tmp_path):
    _portal(tmp_path, token="t1", pending=0)
    (tmp_path / hooks.CARD_NAME).write_text("progress", encoding="utf-8")
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


def test_scm_blocks_missing_forge_handoff_when_pushed(tmp_path):
    """Pushed commits with neither PR nor broker receipt are provably unhanded."""
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
    assert out["decision"] == "block"
    assert "no PR or accepted `gate: forge` handoff" in out["reason"]


def test_scm_accepts_durable_forge_handoff_before_pr_exists(tmp_path):
    """The broker receipt proves a final gate handoff without claiming PR creation."""
    repo = _seeded_repo(tmp_path)
    bare = tmp_path / "remote.git"
    _git(repo, "init", "-q", "--bare", str(bare))
    _git(repo, "switch", "-qc", "brr/work")
    (repo / "feature.py").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "feature")
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "-u", "origin", "brr/work")
    (tmp_path / hooks.FORGE_HANDOFF_NAME).write_text(
        "event: evt-forge\nhead: brr/work\n", encoding="utf-8",
    )
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


def test_scm_names_forge_gate_route_only_when_armed(tmp_path):
    """#568 defect 2: the blocking clause may only point at `gate: forge`
    when the daemon told this hook the gate is actually deliverable here
    (`BRR_FORGE_GATE`, read into `HookContext.forge_gate`). A guard may
    not name a route it hasn't been told exists."""
    repo = _seeded_repo(tmp_path)
    (repo / "wip.txt").write_text("half-done\n", encoding="utf-8")
    _portal(tmp_path, token="t1", pending=0)

    env = _armed_scm(tmp_path, repo)
    env["BRR_FORGE_GATE"] = "1"
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY), env)
    assert out["decision"] == "block"
    assert "gate: forge" in out["reason"]
    assert "open the PR yourself" not in out["reason"]


def test_scm_omits_forge_gate_route_when_unarmed(tmp_path):
    """Flag absent (an unconfigured account, or an older daemon that never
    set it) ⇒ treated as off: the clause falls back to a route that
    doesn't presuppose a gate this account may not have."""
    repo = _seeded_repo(tmp_path)
    (repo / "wip.txt").write_text("half-done\n", encoding="utf-8")
    _portal(tmp_path, token="t1", pending=0)

    env = _armed_scm(tmp_path, repo)
    assert "BRR_FORGE_GATE" not in env
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY), env)
    assert out["decision"] == "block"
    assert "open the PR yourself" in out["reason"]
    assert "gate: forge" not in out["reason"]


# ── The `gate` closeout obligation (ran-the-project's-own-CI block) ───────


def _gate_receipt(tmp_path, repo, **overrides):
    """Write the receipt `scripts/gate.py` would write for `repo`'s current
    tree, into `repo`'s own slot of the outbox's receipts map (#820) — the
    same map `hooks._gate_closeout_clause` reads via
    `gate_receipt.read_receipt(outbox_dir, ctx.repo_dir)`."""
    from brr import gate_receipt

    def out(*args):
        return subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
        ).stdout

    payload = {
        "head": out("rev-parse", "HEAD").strip(),
        "status": out("status", "--porcelain"),
        "diff_digest": hashlib.sha256(
            out("diff", "HEAD").encode("utf-8", "replace")
        ).hexdigest(),
        # Agreement between this and `scripts/gate.py`'s own writer is pinned
        # in `test_gate_runner.py`, against a real repo — not assumed here.
        "untracked_digest": hooks._untracked_digest(repo),
        "verdict": "GREEN",
        "legs": [],
    }
    payload.update(overrides)
    receipt_path = tmp_path / hooks.GATE_RECEIPT_NAME
    data = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.exists() else {}
    data[gate_receipt.tree_key(repo)] = payload
    receipt_path.write_text(json.dumps(data), encoding="utf-8")
    return payload


def _armed_gate(tmp_path, repo, command="python scripts/gate.py"):
    """Arm only the `gate` obligation, card already satisfied, so a block can
    come from nowhere else."""
    (tmp_path / hooks.CARD_NAME).write_text("progress", encoding="utf-8")
    env = _armed_obl(tmp_path, obligations="gate")
    env["BRR_REPO_DIR"] = str(repo)
    env["BRR_SEED_REF"] = "main"
    env["BRR_GATE_COMMAND"] = command
    return env


def test_gate_blocks_when_the_tree_changed_and_no_receipt_exists(tmp_path):
    """The whole point: code changed, the gate never ran, the run is about to
    claim it is done. `pytest` alone is one leg of four."""
    repo = _seeded_repo(tmp_path)
    (repo / "feature.py").write_text("x\n", encoding="utf-8")
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY),
                            _armed_gate(tmp_path, repo))
    assert out["decision"] == "block"
    assert "the gate never ran" in out["reason"]
    # It names the repo's own command, never one brr invented.
    assert "python scripts/gate.py" in out["reason"]


def test_gate_silent_when_the_receipt_matches_the_tree(tmp_path):
    """Ran, on this tree ⇒ nothing owed. The receipt is the whole interface."""
    repo = _seeded_repo(tmp_path)
    (repo / "feature.py").write_text("x\n", encoding="utf-8")
    _gate_receipt(tmp_path, repo)
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY),
                            _armed_gate(tmp_path, repo))
    assert out.get("decision") != "block"


def test_gate_silent_on_a_red_receipt_for_this_tree(tmp_path):
    """The obligation is *the gate ran*, never *the gate was green*. A run may
    end red and report it; a run that never looked is the defect."""
    repo = _seeded_repo(tmp_path)
    (repo / "feature.py").write_text("x\n", encoding="utf-8")
    _gate_receipt(tmp_path, repo, verdict="RED")
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY),
                            _armed_gate(tmp_path, repo))
    assert out.get("decision") != "block"


def test_gate_blocks_when_the_tree_moved_after_the_receipt(tmp_path):
    """A green verdict for a tree you have since edited is a claim about code
    nobody ran — the exact shape of the failure this guard exists for."""
    repo = _seeded_repo(tmp_path)
    (repo / "feature.py").write_text("x\n", encoding="utf-8")
    _gate_receipt(tmp_path, repo)
    (repo / "feature.py").write_text("x\nthe one-line fix nobody re-ran\n", encoding="utf-8")
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY),
                            _armed_gate(tmp_path, repo))
    assert out["decision"] == "block"
    assert "a different tree" in out["reason"]


def test_gate_blocks_when_a_commit_landed_after_the_receipt(tmp_path):
    """Committing does not change `status --porcelain` or `diff HEAD` — both go
    back to empty — so HEAD is the referent that has to catch this. Without it
    a receipt taken on a dirty tree would validate the commit of that tree,
    which is *almost* right and therefore the dangerous kind of wrong."""
    repo = _seeded_repo(tmp_path)
    _git(repo, "switch", "-qc", "brr/work")
    (repo / "feature.py").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "one")
    _gate_receipt(tmp_path, repo)
    (repo / "feature.py").write_text("y\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "two")
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY),
                            _armed_gate(tmp_path, repo))
    assert out["decision"] == "block"
    assert "a different tree" in out["reason"]


def test_gate_blocks_when_the_tree_moved_while_the_gate_was_running(tmp_path):
    """#917, the third state: the receipt describes exactly the tree you are
    ending on — so every check above it is satisfied — and it still does not
    certify that tree, because a file landed *during* the legs. Before this,
    the guard was silent here: both writers sampled only after the last leg,
    and this clause recomputed that same end state and found it matching."""
    from brr import gate_receipt

    repo = _seeded_repo(tmp_path)
    (repo / "feature.py").write_text("x\n", encoding="utf-8")
    before = gate_receipt.tree_referents(repo)          # the gate starts
    (repo / "written-mid-gate.py").write_text("no leg saw this\n", encoding="utf-8")
    tree = gate_receipt.tree_fields(repo, before)       # the gate finishes
    assert tree["tree_moved_during_gate"] is True

    _gate_receipt(tmp_path, repo, **tree)
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY),
                            _armed_gate(tmp_path, repo))
    assert out["decision"] == "block"
    # It names the actual cause and the actual file. A remedy aimed at the
    # wrong cause is worse than none, so the two older sentences must not
    # appear — neither is true here.
    assert "written-mid-gate.py" in out["reason"]
    assert "the gate never ran" not in out["reason"]
    assert "a different tree than the one you are ending on" not in out["reason"]
    # Composition, not just content: the clause is placed as a whole sentence
    # and the next one starts cleanly after it.
    assert (
        "written-mid-gate.py changed during `python scripts/gate.py`, so no "
        "leg ever saw it. The receipt's GREEN is about the tree"
    ) in out["reason"]


def test_gate_silent_on_a_receipt_that_records_a_still_tree(tmp_path):
    """The honest path with the new field present: the writer checked, the
    tree held, nothing owed."""
    from brr import gate_receipt

    repo = _seeded_repo(tmp_path)
    (repo / "feature.py").write_text("x\n", encoding="utf-8")
    tree = gate_receipt.tree_fields(repo, gate_receipt.tree_referents(repo))
    assert tree["tree_moved_during_gate"] is False

    _gate_receipt(tmp_path, repo, **tree)
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY),
                            _armed_gate(tmp_path, repo))
    assert out.get("decision") != "block"


def test_gate_silent_on_a_receipt_too_old_to_carry_the_field(tmp_path):
    """Absence is unassertable, not guilty. Every receipt written before this
    shape existed lacks the field, and a guard that read absence as a moved
    tree would fire on all of them — which is how a guard stops being read."""
    repo = _seeded_repo(tmp_path)
    (repo / "feature.py").write_text("x\n", encoding="utf-8")
    payload = _gate_receipt(tmp_path, repo)
    assert "tree_moved_during_gate" not in payload
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY),
                            _armed_gate(tmp_path, repo))
    assert out.get("decision") != "block"


def test_gate_names_a_file_removed_while_the_gate_was_running(tmp_path):
    """The pair is diffed *both* ways. A file created during the gate appears
    only in the after capture; one removed during the gate appears only in the
    before capture, and looking one way named the first while silently missing
    the second — falling through to a sentence about "a file that was already
    dirty", which a deletion is not."""
    from brr import gate_receipt

    repo = _seeded_repo(tmp_path)
    (repo / "feature.py").write_text("x\n", encoding="utf-8")
    (repo / "scratch.py").write_text("deleted before the gate ended\n", encoding="utf-8")
    before = gate_receipt.tree_referents(repo)
    (repo / "scratch.py").unlink()
    tree = gate_receipt.tree_fields(repo, before)
    assert tree["tree_moved_during_gate"] is True

    _gate_receipt(tmp_path, repo, **tree)
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY),
                            _armed_gate(tmp_path, repo))
    assert out["decision"] == "block"
    assert "scratch.py" in out["reason"]
    assert "already dirty" not in out["reason"]


def test_gate_moved_tree_message_falls_back_to_the_referent_it_cannot_name(tmp_path):
    """A *second* edit to a file that was already ` M` when the gate started
    leaves its `status --porcelain` line byte-identical, so no filename is
    derivable from the pair. The sentence names the referent that moved
    rather than inventing a path.

    This branch is pinned **through the hook**, not just against the helper,
    because the defect it caught lived in neither half: `moved_sentence`'s
    ancestor returned a bare noun when it could name files and a whole clause
    when it could not, and the hook interpolated it mid-sentence as though it
    were always a noun. The filename branch read fine and this one produced
    word salad. A helper tested alone would have passed."""
    from brr import gate_receipt

    repo = _seeded_repo(tmp_path)
    (repo / "feature.py").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "feature")
    (repo / "feature.py").write_text("x\ndirty before the gate\n", encoding="utf-8")
    before = gate_receipt.tree_referents(repo)
    (repo / "feature.py").write_text("x\nand again, under the gate\n", encoding="utf-8")
    tree = gate_receipt.tree_fields(repo, before)
    assert tree["tree_moved_during_gate"] is True

    _gate_receipt(tmp_path, repo, **tree)
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY),
                            _armed_gate(tmp_path, repo))
    assert out["decision"] == "block"
    assert "diff_digest" in out["reason"]
    assert (
        "no path in `git status` changed during `python scripts/gate.py`, but "
        "diff_digest did — content moved under a path that was already dirty "
        "when the gate started. The receipt's GREEN is about the tree"
    ) in out["reason"]
    # The garble this shape exists to prevent, spelled out so a future edit
    # that reintroduces noun-vs-clause has to walk past it.
    assert "started changed" not in out["reason"]


def test_gate_silent_when_nothing_changed(tmp_path):
    """Nothing for CI to run on ⇒ nothing owed. A guard that fires on a
    read-only run is a guard that stops being read."""
    repo = _seeded_repo(tmp_path)
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY),
                            _armed_gate(tmp_path, repo))
    assert out.get("decision") != "block"


def test_gate_silent_when_the_repo_declares_no_gate(tmp_path):
    """brr owns no opinion about what a stranger's gate is. No
    `hooks.gate_command` ⇒ unassertable, silent — never a guessed build."""
    repo = _seeded_repo(tmp_path)
    (repo / "feature.py").write_text("x\n", encoding="utf-8")
    env = _armed_gate(tmp_path, repo)
    del env["BRR_GATE_COMMAND"]
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY), env)
    assert out.get("decision") != "block"


def test_gate_treats_a_malformed_receipt_as_no_receipt(tmp_path):
    """A claim has a direction it can be wrong in; this one picks the
    pessimistic one. Unreadable JSON is not evidence the gate ran."""
    repo = _seeded_repo(tmp_path)
    (repo / "feature.py").write_text("x\n", encoding="utf-8")
    (tmp_path / hooks.GATE_RECEIPT_NAME).write_text("{not json", encoding="utf-8")
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY),
                            _armed_gate(tmp_path, repo))
    assert out["decision"] == "block"
    assert "the gate never ran" in out["reason"]


def test_gate_a_receipt_for_a_different_tree_does_not_satisfy_this_one(tmp_path):
    """#820: one run that gates two trees under one outbox — this repo's own
    documented `host` pattern, a scratch `git worktree add` plus the checkout
    — must not have the second, correct gate's receipt read as covering the
    first tree, or vice versa. A receipt keyed to another tree is a different
    question's answer, neither a pass nor a failure for this guard."""
    repo = _seeded_repo(tmp_path)
    (tmp_path / "other-tree").mkdir()
    other = _seeded_repo(tmp_path / "other-tree")
    (repo / "feature.py").write_text("x\n", encoding="utf-8")
    # Gate `other`'s tree — a real, matching, GREEN receipt, just not for the
    # tree this guard is asking about.
    _gate_receipt(tmp_path, other)
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY),
                            _armed_gate(tmp_path, repo))
    assert out["decision"] == "block"
    assert "the gate never ran" in out["reason"]
    # And once `repo` gets its own entry beside `other`'s, both survive and
    # this guard reads only its own.
    _gate_receipt(tmp_path, repo)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_GOOD_REPLY),
                            _armed_gate(tmp_path, repo))
    assert out.get("decision") != "block"


# ── The `vigil` closeout obligation (#947: a claimed vigil must be armed) ─
#
# Two live instances on 2026-08-01: a run ended on `Holding for the integration
# gate.` and another on `continuing — … vigil armed (keepalive live…)` with no
# `.keepalive` on disk. Both then exited, and both times the maintainer waited
# on a run that was already `done`.


def _coexisting(*, status="absent", siblings=None, owned_children=None):
    """The presence projection the daemon writes into portal-state.

    Three states, all reachable in production: `known` (siblings live),
    `absent` (registry read, nobody there), `unimplemented` (no presence
    collector wired at that call site — a read that never happened).
    """
    facet = {"status": status, "kind": "state", "required": False}
    if siblings is not None:
        facet["siblings"] = siblings
    if owned_children is not None:
        facet["owned_children"] = owned_children
    return {"coexisting_runs": facet}


def _armed_vigil(tmp_path, *, resources=None, **portal_kw):
    """Arm only the `vigil` obligation — deliberately *without*
    `BRR_NEXT_MOVE_GUARD`, exactly as the daemon arms it, so a block here can
    have come from nowhere but this clause."""
    _portal(
        tmp_path, token="t1", pending=0,
        resources=_coexisting() if resources is None else resources,
        **portal_kw,
    )
    env = _env(tmp_path)
    env["BRR_CLOSEOUT_OBLIGATIONS"] = "vigil"
    return env


def _keepalive(tmp_path, text):
    (tmp_path / portals.KEEPALIVE_NAME).write_text(text, encoding="utf-8")


_HOLDING = "Merged #948 and #950.\n\nHolding for the integration gate."
_CONTINUING = (
    "Pushed the branch.\n\n"
    "continuing — combined gate (bp4w3plg6) still running; vigil armed"
)


def test_vigil_blocks_a_holding_claim_with_nothing_armed(tmp_path):
    """The live defect, verbatim: a polite lie the maintainer waited on."""
    env = _armed_vigil(tmp_path)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_HOLDING), env)
    assert out["decision"] == "block"
    assert "holding" in out["reason"]


def test_vigil_block_names_which_arming_was_missing(tmp_path):
    """"Block" is not the product — knowing what to write next is."""
    env = _armed_vigil(tmp_path)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_CONTINUING), env)
    reason = out["reason"]
    assert portals.KEEPALIVE_NAME in reason
    assert "spawn:" in reason
    assert "background shell command is not a continuation" in reason


def test_vigil_accepts_a_live_keepalive(tmp_path):
    """Arming #1. The in-thought vigil the substrate documents."""
    env = _armed_vigil(tmp_path)
    _keepalive(tmp_path, "+30m\n")
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_CONTINUING), env)
    assert out.get("decision") != "block"


def test_vigil_accepts_an_iso_keepalive_deadline(tmp_path):
    env = _armed_vigil(tmp_path)
    later = datetime.datetime.now(
        tz=datetime.timezone.utc
    ) + datetime.timedelta(minutes=20)
    _keepalive(tmp_path, later.strftime("%Y-%m-%dT%H:%M:%SZ"))
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_CONTINUING), env)
    assert out.get("decision") != "block"


def test_vigil_refuses_a_lapsed_keepalive(tmp_path):
    """Present is not armed. A deadline that has passed is a vigil the daemon
    itself has stopped honouring — `_keepalive_state` calls it `expired`."""
    env = _armed_vigil(tmp_path)
    _keepalive(tmp_path, "2020-01-01T00:00:00Z")
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_CONTINUING), env)
    assert out["decision"] == "block"


def test_vigil_refuses_an_unparseable_keepalive(tmp_path):
    """The file the run *named* is not the file the daemon can *read*."""
    env = _armed_vigil(tmp_path)
    _keepalive(tmp_path, "soon-ish\n")
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_CONTINUING), env)
    assert out["decision"] == "block"


def test_vigil_accepts_a_running_spawn_child(tmp_path):
    """Arming #2. A `spawn:` child survives the parent's terminal reply and
    returns as a fresh event, so a vigil resting on one is real — it is the other
    mechanism the substrate names."""
    env = _armed_vigil(tmp_path, resources=_coexisting(
        status="known",
        siblings=[{"run_id": "run-2", "parent_run_id": "run-1",
                   "is_subspawn": True}],
    ))
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_CONTINUING), env)
    assert out.get("decision") != "block"


def test_vigil_accepts_adopted_running_spawn_child_from_owned_children(tmp_path):
    env = _armed_vigil(tmp_path, resources=_coexisting(
        status="known",
        siblings=[{"run_id": "run-2", "parent_run_id": "run-old-parent",
                   "is_subspawn": True}],
        owned_children=[{"run_id": "run-2", "parent_run_id": "run-1",
                         "adopted_from_run_id": "run-old-parent"}],
    ))
    portal = json.loads((tmp_path / "portal-state.json").read_text(encoding="utf-8"))
    assert portal["resources"]["coexisting_runs"]["owned_children"][0]["parent_run_id"] == "run-1", (
        "fixture must mark the child as owned by this run or the guard below "
        "would not be asserting the adopted-edge path"
    )
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_CONTINUING), env)
    assert out.get("decision") != "block"


def test_vigil_does_not_accept_someone_elses_child(tmp_path):
    """Ownership, not headcount: a sibling spawn belonging to another run will
    never send this run's promised follow-up."""
    env = _armed_vigil(tmp_path, resources=_coexisting(
        status="known",
        siblings=[{"run_id": "run-9", "parent_run_id": "run-8",
                   "is_subspawn": True}],
    ))
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_CONTINUING), env)
    assert out["decision"] == "block"


def test_vigil_silent_when_presence_was_never_read(tmp_path):
    """`unimplemented` is a read that did not happen, not an empty registry.
    Absence of evidence is not evidence of absence, so the guard says nothing —
    it cannot prove this run has no child."""
    env = _armed_vigil(tmp_path, resources=_coexisting(status="unimplemented"))
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_CONTINUING), env)
    assert out.get("decision") != "block"


def test_vigil_silent_without_a_run_id_to_match_on(tmp_path):
    """No run id ⇒ ownership is unassertable, same reason."""
    env = _armed_vigil(tmp_path)
    del env["BRR_RUN_ID"]
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_CONTINUING), env)
    assert out.get("decision") != "block"


def test_vigil_silent_without_the_reply(tmp_path):
    """The claim lives in the reply; a Shell that hands over none (codex today)
    leaves nothing to assert from."""
    env = _armed_vigil(tmp_path)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", env)
    assert out.get("decision") != "block"


def test_vigil_is_off_unless_armed(tmp_path):
    _portal(tmp_path, token="t1", pending=0, resources=_coexisting())
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_HOLDING), _env(tmp_path))
    assert out.get("decision") != "block"


def test_vigil_blocks_once_per_claim(tmp_path):
    """#779's anti-pattern is a boundary that re-asserts forever. This one
    shares the closeout latch, which since #981 is keyed on the reply: the
    same unchanged claim, presented twice, is answered once."""
    env = _armed_vigil(tmp_path)
    first, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_HOLDING), env)
    assert first["decision"] == "block"
    second, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(_HOLDING), env)
    assert second.get("decision") != "block"


def test_vigil_leaves_the_honest_closes_alone(tmp_path):
    """`done — receipt` and `blocked — whose move` are always legal, and stay
    legal even when the sentence around them says "waiting"."""
    for reply in (
        "...\n\n**done** — merged #948, #950; receipts on both",
        "...\n\nblocked — needs the API token; waiting on you",
        "...\n\ndone — shipped. The gate is still running, I'll not wait.",
        "Which way?\n\n1. cut it\n2. keep the flag\n\nI'd take (1).",
    ):
        env = _armed_vigil(tmp_path)
        (tmp_path / hooks.HOOK_STATE_NAME).unlink(missing_ok=True)
        out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(reply), env)
        assert out.get("decision") != "block", reply


def test_vigil_is_blind_to_a_claim_inside_a_code_fence(tmp_path):
    """A run that *documents* this guard writes the claim into a fence. A
    matcher blind to code spans reads the example as the thing (#562)."""
    reply = (
        "Added the guard.\n\n"
        "```\n"
        "continuing — gate still running\n"
        "```\n\n"
        "It also catches `holding` and `standing by`.\n\n"
        "done — hooks.py + tests, receipt in the card"
    )
    env = _armed_vigil(tmp_path)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin(reply), env)
    assert out.get("decision") != "block"


class TestVigilMatcher:
    """The matcher's shape, unit-level — and what it deliberately declines."""

    def test_the_bare_state_line_is_the_strong_signal(self):
        assert hooks.vigil_claim("x\n\ncontinuing — the gate runs") == "continuing"
        assert hooks.vigil_claim("x\n\n**continuing** — the gate runs") == "continuing"

    def test_the_observed_prose_vocabulary(self):
        for line, word in (
            ("Holding for the integration gate.", "holding for"),
            ("Vigil armed on the combined tree.", "vigil armed"),
            ("Vigil live until the gate returns.", "vigil live"),
            ("Waiting on the gate to come back.", "waiting on"),
            ("I will report back when it lands.", "will report back"),
            ("Standing by for the merge.", "standing by for"),
        ):
            assert hooks.vigil_claim(f"body\n\n{line}") == word, line

    def test_done_and_blocked_are_never_a_claim(self):
        assert hooks.vigil_claim("x\n\ndone — merged, holding nothing") is None
        assert hooks.vigil_claim("x\n\nblocked — your move; standing by") is None

    def test_the_final_line_outranks_the_body(self):
        """A `continuing` paragraph *above* an honest close is not a vigil —
        preferring the final line is what keeps the guard off a reply that
        narrates its own history before closing."""
        reply = (
            "continuing — that was the plan at 14:30\n\n"
            "…then the gate came back.\n\n"
            "done — green, merged"
        )
        assert hooks.vigil_claim(reply) is None

    def test_an_earlier_state_cannot_classify_a_different_final_line(self):
        reply = (
            "continuing — that was the plan at 14:30\n\n"
            "The gate completed and the branch is ready."
        )
        assert hooks.vigil_claim(reply) is None

    def test_a_final_claim_survives_an_earlier_honest_state(self):
        reply = "done — first batch landed\n\nHolding for the integration gate."
        assert hooks.vigil_claim(reply) == "holding for"

    def test_topic_words_are_not_continuation_claims(self):
        for line in (
            "Holding the worker count constant proved the race.",
            "The vigil design belongs in issue 959.",
        ):
            assert hooks.vigil_claim(f"body\n\n{line}") is None

    def test_a_claim_in_an_inline_code_span_is_not_a_claim(self):
        assert hooks.vigil_claim("x\n\nThe matcher catches `holding` too.") is None

    def test_a_reply_that_ends_on_nothing_is_not_automatically_a_claim(self):
        """This guard asserts one thing only. "Ends on no state at all" is the
        next-move clause's business, and pretending otherwise would double a
        single defect into two block sentences."""
        assert hooks.vigil_claim("I refactored the module and the tests pass.") is None

    def test_an_empty_reply_claims_nothing(self):
        assert hooks.vigil_claim("") is None
        assert hooks.vigil_claim("\n\n   \n") is None


class TestStopRunBody:
    """The run's own body rides the closeout delta (wyrd §5, maintainer 2026-07-19)."""

    def _payload(self, text: str = "") -> dict:
        return {
            "run": {"id": "run-1"},
            "attention": {"pending_event_count": 0, "pending_outbox_file_count": 0},
            "card": {"active": bool(text), "text": text, "stale": False},
        }

    def test_stop_hands_back_the_whole_body_not_the_now_projection(self):
        body = "## Now\n\nLanding it.\n\n## Arc\n\nThe part that fell out of context."

        rendered = hooks.format_delta(self._payload(), stop=True, run_body=body)

        assert "your run body" in rendered
        assert "The part that fell out of context." in rendered
        assert "Landing it." in rendered

    def test_the_body_is_a_closeout_capsule_only(self):
        body = "## Now\n\nMid-flight."

        assert "your run body" not in (hooks.format_delta(self._payload(body)) or "")
        assert "your run body" not in hooks.format_delta(self._payload(body), seed=True)

    def test_a_fresh_read_beats_the_heartbeat_snapshot(self):
        """A card rewritten in the run's final action predates no portal write."""
        rendered = hooks.format_delta(
            self._payload("stale snapshot"), stop=True, run_body="## Now\n\nwritten last",
        )

        assert "written last" in rendered
        assert "stale snapshot" not in rendered

    def test_without_a_fresh_read_the_snapshot_still_serves(self):
        rendered = hooks.format_delta(self._payload("from the snapshot"), stop=True)

        assert "from the snapshot" in rendered

    def test_a_pathological_card_is_tail_capped_not_dropped(self):
        body = "x" * (hooks._STOP_BODY_MAX_CHARS + 500) + "THE-LATEST-THINKING"

        rendered = hooks.format_delta(self._payload(), stop=True, run_body=body)

        assert "THE-LATEST-THINKING" in rendered
        assert len(rendered) < len(body) + 2000

    def test_a_run_that_wrote_no_card_adds_no_body_line(self):
        assert "your run body" not in hooks.format_delta(
            self._payload(), stop=True, run_body="   \n",
        )


# ── Slice 8 (#513): the agnoster mid-run status bar ──────────────────────


def _bar_payload(**overrides):
    """A fully-laden post-tool payload — every bar segment has something to
    show. Individual tests knock pieces out via ``overrides`` to exercise the
    quiet/partial shapes."""
    until = (
        datetime.datetime.now(tz=datetime.timezone.utc)
        + datetime.timedelta(hours=3)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "run": {"id": "run-260723-1241-3jy8"},
        "attention": {"pending_event_count": 0, "pending_outbox_file_count": 0},
        "inbound": {"events": []},
        "budget": {
            "elapsed_seconds": 16 * 60, "budget_seconds": 120 * 60,
            "keepalive": {"status": "active", "until": until},
        },
        "outbound": {"replies_current": 2, "replies_other": 3,
                     "outbound_messages": 0},
        "produce": {"known": True, "counts": {"commit": 3, "kb": 1}},
        "card": {"active": True, "stale": False},
        "resources": {
            "quota": {
                "status": "known",
                "summary": (
                    "session 57% left (resets 8:30pm (Europe/Berlin)); "
                    "week 50% left (resets Jul 24, 12am (Europe/Berlin)); "
                    "Fable week 27% left"
                ),
            },
            "coexisting_runs": {
                "status": "known",
                "siblings": [{"run_id": "run-x"}],
            },
        },
    }
    for key, value in overrides.items():
        payload[key] = value
    return payload


def test_post_tool_bar_renders_every_segment_when_laden():
    rendered = hooks.format_delta(_bar_payload(), mood="smug_")
    bar = rendered.splitlines()[0]

    assert bar == (
        "⌁ 3jy8 │ ⏱ 16/120m │ q S57·W50·F27 │ ▷1 │ rb3h │ ⇡2+3 │ ⚒4 │ "
        "mood brnrd smug_ │ card ok"
    )


def test_post_tool_bar_is_quiet_when_nothing_is_laden():
    payload = _bar_payload(
        budget={"elapsed_seconds": 60, "budget_seconds": 7200},
        outbound={"replies_current": 0, "replies_other": 0,
                  "outbound_messages": 0},
        produce={"known": False, "counts": {}},
        resources={},
    )
    assert hooks.format_delta(payload) is None


def test_post_tool_bar_short_when_only_run_id_and_budget_move():
    # A boundary with nothing to act on still renders a *short* bar when
    # something genuinely laden triggers it — here, a known quota bucket.
    payload = _bar_payload(
        budget={"elapsed_seconds": 16 * 60, "budget_seconds": 120 * 60},
        outbound={"replies_current": 0, "replies_other": 0,
                   "outbound_messages": 0},
        produce={"known": False, "counts": {}},
        resources={"quota": {"status": "known", "summary": "week 80% left"}},
    )
    rendered = hooks.format_delta(payload)
    bar = rendered.splitlines()[0]
    assert bar == "⌁ 3jy8 │ ⏱ 16/120m │ q W80 │ card ok"


def test_post_tool_bar_pending_events_always_get_a_detail_line():
    # #513: "never bury an obligation in a glyph" — pending events are never
    # compressed into a bar segment, and non-zero pending always earns a
    # full action-verb detail line below the bar, no matter how quiet
    # everything else is.
    payload = _bar_payload(
        attention={"pending_event_count": 1, "pending_outbox_file_count": 0},
        inbound={"events": [
            {"id": "evt-9", "source": "telegram", "summary": "ping"},
        ]},
        budget={"elapsed_seconds": 0, "budget_seconds": 0},
        outbound={"replies_current": 0, "replies_other": 0,
                  "outbound_messages": 0},
        produce={"known": False, "counts": {}},
        resources={},
    )
    rendered = hooks.format_delta(payload)
    lines = rendered.splitlines()
    assert "▷" not in lines[0] and "⚒" not in lines[0]
    assert "1 pending event(s)" in rendered
    assert "Address each below" in rendered
    # Letter chrome: one header row (glyph · id · source · size) + the body
    # inline in full — a short message is never cut.
    assert "- ✉ evt-9 · telegram · 4 B" in rendered
    assert "ping" in rendered


def test_post_tool_bar_never_renders_a_pending_count_as_a_segment():
    # The obligation must live in a detail line, not a glyph on the bar
    # itself — assert the bar *line* (not the whole rendered block) carries
    # no bare pending count.
    payload = _bar_payload(
        attention={"pending_event_count": 2, "pending_outbox_file_count": 0},
        inbound={"events": []},
    )
    rendered = hooks.format_delta(payload)
    bar = rendered.splitlines()[0]
    assert "pending" not in bar


# ── Armed dated-letters block (#904) ──────────────────────────────────


def test_render_armed_rows_shape():
    rows = hooks._render_armed_rows([
        {"id": "a", "when": "T1", "heading": "H1", "premise": "P1"},
        {"id": "b", "when": "T2", "heading": "H2"},
    ])
    assert rows == [
        "⏲ 2 armed dated letter(s) — pending `at:` schedule entries, "
        "swept fresh each boundary:",
        "- ⏲ T1 · H1 · premise: P1",
        "- ⏲ T2 · H2",
    ]


def test_render_armed_rows_empty_input():
    assert hooks._render_armed_rows([]) == []
    assert hooks._render_armed_rows(None) == []


def test_post_tool_bar_renders_armed_dated_letters():
    payload = _bar_payload(schedule={"armed": [
        {
            "id": "ship-the-thing", "when": "2026-08-01T09:00:00Z",
            "heading": "Ship the thing",
            "premise": "the release branch is still green",
        },
    ]})
    rendered = hooks.format_delta(payload, mood="smug_")
    assert "⏲ 1 armed dated letter(s)" in rendered
    assert (
        "- ⏲ 2026-08-01T09:00:00Z · Ship the thing · "
        "premise: the release branch is still green"
    ) in rendered


def test_post_tool_bar_armed_letter_without_premise_omits_the_clause():
    payload = _bar_payload(
        budget={"elapsed_seconds": 60, "budget_seconds": 7200},
        outbound={"replies_current": 0, "replies_other": 0,
                  "outbound_messages": 0},
        produce={"known": False, "counts": {}},
        resources={},
        schedule={"armed": [
            {"id": "followup", "when": "2026-08-01T09:00:00Z", "heading": "Followup"},
        ]},
    )
    rendered = hooks.format_delta(payload)
    assert rendered is not None
    assert "- ⏲ 2026-08-01T09:00:00Z · Followup" in rendered
    assert "premise:" not in rendered


def test_post_tool_bar_opens_on_armed_letters_alone():
    # Otherwise-quiet boundary (the exact fixture that returns None in
    # test_post_tool_bar_is_quiet_when_nothing_is_laden) must still open
    # the bar when there is an armed dated letter — a run must see it even
    # when nothing else earned a turn.
    payload = _bar_payload(
        budget={"elapsed_seconds": 60, "budget_seconds": 7200},
        outbound={"replies_current": 0, "replies_other": 0,
                  "outbound_messages": 0},
        produce={"known": False, "counts": {}},
        resources={},
        schedule={"armed": [{"id": "x", "when": "-", "heading": "X"}]},
    )
    assert hooks.format_delta(payload) is not None


def test_no_armed_letters_renders_no_block():
    payload = _bar_payload()
    rendered = hooks.format_delta(payload, seed=True)
    assert "armed dated letter" not in rendered


def test_seed_and_closeout_render_armed_dated_letters():
    payload = _bar_payload(schedule={"armed": [
        {
            "id": "ship-the-thing", "when": "2026-08-01T09:00:00Z",
            "heading": "Ship the thing", "premise": "green branch",
        },
    ]})
    seed = hooks.format_delta(payload, seed=True)
    stop = hooks.format_delta(payload, stop=True)
    expected = (
        "- ⏲ 2026-08-01T09:00:00Z · Ship the thing · premise: green branch"
    )
    assert expected in seed
    assert expected in stop


def test_armed_dated_letters_are_never_seen_suppressed():
    # Unlike the pending-event letter chrome (which drops to a one-line
    # "seen ×N" form after its first render), the armed block is a pure
    # projection of the current payload with no caller-owned ledger — it
    # must render identically on a second call against the same snapshot,
    # not collapse the way an already-shown pending event does.
    payload = _bar_payload(schedule={"armed": [
        {"id": "x", "when": "T", "heading": "X"},
    ]})
    first = hooks.format_delta(payload, mood="smug_")
    second = hooks.format_delta(payload, mood="smug_")
    assert "- ⏲ T · X" in first
    assert "- ⏲ T · X" in second


def test_post_tool_surfaces_armed_dated_letters_end_to_end(tmp_path):
    """Through the real hook CLI entry point, off a real portal-state.json
    the way the daemon writes it — not just the pure ``format_delta`` call."""
    _portal(tmp_path, token="t1", pending=0, schedule={"armed": [
        {
            "id": "ship-the-thing", "when": "2026-08-01T09:00:00Z",
            "heading": "Ship the thing",
            "premise": "the release branch is still green",
        },
    ]})
    out, code = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))
    assert code == 0
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "armed dated letter" in ctx
    assert (
        "⏲ 2026-08-01T09:00:00Z · Ship the thing · "
        "premise: the release branch is still green"
    ) in ctx


# ── Armed-letter obligation classification (#1209) ────────────────────────
#
# `_has_post_tool_obligations`'s armed-letters clause used to treat mere
# *presence* of an armed `at:` entry as an obligation — so a letter hours
# out rendered the OBLIGATION-classed block at every boundary of the whole
# run, defeating the quiet-boundary economy (#1116) until it fired. The fix
# scopes the clause to overdue-or-imminent entries only; the always-shown
# render (`_render_armed_rows`, exercised above) is untouched.


def _obligations_portal(**armed_entry_overrides):
    """Minimal portal with every *other* obligation signal quiet, so only
    the armed-letters clause under test can flip the verdict."""
    entry = {"id": "x", "when": "T", "heading": "X"}
    entry.update(armed_entry_overrides)
    return {
        "attention": {"pending_event_count": 0, "pending_outbox_file_count": 0},
        "card": {"stale": False},
        "notices": [],
        "schedule": {"armed": [entry]},
    }


def test_armed_entry_far_in_the_future_is_not_an_obligation():
    portal = _obligations_portal(at=time.time() + 6 * 3600)
    assert hooks._has_post_tool_obligations(
        portal, None, None, False, False,
    ) is False


def test_armed_entry_within_the_imminent_horizon_is_an_obligation():
    portal = _obligations_portal(at=time.time() + 5 * 60)
    assert hooks._has_post_tool_obligations(
        portal, None, None, False, False,
    ) is True


def test_armed_entry_overdue_is_an_obligation():
    portal = _obligations_portal(at=time.time() - 100)
    assert hooks._has_post_tool_obligations(
        portal, None, None, False, False,
    ) is True


def test_armed_entry_missing_at_is_an_obligation():
    # Fail toward the obligation, same posture as an unavailable portal
    # elsewhere in this function: a malformed payload must not silently
    # mute the escalation. `_obligations_portal()`'s default entry carries
    # no `at` key at all — the shape a truly malformed projection would take.
    portal = _obligations_portal()
    assert "at" not in portal["schedule"]["armed"][0]
    assert hooks._has_post_tool_obligations(
        portal, None, None, False, False,
    ) is True


def test_armed_entry_render_path_unaffected_by_obligation_horizon():
    # `_render_armed_rows` / the bar's armed block still shows a
    # far-future entry regardless of the obligation classification above —
    # the two are deliberately decoupled (#1209's own text: "the sweep
    # line itself can stay — it is the gate-opening that lies").
    rows = hooks._render_armed_rows([
        {"id": "x", "when": "2026-12-25T09:00:00Z", "heading": "X",
         "at": time.time() + 6 * 3600},
    ])
    assert "⏲ 2026-12-25T09:00:00Z · X" in rows[1]


def test_armed_entry_is_due_helper_directly():
    now = time.time()
    assert hooks._armed_entry_is_due({"at": now + 3600}) is False
    assert hooks._armed_entry_is_due({"at": now + 60}) is True
    assert hooks._armed_entry_is_due({"at": now - 1}) is True
    assert hooks._armed_entry_is_due({}) is True
    assert hooks._armed_entry_is_due({"at": None}) is True
    assert hooks._armed_entry_is_due({"at": "not-a-number"}) is True


# ── The `.mood` control channel (#566 layer 2) ───────────────────────────


def test_post_tool_mood_renders_as_a_bar_segment(tmp_path):
    # Display, no ask: a quiet boundary shows the face and says nothing about
    # it. The old unconditional "·keep?" asked at every boundary, which is the
    # habituation this module already names one segment over (2026-07-23).
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    (tmp_path / hooks.MOOD_NAME).write_text("bo_Od\n", encoding="utf-8")
    out, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    # Glyph-prefixed: `bo_Od` is a real handle, so the face renders (#601 seam).
    assert "mood b·_·d bo_Od" in ctx
    assert "keep?" not in ctx
    assert "←" not in ctx


def test_post_tool_mood_absent_renders_no_segment(tmp_path):
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    out, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "mood" not in ctx


def test_seed_and_stop_render_mood_as_a_plain_prose_line(tmp_path):
    # Seed/stop stay affirmative prose (#513) — mood still rides every
    # boundary (#566), just not compressed into a bar segment there.
    #
    # The fixture used to be "curious", which is a *family* word and not a
    # handle — so this pinned the unresolved rendering as if it were the
    # normal one, which is precisely how the whole channel shipped broken:
    # every mood test in this file named a face that does not exist, and
    # "no glyph" was therefore the expected output everywhere.
    _portal(tmp_path, token="t1", pending=0)
    (tmp_path / hooks.MOOD_NAME).write_text("hmn_", encoding="utf-8")
    out, _ = hooks.run_hook(hooks.PHASE_SESSION_START, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "- mood: b·_·d hmn_" in ctx
    assert "a mood worth showing is one the work moved" in ctx


def test_seed_says_so_when_the_mood_handle_did_not_resolve(tmp_path):
    """The boundary that can still fix it is the boundary that says so.

    ``curious`` is the word; ``hmn_``/``ooh_``/``peek_`` are the faces. A
    resident writing the word believed it wore one for the whole run, and
    the dashboard published the word as an id. Now the seed answers back.
    """
    _portal(tmp_path, token="t1", pending=0)
    (tmp_path / hooks.MOOD_NAME).write_text("curious", encoding="utf-8")
    out, _ = hooks.run_hook(hooks.PHASE_SESSION_START, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "- mood: ✗ curious → " in ctx


def test_mood_malformed_file_is_read_defensively(tmp_path):
    # A huge, newline-free `.mood` must never bloat the boundary or crash
    # rendering — first line only, hard-capped at read time.
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    (tmp_path / hooks.MOOD_NAME).write_text("x" * 5000, encoding="utf-8")
    out, code = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))
    assert code == 0
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert len(ctx) < 2000


def test_mood_blank_file_renders_no_segment(tmp_path):
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    (tmp_path / hooks.MOOD_NAME).write_text("   \n\nsecond line\n", encoding="utf-8")
    out, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "mood" not in ctx


def test_mood_chip_truncates_a_long_name():
    """The cap is on the *name*, and the miss mark rides outside it.

    An overlong handle is by definition not a face, so the chip marks it —
    but the truncation is what keeps the statusline a line. The ``✗`` and
    any suggestions are the payload of a broken state: loud exactly when
    something is wrong, absent the rest of the time.
    """
    chip = hooks._mood_chip("a-very-long-mood-name-that-overflows-the-chip")
    assert chip == "✗ a-very-long-mood…"
    name = chip.removeprefix("✗ ")
    assert len(name) <= hooks._MOOD_DISPLAY_MAX_CHARS + 1


def test_emote_glyph_degrades_to_none_for_an_unresolvable_name():
    """A handle with no library entry degrades silently — never raises.

    This test used to read "`brr.emotes` (#566) does not exist in this tree
    yet" and assert no glyph for **`bo_Od`** — which is a real entry in the
    shipped library. So once #601 landed it was no longer testing degradation
    at all: it was pinning the broken resolver's output as correct. A fixture
    that quietly becomes valid input is how a negative test turns into a lock
    on the bug. The handle below is invented on purpose and stays invented.
    """
    from brr import emotes

    name = "not_an_emote_xyz"
    assert name not in emotes.EMOTES, "fixture must stay unresolvable"
    assert emotes.lookup(name) is None, "fixture must stay unresolvable"
    assert hooks._emote_glyph(name) is None
    # Degrades, but no longer *silently*: the chip used to render the bare
    # word, which is indistinguishable from a face that simply has no glyph.
    # A run reading its own boundary could not tell the two apart, and on the
    # dashboard the same ambiguity shipped to the public page.
    assert hooks._mood_chip(name) == f"✗ {name}"


def test_a_missed_handle_names_what_it_was_reaching_for():
    """The silence, not the miss, was the defect.

    ``satisfied`` is a family word — four faces — so ``lookup`` declines to
    guess and always will; that part is the honesty bar working. What was
    broken is that declining looked *exactly* like succeeding: four ``null``s
    on the wire, a bare word in the chip, and a run that believed it was
    wearing a face until a human looked at brnrd.dev and said otherwise.

    So the chip names the candidates. The run can fix it at the next
    boundary, which is the only moment fixing it is cheap.
    """
    from brr import emotes

    name = "satisfied"
    assert emotes.lookup(name) is None, "a family word must not resolve to one face"

    chip = hooks._mood_chip(name)
    assert chip.startswith("✗ satisfied → "), chip
    named = chip.split(" → ", 1)[1].split(" · ")
    assert named, chip
    for handle in named:
        assert emotes.lookup(handle) is not None, f"{handle!r} must be a real handle"
        assert emotes.EMOTES[handle].family == "satisfied", handle


def test_the_word_for_the_feeling_renders_the_same_chip_as_the_handle():
    """``focused`` is what a run writes; ``fo.cus`` is what the file calls it.

    This is the whole reported bug in one assertion. ``.mood`` is a
    machine-parsed channel, the handles were minted as weave marks, and the
    parser matched them byte for byte — so the obvious spelling published
    nothing and the dashboard printed the raw string. Both spellings are one
    face now, and the chip proves it end to end rather than at the library
    boundary where the mismatch was invisible.
    """
    from brr import emotes

    assert emotes.lookup("focused") is emotes.EMOTES["fo.cus"]
    assert hooks._mood_chip("focused") == f"{emotes.glyph('fo.cus')} focused"


def test_a_real_emote_handle_renders_its_face_in_the_chip():
    """The seam between the statusline (#603) and the emote library (#601).

    Every other mood test in this file names an *invented* handle — "stoked",
    "curious", "bo_Od" — so "no glyph" was the expected output everywhere and
    a broken resolver was indistinguishable from a green suite. It was broken:
    ``hooks._emote_glyph`` called ``emotes.glyph``, a function the shipped
    library never had, and the caller's deliberately broad ``except`` ate the
    ``AttributeError`` on every boundary. The face never rendered, for anyone,
    ever — which is exactly what a guard swallowing its own signal buys you.

    So this test uses a handle that really is in ``EMOTES``. It is the only
    one here that can fail if the seam breaks again.
    """
    from brr import emotes

    name = "fo.cus"
    assert emotes.lookup(name) is not None, "fixture handle must be a real emote"

    expected = emotes.glyph(name)
    assert expected, "a resolvable handle must yield a base-frame glyph"
    assert expected == emotes.EMOTES[name].frames[0], "base frame is frames[0]"

    assert hooks._emote_glyph(name) == expected
    assert hooks._mood_chip(name) == f"{expected} {name}"


def test_quota_chip_disambiguates_a_repeated_first_letter():
    # Two per-model week buckets that would otherwise both abbreviate to the
    # same letter must not collapse into one chip.
    resources = {
        "quota": {
            "status": "known",
            "summary": "Wisp week 10% left; Wren week 20% left",
        },
    }
    chip = hooks._quota_chip(resources)
    assert chip is not None
    letters = chip[len("q "):].split("·")
    assert len(letters) == 2
    assert letters[0] != letters[1]


# ── Mood asks on the edge, not on the tick (2026-07-23) ──────────────────


def _batch(response: str, tool: str = "Bash") -> str:
    """A claude ``PostToolBatch`` stdin payload with one call in it.

    Shape verified against a live payload (run-260723-1659-85cx): the batch
    hands over ``{tool_name, tool_input, tool_use_id, tool_response}`` and no
    structured error flag — a non-zero exit arrives as the string
    ``"Exit code 1"``.
    """
    return json.dumps({
        "hook_event_name": "PostToolBatch",
        "tool_calls": [{
            "tool_name": tool,
            "tool_input": {"command": "x"},
            "tool_use_id": "toolu_1",
            "tool_response": response,
        }],
    })


def test_tool_surprise_reads_a_failed_call_and_ignores_a_clean_one():
    assert hooks._tool_surprise(json.loads(_batch("Exit code 1"))) == "Bash ✗"
    assert hooks._tool_surprise(json.loads(_batch("probe-armed"))) is None
    assert hooks._tool_surprise({}) is None
    assert hooks._tool_surprise({"tool_calls": "nonsense"}) is None


def test_mood_ask_fires_on_the_failure_edge_and_names_it(tmp_path):
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    (tmp_path / hooks.MOOD_NAME).write_text("fo.cus\n", encoding="utf-8")
    out, _ = hooks.run_hook(
        hooks.PHASE_POST_TOOL, _batch("Exit code 1"), _env(tmp_path)
    )
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "mood b·_·d fo.cus ← Bash ✗" in ctx


def test_mood_ask_is_transition_stamped_not_per_pass(tmp_path):
    # A run debugging a red test fails at every boundary of the debugging.
    # The interesting moment is clean -> broken, once — the same discipline a
    # commit inside a retry loop needs.
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    (tmp_path / hooks.MOOD_NAME).write_text("fo.cus\n", encoding="utf-8")
    env = _env(tmp_path)
    first, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, _batch("Exit code 1"), env)
    assert "← Bash ✗" in first["hookSpecificOutput"]["additionalContext"]

    second, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, _batch("Exit code 2"), env)
    ctx = (second.get("hookSpecificOutput") or {}).get("additionalContext", "")
    assert "←" not in ctx

    # Clean again, then broken again — that is a fresh edge.
    hooks.run_hook(hooks.PHASE_POST_TOOL, _batch("all good"), env)
    third, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, _batch("Exit code 1"), env)
    assert "← Bash ✗" in third["hookSpecificOutput"]["additionalContext"]


def test_mood_edge_renders_even_when_the_portal_token_has_not_moved(tmp_path):
    # The gate this opens is the point: a failing tool call changes nothing
    # the daemon writes into portal-state, so a token-gated ask would render
    # nothing at exactly the boundary it exists for.
    _portal(tmp_path, token="t1", pending=0)
    (tmp_path / hooks.MOOD_NAME).write_text("fo.cus\n", encoding="utf-8")
    env = _env(tmp_path)
    hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)  # consume the token
    quiet, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    assert not (quiet.get("hookSpecificOutput") or {}).get("additionalContext")

    edged, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, _batch("Exit code 1"), env)
    assert "← Bash ✗" in edged["hookSpecificOutput"]["additionalContext"]


def test_no_mood_means_no_surprise_annotation(tmp_path):
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    out, _ = hooks.run_hook(
        hooks.PHASE_POST_TOOL, _batch("Exit code 1"), _env(tmp_path)
    )
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "←" not in ctx and "mood" not in ctx


# ── The orientation ledger (#513 Slice 9) ────────────────────────────────
#
# Fixture discipline (#611): every negative assertion below lives beside a
# positive twin on the *same* input shape, so an assertion of absence can
# never be green against an input that could not have produced the segment.


def _orient_files(tmp_path, names=("a.md", "b.md")):
    files = []
    for name in names:
        path = tmp_path / name
        path.write_text(f"# {name}\n", encoding="utf-8")
        files.append(path)
    return files


def _boot_score(tmp_path, files):
    path = tmp_path / "boot-score.json"
    path.write_text(json.dumps({
        "orientation_set": [
            {"path": str(f), "bytes": f.stat().st_size} for f in files
        ],
    }), encoding="utf-8")
    return path


def _orient_env(tmp_path):
    env = _env(tmp_path)
    env["BRR_BOOT_SCORE"] = str(tmp_path / "boot-score.json")
    return env


def _read_batch(*paths):
    return json.dumps({
        "hook_event_name": "PostToolBatch",
        "tool_calls": [
            {"tool_name": "Read", "tool_input": {"file_path": str(p)},
             "tool_use_id": f"t{i}", "tool_response": "file contents"}
            for i, p in enumerate(paths)
        ],
    })


def _sliced_read_batch(path, *, offset=0, limit=90):
    return json.dumps({
        "hook_event_name": "PostToolBatch",
        "tool_calls": [{
            "tool_name": "Read",
            "tool_input": {
                "file_path": str(path),
                "offset": offset,
                "limit": limit,
            },
            "tool_use_id": f"read-{offset}-{limit}",
            "tool_response": "file contents",
        }],
    })


def _inject_text(out):
    return (out.get("hookSpecificOutput") or {}).get("additionalContext") or ""


def test_orient_meters_reads_against_the_set_while_the_walk_is_open(tmp_path):
    a, _b = _orient_files(tmp_path)
    _boot_score(tmp_path, _orient_files(tmp_path))
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    out, code = hooks.run_hook(
        hooks.PHASE_POST_TOOL, _read_batch(a), _orient_env(tmp_path)
    )
    assert code == 0
    assert "orient 1/2" in _inject_text(out).splitlines()[0]


def test_orient_segment_leaves_at_completion(tmp_path):
    a, b = _orient_files(tmp_path)
    _boot_score(tmp_path, [a, b])
    env = _orient_env(tmp_path)
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    first, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, _read_batch(a), env)
    # The positive twin: this exact setup renders the meter while open.
    assert "orient 1/2" in _inject_text(first)
    _portal(tmp_path, token="t2", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    second, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, _read_batch(b), env)
    text = _inject_text(second)
    assert text  # the bar still renders — only the meter has left
    assert "orient" not in text


def test_orient_partial_read_is_not_a_completed_file(tmp_path):
    """Opening ninety lines of a long page proves touch, not orientation."""
    page = tmp_path / "long.md"
    page.write_text(
        "".join(f"line {number}\n" for number in range(240)),
        encoding="utf-8",
    )
    _boot_score(tmp_path, [page])
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])

    out, _ = hooks.run_hook(
        hooks.PHASE_POST_TOOL,
        _sliced_read_batch(page, offset=0, limit=90),
        _orient_env(tmp_path),
    )

    assert "orient 0/1" in _inject_text(out)
    state = json.loads(
        (tmp_path / hooks.HOOK_STATE_NAME).read_text(encoding="utf-8")
    )
    assert state[hooks.ORIENTATION_READ_KEY] == []
    assert state[hooks.ORIENTATION_READ_RANGES_KEY] == {
        str(page.resolve()): [[0, 90]]
    }


def test_orient_paged_reads_complete_only_after_covering_the_file(tmp_path):
    page = tmp_path / "long.md"
    page.write_text(
        "".join(f"line {number}\n" for number in range(240)),
        encoding="utf-8",
    )
    _boot_score(tmp_path, [page])
    env = _orient_env(tmp_path)
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    first, _ = hooks.run_hook(
        hooks.PHASE_POST_TOOL,
        _sliced_read_batch(page, offset=0, limit=90),
        env,
    )
    assert "orient 0/1" in _inject_text(first)

    _portal(tmp_path, token="t2", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    second, _ = hooks.run_hook(
        hooks.PHASE_POST_TOOL,
        _sliced_read_batch(page, offset=90, limit=150),
        env,
    )

    assert "orient" not in _inject_text(second)
    state = json.loads(
        (tmp_path / hooks.HOOK_STATE_NAME).read_text(encoding="utf-8")
    )
    assert state[hooks.ORIENTATION_READ_KEY] == [str(page.resolve())]
    assert state[hooks.ORIENTATION_READ_RANGES_KEY] == {}


def test_orient_unbounded_read_respects_the_runner_default_page(tmp_path):
    page = tmp_path / "too-long-for-one-read.md"
    page.write_text("line\n" * (hooks._ORIENTATION_READ_DEFAULT_LIMIT + 1),
                    encoding="utf-8")
    _boot_score(tmp_path, [page])
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])

    out, _ = hooks.run_hook(
        hooks.PHASE_POST_TOOL, _read_batch(page), _orient_env(tmp_path)
    )

    assert "orient 0/1" in _inject_text(out)


def test_orient_ignores_reads_outside_the_set(tmp_path):
    a, b = _orient_files(tmp_path)
    _boot_score(tmp_path, [a, b])
    unrelated = tmp_path / "unrelated.md"
    unrelated.write_text("not in the set\n", encoding="utf-8")
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    out, _ = hooks.run_hook(
        hooks.PHASE_POST_TOOL, _read_batch(unrelated), _orient_env(tmp_path)
    )
    assert "orient 0/2" in _inject_text(out)


def test_orient_skip_on_card_silences_the_meter_but_not_the_ledger(tmp_path):
    a, b = _orient_files(tmp_path)
    _boot_score(tmp_path, [a, b])
    env = _orient_env(tmp_path)
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    open_walk, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    # Positive twin: without the declaration this input renders the meter.
    assert "orient 0/2" in _inject_text(open_walk)

    (tmp_path / hooks.CARD_NAME).write_text(
        "## Now\nassuming prior knowledge, skipping orientation\n",
        encoding="utf-8",
    )
    _portal(tmp_path, token="t2", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    skipped, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, _read_batch(a), env)
    assert "orient" not in _inject_text(skipped)
    # The observation still lands — skip silences the segment, never the
    # instrument (Slice 4 reads completeness from this state).
    state = json.loads(
        (tmp_path / hooks.HOOK_STATE_NAME).read_text(encoding="utf-8")
    )
    assert state[hooks.ORIENTATION_READ_KEY] == [str(a.resolve())]


def test_orient_skip_needs_a_declaration_not_a_mention(tmp_path):
    """Prose about skipping and orientation must not silence the meter.

    The first shape of this guard matched any single line carrying both
    words. That is line-scoped but not *declaration*-scoped, and the resident
    holds the pen on `.card` — so a line reporting the ledger's own value, or
    a line about working on this very ticket, turned the ledger off. The
    second string below is the sharp one: it contains an explicit **negation**
    and used to declare a skip.
    """
    a, b = _orient_files(tmp_path)
    _boot_score(tmp_path, [a, b])
    (tmp_path / hooks.CARD_NAME).write_text(
        "## Now\n"
        "skip the flaky test for now\n"            # words on separate lines
        "orientation files come next\n"
        "orient 3/5 rendered; nothing skipped\n"   # same line, and a negation
        "Reviewed Slice 9: skip is a first-class outcome for orientation\n"
        "the resident declares the skip for orientation on .card\n",
        encoding="utf-8",
    )
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    out, _ = hooks.run_hook(
        hooks.PHASE_POST_TOOL, "{}", _orient_env(tmp_path)
    )
    assert "orient 0/2" in _inject_text(out)


def test_orient_skip_accepts_the_terse_declaration(tmp_path):
    """`orient: skip` heading a line is the terse form, and must still work.

    Narrowing the guard is only correct if the *intended* declarations still
    land — otherwise the fix trades a false positive for a dead feature.
    """
    a, b = _orient_files(tmp_path)
    _boot_score(tmp_path, [a, b])
    (tmp_path / hooks.CARD_NAME).write_text(
        "## Now\n- orient: skip\n", encoding="utf-8",
    )
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    out, _ = hooks.run_hook(
        hooks.PHASE_POST_TOOL, "{}", _orient_env(tmp_path)
    )
    assert "orient" not in _inject_text(out)


def test_orient_skip_accepts_a_field_prefixed_declaration_with_prose_between(
    tmp_path,
):
    """#1266: the real declaration that shipped this fix, verbatim.

    A 145-minute live run wrote this exact line on `.card`, per the boot's
    own "declare the skip" instruction, and the meter rendered `orient 0/3`
    at every boundary anyway — the narrowed guard only accepted "skip"
    immediately after the separator, and natural phrasing put the subject
    clause in between and used the past tense.
    """
    a, b = _orient_files(tmp_path)
    _boot_score(tmp_path, [a, b])
    (tmp_path / hooks.CARD_NAME).write_text(
        "## Now\n"
        "orientation: AGENTS.md skim skipped — assuming prior knowledge "
        "(contract carried in playbook + continuity; surface pages "
        "injected this wake)\n",
        encoding="utf-8",
    )
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    out, _ = hooks.run_hook(
        hooks.PHASE_POST_TOOL, "{}", _orient_env(tmp_path)
    )
    assert "orient" not in _inject_text(out)


def test_orient_skip_field_prefix_still_rejects_a_negated_value(tmp_path):
    """The widened form must not reopen the reporting-its-own-value trap.

    "orientation: not skipped" is the field-prefixed idiom's own negation —
    the same class #614 narrowed the original regex against, one clause
    shape over. It must keep the meter rendering.
    """
    a, b = _orient_files(tmp_path)
    _boot_score(tmp_path, [a, b])
    (tmp_path / hooks.CARD_NAME).write_text(
        "## Now\norientation: not skipped, read AGENTS.md and the plan\n",
        encoding="utf-8",
    )
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    out, _ = hooks.run_hook(
        hooks.PHASE_POST_TOOL, "{}", _orient_env(tmp_path)
    )
    assert "orient 0/2" in _inject_text(out)


def test_orient_is_unassertable_without_an_armed_boot_score(tmp_path):
    a, b = _orient_files(tmp_path)
    _boot_score(tmp_path, [a, b])  # on disk, but the daemon never armed it
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    unarmed, _ = hooks.run_hook(
        hooks.PHASE_POST_TOOL, _read_batch(a), _env(tmp_path)
    )
    assert "orient" not in _inject_text(unarmed)
    # Positive twin: the identical input with the env armed does render —
    # so the absence above is the guard's, not the fixture's.
    _portal(tmp_path, token="t2", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    armed, _ = hooks.run_hook(
        hooks.PHASE_POST_TOOL, _read_batch(a), _orient_env(tmp_path)
    )
    assert "orient 1/2" in _inject_text(armed)


def test_orient_prunes_state_paths_that_left_the_set(tmp_path):
    a, b = _orient_files(tmp_path)
    _boot_score(tmp_path, [a, b])
    # A stale ledger entry from a path no longer in the set (say, a prior
    # run's state file surviving into a re-run) must never inflate the count.
    (tmp_path / hooks.HOOK_STATE_NAME).write_text(
        json.dumps({hooks.ORIENTATION_READ_KEY: ["/elsewhere/gone.md"]}),
        encoding="utf-8",
    )
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    out, _ = hooks.run_hook(
        hooks.PHASE_POST_TOOL, "{}", _orient_env(tmp_path)
    )
    assert "orient 0/2" in _inject_text(out)


def test_orient_bar_position_is_after_quota():
    rendered = hooks.format_delta(_bar_payload(), mood="smug_", orient=(3, 5))
    assert rendered.splitlines()[0] == (
        "⌁ 3jy8 │ ⏱ 16/120m │ q S57·W50·F27 │ orient 3/5 │ ▷1 │ rb3h │ "
        "⇡2+3 │ ⚒4 │ mood brnrd smug_ │ card ok"
    )


def test_orient_never_opens_the_bar_on_its_own():
    # The same quiet payload test_post_tool_bar_is_quiet_when_nothing_is_laden
    # pins as None must stay None with an open walk riding along: a meter is
    # not an obligation, and a segment that could keep the bar alive at every
    # boundary would train the exact skimming it measures.
    payload = _bar_payload(
        budget={"elapsed_seconds": 60, "budget_seconds": 7200},
        outbound={"replies_current": 0, "replies_other": 0,
                  "outbound_messages": 0},
        produce={"known": False, "counts": {}},
        resources={},
    )
    assert hooks.format_delta(payload) is None  # the twin that proves quiet
    assert hooks.format_delta(payload, orient=(0, 3)) is None


# ── #616: notices segment and spawn_completed closeout rendering ─────────────


def test_notices_chip_present_at_nonzero_count():
    """!N segment renders when the notices list is non-empty.

    Drive red: comment out the notices chip in _render_bar and confirm this
    fails; restore to keep.
    """
    notices = [{"at": "2026-07-24T03:36:00Z", "text": "reply NOT delivered"}]
    rendered = hooks.format_delta(_bar_payload(notices=notices))
    bar = rendered.splitlines()[0]
    assert "!1" in bar


def test_notices_chip_absent_at_zero():
    """No notices entry means no !N segment — absent at zero.

    Also asserting the fixture is legal at zero (the absence assertion is
    not a time bomb against a payload that could never have notices).
    """
    rendered = hooks.format_delta(_bar_payload())  # no notices key
    assert rendered is not None  # bar renders for other reasons (laden payload)
    bar = rendered.splitlines()[0]
    assert "!" not in bar

    rendered_empty = hooks.format_delta(_bar_payload(notices=[]))
    assert "!" not in (rendered_empty.splitlines()[0] if rendered_empty else "")


def test_notices_chip_position_is_after_produce_before_card():
    """!N appears after produce (⚒) and before card — absent at zero, present
    at 1.  Order: ... │ ⚒N │ !N │ mood ... │ card ok
    """
    notices = [{"at": "2026-07-24T03:36:00Z", "text": "spawn dropped: no inbox"}]
    rendered = hooks.format_delta(_bar_payload(notices=notices), mood="smug_")
    bar = rendered.splitlines()[0]
    # !1 is present
    assert "!1" in bar
    # Order: ⚒ before !1 before mood before card
    assert bar.index("⚒") < bar.index("!1") < bar.index("mood") < bar.index("card ok")


def test_notices_chip_carries_a_discharge_detail_line():
    """#1116 residue: `!N` is an OBLIGATION-class chip like pending events,
    but unlike pending events it rendered bare — no line saying what to do
    about it. A refused outbox file is deleted exactly like an accepted
    one, so `portal-state.json` → `notices` is the only way to recover the
    text; the detail line must say so.
    """
    notices = [{"at": "2026-07-24T03:36:00Z", "text": "reply NOT delivered"}]
    rendered = hooks.format_delta(_bar_payload(notices=notices))
    lines = rendered.splitlines()
    assert "!1" in lines[0]
    detail = "\n".join(lines[1:])
    assert "!1" in detail
    assert "1 directive" in detail
    assert "directives" not in detail  # singular at N=1
    assert "portal-state.json" in detail
    assert "notices" in detail

    notices_plural = notices + [
        {"at": "2026-07-24T03:37:00Z", "text": "spawn dropped: no inbox"}
    ]
    rendered_plural = hooks.format_delta(_bar_payload(notices=notices_plural))
    detail_plural = "\n".join(rendered_plural.splitlines()[1:])
    assert "2 directives" in detail_plural  # plural at N>1


def test_mood_prompt_chip_carries_a_discharge_detail_line():
    """#1116 residue: `mood?` is the other bare OBLIGATION-class chip — the
    blank-mood nudge names the ask but never what silences it. The latch
    itself (fires once, only in `mood`'s absence) is untouched; this only
    checks the accompanying line renders whenever the chip does.
    """
    rendered = hooks.format_delta(_bar_payload(), mood_prompt=True)
    lines = rendered.splitlines()
    assert "mood?" in lines[0]
    detail = "\n".join(lines[1:])
    assert "mood?" in detail
    assert ".mood" in detail
    assert "brnrd emotes" in detail

    # A run already wearing a face never needs the ask — same as the chip
    # itself (`mood` takes the `if` branch, `mood_prompt` never reaches the
    # `elif`) — so no detail line either.
    rendered_with_mood = hooks.format_delta(
        _bar_payload(), mood="smug_", mood_prompt=True
    )
    detail_with_mood = "\n".join(rendered_with_mood.splitlines()[1:])
    assert "mood?" not in detail_with_mood


# ── #1002: a notice carries a `kind`, and only `refused`/`dropped` count ─────


def _kind_notice(text, kind=None, lifetime=None):
    record = {"at": "2026-08-02T20:00:00Z", "text": text}
    if kind is not None:
        record["kind"] = kind
    if lifetime is not None:
        record["lifetime"] = lifetime
    return record


def test_notices_chip_absent_when_every_notice_is_advisory():
    """Seven advisory notices — the measured #1002 shape (seven working
    `note:` files) — must drive no `!N` chip at all.

    Drive red: revert `_notices_chip` to `len(notices)` and this fails,
    reproducing the exact bug the ticket measured (`!7` for zero refusals).
    """
    notices = [
        _kind_notice(f"note: body text ignored — event evt-{i} closed", "advisory")
        for i in range(7)
    ]
    rendered = hooks.format_delta(_bar_payload(notices=notices))
    bar = rendered.splitlines()[0]
    assert "!" not in bar


def test_a_redirected_reply_counts_it_reached_the_wrong_correspondent():
    """`redirected` is not FYI: the reply was delivered to a lane the
    resident did not address.

    Parent review of #1002. The cross-gate redirect in `_drain_outbox` was
    filed `advisory` on the reasoning that nothing was refused or dropped —
    true of the *content* and of the *lifecycle*, false of the *addressing*,
    which is the one of the three `daemon-substrate.md` tells a run to open
    `notices` for: did my addressed reply reach who I addressed? A run that
    reads no `!` believes it did.

    Counting works with no change to the filter, because `_counted_notices`
    excludes only `advisory` rather than enumerating the kinds that count —
    the structural property, not the member list.
    """
    notices = [
        _kind_notice(
            "reply redirected: event evt-9 is owned by gate 'slack', not "
            "reachable from this run — delivered on this run's own gate instead",
            "redirected",
        ),
        _kind_notice("note: body text ignored — event evt-1 closed", "advisory"),
    ]
    rendered = hooks.format_delta(_bar_payload(notices=notices))
    bar = rendered.splitlines()[0]
    assert "!1" in bar


def test_notices_chip_counts_only_the_one_refusal_among_advisories():
    """One real refusal in a pile of six advisories must still read `!1` —
    the whole point of a `kind` is that the pile no longer drowns it."""
    notices = [_kind_notice("spawn refused: environment 'host' is not spawnable", "refused")]
    notices += [
        _kind_notice(f"note: body text ignored — event evt-{i} closed", "advisory")
        for i in range(6)
    ]
    rendered = hooks.format_delta(_bar_payload(notices=notices), mood="smug_")
    bar = rendered.splitlines()[0]
    assert "!1" in bar


def test_legacy_notice_with_no_kind_key_counts_as_refusing():
    """A record with no ``kind`` at all — written by a daemon generation
    before #1002, possibly still sitting in a live ``portal-state.json``
    across a restart — must count. The pessimistic direction: hiding a real
    refusal behind a silently-dropped legacy entry is the worse failure."""
    notices = [_kind_notice("reply text staged undeliverable — no gate owns it")]
    assert "kind" not in notices[0]
    rendered = hooks.format_delta(_bar_payload(notices=notices), mood="smug_")
    bar = rendered.splitlines()[0]
    assert "!1" in bar


def test_advisory_notice_is_readable_but_excluded_from_the_seed_briefing_count(
    tmp_path,
):
    """An advisory notice must still render its text at the seed/stop
    boundary (PR #754's "render, don't just count") while the header count
    — like the bar's `!N` — excludes it from "refused or dropped"."""
    notices = [
        _kind_notice(
            "note: body text ignored — a note closes event evt-y8lx "
            "without speaking; use event: to reply",
            "advisory",
        ),
    ]
    _portal(tmp_path, token="t1", pending=0, notices=notices)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "notices: 0 directive(s) brnrd refused or dropped" in ctx
    assert "+1 advisory" in ctx
    assert "a note closes event evt-y8lx" in ctx


# ── #716: `lifetime` splits standing environmental notices out of `!N` ───────


def test_notices_chip_absent_for_a_standing_notice_alone():
    """A single standing environmental notice (ignored `.brr/config` security
    key, unreachable `runners.md`, ...) must drive no `!N` chip at all — it
    is a fact about the environment, not a refused directive.

    Drive red: drop the `lifetime`-aware exclusion from `_counted_notices`
    and this fails, reproducing #716's measured `!1` baseline for a notice
    nothing in the run could have cleared.
    """
    notices = [
        _kind_notice(
            "repo config tried to set security-defining key(s) "
            "['runner_cmd'] in .brr/config — ignored, not honoured",
            "refused",
            "standing",
        ),
    ]
    rendered = hooks.format_delta(_bar_payload(notices=notices))
    bar = rendered.splitlines()[0]
    assert "!" not in bar


def test_notices_chip_reads_the_zero_to_one_transition_not_one_to_two():
    """A standing notice plus one real refusal must read `!1`, not `!2` —
    the exact transition #716 exists to restore. This is the case the whole
    ticket is about: a fresh refusal must be visible as a delta against
    zero, not against a nonzero environmental baseline."""
    notices = [
        _kind_notice(
            "custom runner profiles are not being read in this worktree",
            "dropped",
            "standing",
        ),
        _kind_notice(
            "spawn refused: environment 'host' is not spawnable",
            "refused",
            "run",
        ),
    ]
    rendered = hooks.format_delta(_bar_payload(notices=notices))
    bar = rendered.splitlines()[0]
    assert "!1" in bar
    assert "!2" not in bar


def test_legacy_notice_with_no_lifetime_key_counts_as_refusing():
    """A record carrying `kind` but no `lifetime` at all — written by a
    daemon generation before #716 — must still count. Same pessimistic
    direction as the missing-`kind` legacy case: a real refusal hidden by
    an under-count costs more than a stale standing notice over-counted."""
    notices = [_kind_notice("spawn dropped: no inbox to queue into", "dropped")]
    assert "lifetime" not in notices[0]
    rendered = hooks.format_delta(_bar_payload(notices=notices))
    bar = rendered.splitlines()[0]
    assert "!1" in bar


def test_seed_briefing_renders_the_standing_notice_separately_from_advisory(
    tmp_path,
):
    """The seed/stop briefing must still render a standing notice's text
    (PR #754's "render, don't just count") and must label it `standing`,
    not fold it into the `advisory` count — an operator reading `advisory`
    about an ignored security key learns the wrong severity."""
    notices = [
        _kind_notice(
            "repo-side runner profile file(s) .brr/runners.md — ignored, "
            "not loaded",
            "refused",
            "standing",
        ),
    ]
    _portal(tmp_path, token="t1", pending=0, notices=notices)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "notices: 0 directive(s) brnrd refused or dropped" in ctx
    assert "+1 standing" in ctx
    assert "advisory" not in ctx
    assert ".brr/runners.md" in ctx


def test_card_chip_meters_the_projection_not_the_file():
    """The chip that said `card ok` all the way through #685.

    Positive control first: a card far over the *file* cap whose ``Now``
    section is small is safe, and must still read `ok` — the chip is not a size
    warning about `.card`. Then the case it exists for: a card comfortably
    under any file cap whose projection oversteps the 4096-char transport.

    The two are ordered this way deliberately. `card ok` on a 30 KB file is the
    assertion that would still pass if the chip were reverted to checking
    non-emptiness; `card cut` on a 4.1 KB one is the assertion that would not.
    """
    cap = card.CARD_TEXT_MAX_CHARS

    big_file_small_now = "## Now\nfine\n\n## Arc\n" + "history. " * 5000
    assert len(big_file_small_now) > 30_000
    chip = hooks._card_chip(
        {"active": True, "text": big_file_small_now}, card_stale=False
    )
    assert chip == "card ok"

    small_file_big_now = "## Now\n" + "y" * (cap + 20)
    assert len(small_file_big_now) < 2 * cap
    chip = hooks._card_chip(
        {"active": True, "text": small_file_big_now}, card_stale=False
    )
    assert chip == f"card cut {cap + 20}>{cap}"

    # The H1 card that started it: identical content, one heading level apart.
    h1 = "# Now\nfine\n\n# Arc\n" + "history. " * 5000
    assert hooks._card_chip({"active": True, "text": h1}, card_stale=False) == "card ok"

    # Unchanged verdicts.
    assert hooks._card_chip({"active": False}, card_stale=False) == "card blank"
    assert hooks._card_chip({"active": True}, card_stale=True) == "card stale"
    # A capsule with no body to measure is not a failure verdict.
    assert hooks._card_chip({"active": True}, card_stale=False) == "card ok"


def test_closeout_excludes_spawn_completed_from_obligation_count():
    """spawn_completed events for the current run are reported as a distinct
    fact at closeout — not counted in the obligation total, not listed under
    'Address each'.

    Drive red: remove the finished_spawns partitioning in format_delta and
    confirm spawn_completed appears in the 'N pending event(s)' count and
    'Address each' detail; restore to keep.
    """
    run_id = "run-260724-0336-u6pi"
    payload = {
        "run": {"id": run_id},
        "attention": {"pending_event_count": 1, "pending_outbox_file_count": 0},
        "inbound": {
            "current_event": "evt-parent",
            "current_event_replyable": True,
            "events": [
                {
                    "id": "evt-spawn-done",
                    "source": "spawn_completed",
                    "spawn_parent_run_id": run_id,
                    # Structured, not only in the prose: since #730 a spawn
                    # that reached a terminal state always carries this, so a
                    # fixture asserting the healthy wording has to look like
                    # what production now emits. Without it this is an
                    # outcome-never-determined event, which is exactly the
                    # case that must not read as "no address needed".
                    "spawn_status": "done",
                    "summary": "concurrent spawn run-child done: status=done",
                },
            ],
        },
        "outbound": {"replies_current": 1, "replies_other": 0, "outbound_messages": 0,
                     "any_sent": True},
        "budget": {"elapsed_seconds": 10, "budget_seconds": 3600},
        "notices": [],
    }
    rendered = hooks.format_delta(payload, stop=True)
    # The header must show 0 obligation events, not 1.
    assert "0 pending event(s)" in rendered
    # Must NOT demand address.
    assert "Address each" not in rendered
    # spawn_completed must appear as a distinct fact line, not an obligation.
    assert "1 finished spawn(s) observed" in rendered
    assert "no address needed" in rendered
    # The event id should appear (visibility constraint #1).
    assert "evt-spawn-done" in rendered


def test_closeout_still_shows_action_events_alongside_finished_spawns():
    """When real pending events coexist with spawn_completed, only the real
    ones appear in the 'Address each' obligation block."""
    run_id = "run-abc"
    payload = {
        "run": {"id": run_id},
        "attention": {"pending_event_count": 2, "pending_outbox_file_count": 0},
        "inbound": {
            "current_event": "evt-lead",
            "current_event_replyable": True,
            "events": [
                {
                    "id": "evt-followup",
                    "source": "telegram",
                    "spawn_parent_run_id": None,
                    "summary": "a real follow-up",
                },
                {
                    "id": "evt-done",
                    "source": "spawn_completed",
                    "spawn_parent_run_id": run_id,
                    "summary": "child done",
                },
            ],
        },
        "outbound": {"replies_current": 1, "replies_other": 0, "outbound_messages": 0},
        "budget": {"elapsed_seconds": 10, "budget_seconds": 3600},
    }
    rendered = hooks.format_delta(payload, stop=True)
    # 1 real obligation, not 2.
    assert "1 pending event(s)" in rendered
    assert "Address each" in rendered
    assert "evt-followup" in rendered
    # Finished spawn reported separately.
    assert "1 finished spawn(s)" in rendered
    assert "evt-done" in rendered


def test_closeout_names_live_children_handed_to_next_run():
    run_id = "run-parent"
    payload = {
        "run": {"id": run_id},
        "attention": {"pending_event_count": 0, "pending_outbox_file_count": 0},
        "inbound": {"events": []},
        "resources": _coexisting(
            status="known",
            owned_children=[
                {"run_id": "run-child-a", "parent_run_id": run_id},
                {"event_id": "evt-child-b", "parent_run_id": run_id, "run_id": ""},
            ],
        ),
        "outbound": {"replies_current": 1, "replies_other": 0, "outbound_messages": 0},
        "budget": {"elapsed_seconds": 10, "budget_seconds": 3600},
    }
    assert payload["resources"]["coexisting_runs"]["owned_children"][1]["parent_run_id"] == run_id, (
        "fixture must carry this run's live children or the handover line "
        "below would pass over someone else's edges"
    )
    rendered = hooks.format_delta(payload, stop=True)
    assert "2 child run(s) still live" in rendered
    assert "evt-child-b" in rendered
    assert "run-child-a" in rendered
    assert "passes to the next run on this thread" in rendered


def test_finished_spawn_outcomes_render_identically_at_both_boundaries():
    """The bar and seed/closeout prose share one outcome-aware fact line."""
    run_id = "run-parent"
    cases = [
        (
            [{
                "id": "evt-ok", "source": "spawn_completed",
                "spawn_parent_run_id": run_id, "spawned_by_run": "run-ok",
                "spawn_status": "done",
            }],
            "- ▷ 1 finished spawn(s) observed — no address needed; "
            "will retire at run end.",
        ),
        (
            [{
                "id": "evt-error", "source": "spawn_completed",
                "spawn_parent_run_id": run_id, "spawned_by_run": "run-error",
                "spawn_status": "error",
            }],
            "- ▷ 1 finished spawn(s) — 1 error (run-error).",
        ),
        (
            [
                {
                    "id": "evt-ok", "source": "spawn_completed",
                    "spawn_parent_run_id": run_id, "spawned_by_run": "run-ok",
                    "spawn_status": "done",
                },
                {
                    "id": "evt-error", "source": "spawn_completed",
                    "spawn_parent_run_id": run_id,
                    "spawned_by_run": "run-error", "spawn_status": "error",
                },
            ],
            "- ▷ 2 finished spawn(s) — 1 ok, 1 error (run-error).",
        ),
        (
            [{
                "id": "evt-no-report", "source": "spawn_completed",
                "spawn_parent_run_id": run_id,
                "spawned_by_run": "run-no-report", "spawn_status": "done",
                "spawn_report_found": False,
            }],
            "- ▷ 1 finished spawn(s) — 1 ok "
            "(run-no-report; no report written).",
        ),
        # An event with no ``spawn_status`` is the case this line exists for:
        # an outcome that was never determined. It must NOT take the all-clear
        # branch — "no address needed" is itself a claim about outcome, and
        # restating it here would reproduce #730 in the one place nobody
        # drives. Reachable in practice: every completion event minted before
        # this shipped carries no status, and daemon.py is restart-only.
        (
            [{
                "id": "evt-legacy", "source": "spawn_completed",
                "spawn_parent_run_id": run_id,
                "spawned_by_run": "run-legacy",
            }],
            "- ▷ 1 finished spawn(s) — 1 status unknown (run-legacy).",
        ),
        # Parts sum to the whole: a known-good spawn beside an undetermined
        # one accounts for both, rather than suppressing the "ok" term.
        (
            [
                {
                    "id": "evt-ok", "source": "spawn_completed",
                    "spawn_parent_run_id": run_id, "spawned_by_run": "run-ok",
                    "spawn_status": "done",
                },
                {
                    "id": "evt-unknown", "source": "spawn_completed",
                    "spawn_parent_run_id": run_id,
                    "spawned_by_run": "run-unknown",
                },
            ],
            "- ▷ 2 finished spawn(s) — 1 ok, 1 status unknown (run-unknown).",
        ),
    ]

    for events, expected in cases:
        payload = _bar_payload(
            run={"id": run_id},
            attention={
                "pending_event_count": len(events),
                "pending_outbox_file_count": 0,
            },
            inbound={"events": events},
        )
        for stop in (False, True):
            rendered = hooks.format_delta(payload, stop=stop)
            fact_lines = [
                line for line in rendered.splitlines()
                if line.startswith("- ▷ ")
            ]
            assert fact_lines == [expected], f"stop={stop}, events={events}"


def test_spawn_completed_for_different_run_still_counts_as_obligation():
    """A spawn_completed whose spawn_parent_run_id doesn't match the current
    run is NOT a fact-for-this-run — it counts as a normal obligation.

    This asserts the fixture stays illegal (spawn_completed for a different
    parent cannot be mistaken for the current run's child) so the absence
    assertion below is not a time bomb.
    """
    my_run_id = "run-me"
    other_run_id = "run-other"
    payload = {
        "run": {"id": my_run_id},
        "attention": {"pending_event_count": 1, "pending_outbox_file_count": 0},
        "inbound": {
            "current_event": "evt-lead",
            "current_event_replyable": True,
            "events": [
                {
                    "id": "evt-wrong-parent",
                    "source": "spawn_completed",
                    "spawn_parent_run_id": other_run_id,
                    "summary": "someone else's child done",
                },
            ],
        },
        "outbound": {"replies_current": 0, "replies_other": 0, "outbound_messages": 0},
        "budget": {"elapsed_seconds": 10, "budget_seconds": 3600},
    }
    # The fixture's spawn_parent_run_id (other) != my_run_id — that's the
    # condition this test asserts the code respects.
    assert payload["inbound"]["events"][0]["spawn_parent_run_id"] != my_run_id, (
        "fixture must carry a different parent id or the absence assertion below "
        "cannot distinguish a bug from a correct exclusion"
    )
    rendered = hooks.format_delta(payload, stop=True)
    # Treated as a regular obligation — NOT excluded from the count.
    assert "1 pending event(s)" in rendered
    assert "Address each" in rendered
    # Not classified as a finished spawn.
    assert "finished spawn" not in rendered


# ── notices: the seed/stop briefing spells the text, not just the count ──────
#
# The count already had a home (the ``!N`` bar segment, #616). What did not:
# ``notices`` was read on the seed/stop path and then never rendered, so the
# two verbose boundaries — including Stop, the last one at which a dropped
# reply can still be re-routed — said nothing about a refusal at all. A
# refused outbox file is deleted exactly like an accepted one, so the notice
# is the only trace there is.


def _notice(text):
    return {"at": "2026-07-25T20:47:00Z", "text": text}


def test_stop_spells_out_notice_text(tmp_path):
    """Drive red: delete the notices block in ``format_context`` — the count
    still renders on the bar and this test still fails, which is the point."""
    _portal(
        tmp_path, token="t1", pending=0,
        notices=[_notice(
            "event evt-y8lx retired done; reply text staged undeliverable — "
            "no gate owns dispatch_message events; route via gate:<name> if "
            "a person must read it"
        )],
    )
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "notices: 1 directive(s) brnrd refused or dropped" in ctx
    assert "no gate owns dispatch_message events" in ctx
    # Stop is the last re-route boundary and says so; seed does not.
    assert "last boundary that can re-route one" in ctx


def test_seed_spells_out_notice_text_without_the_stop_clause(tmp_path):
    _portal(tmp_path, token="t1", pending=0,
            notices=[_notice("spawn dropped: no inbox to queue into")])
    out, _ = hooks.run_hook(hooks.PHASE_SESSION_START, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "spawn dropped: no inbox to queue into" in ctx
    assert "last boundary that can re-route one" not in ctx


def test_no_notices_stays_silent(tmp_path):
    """Absent at zero, exactly like the chip and the scm line — or the block
    becomes the permanent line that teaches the reader to skip the channel."""
    _portal(tmp_path, token="t1", pending=0, notices=[])
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    assert "notices:" not in out["hookSpecificOutput"]["additionalContext"]


def test_post_tool_still_uses_the_chip_not_the_prose(tmp_path):
    """The mid-run boundary keeps the cheap count; spelling four notices out
    at every tool call is the churn the bar exists to avoid."""
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}],
            notices=[_notice(
                "event evt-tick retired done; reply text staged undeliverable — "
                "no gate owns schedule events"
            )])
    out, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "refused or dropped" not in ctx
    assert "!1" in ctx


def test_notice_overflow_is_counted_not_silently_dropped(tmp_path):
    """A cap that reads as "that was all of them" is the failure this guards:
    the same silent-truncation class the notices themselves are about."""
    _portal(tmp_path, token="t1", pending=0,
            notices=[_notice(f"dropped directive {i}") for i in range(7)])
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "notices: 7 directive(s)" in ctx
    # newest kept, oldest summarised
    assert "dropped directive 6" in ctx
    assert "dropped directive 0" not in ctx
    assert "(+3 older" in ctx


def test_long_notice_text_is_truncated_with_an_ellipsis(tmp_path):
    _portal(tmp_path, token="t1", pending=0,
            notices=[_notice("x" * 400)])
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "…" in ctx
    assert "x" * 400 not in ctx


# ── The wake census (#739, the reading half of #683) ─────────────────────
#
# Same fixture discipline as the orientation ledger above (#611): every
# assertion of absence sits beside a positive twin on the same input shape,
# so "no census rendered" can never be green against an input that could not
# have produced one.


def _census_score(tmp_path, *, contracts, prompt_bytes=None):
    """A `boot-score.json` carrying only what the census reads.

    Deliberately not a full score: the census must work off whatever the
    daemon wrote, including an older daemon's partial one.
    """
    payload = {"contracts": contracts}
    if prompt_bytes is not None:
        payload["prompt_bytes"] = prompt_bytes
    path = tmp_path / "boot-score.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


_CENSUS_BLOCKS = [
    {"block_key": "work-surface", "label": "Discovered work surface",
     "bytes": 24716, "present": True, "oldest_item": "2026-07-25 21:18"},
    {"block_key": "dominion", "label": "Dominion digest",
     "bytes": 21521, "present": True, "oldest_item": None},
    {"block_key": "weave", "label": "Working register",
     "bytes": 5524, "present": True, "oldest_item": "2026-07-26 09:00"},
    # Present in scope, silent in this wake: must not win `top` on a stale
    # byte count from a block that never rendered.
    {"block_key": "diffense", "label": "Diffense pack",
     "bytes": 99999, "present": False, "oldest_item": None},
]


def test_census_renders_total_biggest_block_and_oldest_item(tmp_path):
    _census_score(tmp_path, contracts=_CENSUS_BLOCKS, prompt_bytes=115714)
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    out, code = hooks.run_hook(
        hooks.PHASE_POST_TOOL, "{}", _orient_env(tmp_path)
    )
    assert code == 0
    bar = _inject_text(out).splitlines()[0]
    assert "wake 113.0 KB" in bar
    assert "top work-surface 24.1 KB" in bar
    # The oldest item across every measured block, not the top block's own.
    assert "oldest 2026-07-25" in bar


def test_census_is_silent_when_the_score_carries_no_contracts(tmp_path):
    """An older daemon's score, or none at all, renders nothing — not a zero.

    Positive twin: the same portal input with a contracts-bearing score
    renders the segment (asserted above), so this absence is the score's,
    not the boundary's.
    """
    _boot_score(tmp_path, _orient_files(tmp_path))  # orientation_set only
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    out, _ = hooks.run_hook(
        hooks.PHASE_POST_TOOL, "{}", _orient_env(tmp_path)
    )
    assert "wake " not in _inject_text(out)


def test_census_fields_degrade_one_at_a_time(tmp_path):
    """A partial score still renders what it measured.

    One absent field silencing the line would hide the census exactly when
    the score is partial — which is when it is most worth reading.
    """
    blocks = [
        {"block_key": "run-context-bundle", "bytes": 19789, "present": True},
    ]
    _census_score(tmp_path, contracts=blocks)  # no prompt_bytes, no oldest
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    out, _ = hooks.run_hook(
        hooks.PHASE_POST_TOOL, "{}", _orient_env(tmp_path)
    )
    bar = _inject_text(out).splitlines()[0]
    assert "top run-context-bundle 19.3 KB" in bar
    assert "wake " not in bar
    assert "oldest" not in bar


def test_census_never_opens_the_bar_on_its_own(tmp_path):
    """A value constant for a whole run must not manufacture a boundary.

    Same rule the orientation meter keeps: the census rides bars the portal
    would have rendered anyway. Positive twin: the identical score with one
    pending event renders it (asserted above).
    """
    _census_score(tmp_path, contracts=_CENSUS_BLOCKS, prompt_bytes=115714)
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(
        hooks.PHASE_POST_TOOL, "{}", _orient_env(tmp_path)
    )
    assert "wake " not in _inject_text(out)


# ── Boundary transcript ──────────────────────────────────────────────────
#
# The wake capture (`prompt.md`) has always been written and the boundaries
# never were, so the only inspectable record of a run's environment stopped at
# t=0. These pin the other half.


def _transcript_env(tmp_path):
    """An env whose run directory is *not* the outbox directory.

    Deliberately distinct: the transcript is anchored on ``BRR_BOOT_SCORE``'s
    parent so it lands beside ``prompt.md``, and a fixture that collapses the
    two paths cannot tell a correct implementation from one that writes into
    the outbox (where the daemon's drain and the closeout capture would both
    see a file they know nothing about).
    """
    run_dir = tmp_path / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    env = _env(tmp_path)
    env["BRR_BOOT_SCORE"] = str(run_dir / "boot-score.json")
    return env, run_dir


def _transcript(run_dir):
    path = run_dir / "boundaries.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_boundary_transcript_records_the_injection_beside_the_wake(tmp_path):
    env, run_dir = _transcript_env(tmp_path)
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    out, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)

    records = _transcript(run_dir)
    assert len(records) == 1
    assert records[0]["phase"] == hooks.PHASE_POST_TOOL
    # The recorded text is the text the runner actually received — compared
    # against the rendered native output, not against a second rendering.
    assert records[0]["inject"] == _inject_text(out)
    assert records[0]["at"].endswith("Z")


def test_a_silent_boundary_is_still_recorded(tmp_path):
    """A fired-but-silent hook is a result, not a gap.

    Most post-tool boundaries inject nothing (the change token has not moved),
    and a transcript that only logged the loud ones would report a run as
    having far fewer boundaries than it had — which is the exact reading error
    someone reasoning about the environment from this file would make.
    """
    env, run_dir = _transcript_env(tmp_path)
    # A payload with nothing owed, so the second boundary is genuinely
    # silent. Since #1116 a *pending* one is not: an obligation repeats
    # until discharged, by design, so it can no longer serve as the fixture
    # for silence. What this test is for — a fired-but-silent hook is a
    # recorded result, not a gap — is unchanged.
    _portal(
        tmp_path, token="t1", pending=0, events=[],
        resources={"quota": {"status": "known", "summary": "session 80% left"}},
    )
    hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)

    records = _transcript(run_dir)
    assert len(records) == 2
    assert records[0]["inject"]
    # Second fire: same token, nothing new to say.
    assert records[1]["inject"] is None
    assert records[1]["phase"] == hooks.PHASE_POST_TOOL


def test_boundary_transcript_records_a_block_and_its_reason(tmp_path):
    env, run_dir = _transcript_env(tmp_path)
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "body": "one more thing"}])
    hooks.run_hook(hooks.PHASE_STOP, "{}", env)

    record = _transcript(run_dir)[-1]
    assert record["block"] is True
    assert "one more thing" in record["block_reason"]


def test_no_run_directory_records_nothing_and_does_not_raise(tmp_path):
    """No ``BRR_BOOT_SCORE`` (an older daemon, an ad-hoc hook run) is silent.

    Same doctrine as every other optional handle in ``HookContext``: an
    unassertable fact produces no artifact rather than a guessed location.
    """
    env = _env(tmp_path)  # no BRR_BOOT_SCORE
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    out, code = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)

    assert code == 0
    assert _inject_text(out)  # the hook still did its actual job
    assert not (tmp_path / "boundaries.jsonl").exists()
    assert not list(tmp_path.glob("**/boundaries.jsonl"))


def test_the_transcript_cap_announces_itself(tmp_path, monkeypatch):
    """Past the cap the file stops growing — and says so on its last line.

    A transcript that silently stops is byte-identical to a run that went
    quiet, which is the reading this file exists to make impossible.
    """
    env, run_dir = _transcript_env(tmp_path)
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    monkeypatch.setattr(hooks, "_BOUNDARIES_MAX_BYTES", 200)

    for _ in range(6):
        hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)

    records = _transcript(run_dir)
    assert records[-1].get("truncated") is True
    assert "capped" in records[-1]["inject"]
    size = (run_dir / "boundaries.jsonl").stat().st_size
    # One over-cap line is written to carry the notice; nothing after it.
    before = len(records)
    hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    assert len(_transcript(run_dir)) == before
    assert (run_dir / "boundaries.jsonl").stat().st_size == size


# ── Boundary summary (`boundaries.json`) ──────────────────────────────────
#
# `derive_boundaries_summary` reads the exact transcript `record_boundary`
# writes, so these fixtures are driven through the real hook entry point
# (`hooks.run_hook`) rather than hand-assembled — the same discipline the
# transcript tests above already keep. Only the malformed-line case appends
# synthetic damage on top of a real file, because that damage cannot be
# produced any other way.


def test_derive_boundaries_summary_is_none_when_the_file_is_absent(tmp_path):
    """No transcript, no summary — never a guessed zero-valued one."""
    assert hooks.derive_boundaries_summary(tmp_path / "boundaries.jsonl") is None


def test_derive_boundaries_summary_is_none_when_every_line_is_malformed(tmp_path):
    """Total corruption reads the same as absence, not as a clean zero run."""
    path = tmp_path / "boundaries.jsonl"
    path.write_text(
        "not json at all\n{\"phase\": \"stop\"\n{\"no_phase_field\": true}\n",
        encoding="utf-8",
    )
    assert hooks.derive_boundaries_summary(path) is None


def test_derive_boundaries_summary_counts_stops_and_skips_bad_lines(tmp_path):
    """Realistic transcript, driven through `run_hook`, plus damage on top."""
    env, run_dir = _transcript_env(tmp_path)
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "body": "one more thing"}])
    hooks.run_hook(hooks.PHASE_SESSION_START, "{}", env)
    hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    hooks.run_hook(hooks.PHASE_STOP, "{}", env)  # blocks: pending event unaddressed
    # A second Stop against the same unresolved snapshot does not re-block
    # (the token-scoped latch, #981) — this run's last word is clean.
    hooks.run_hook(hooks.PHASE_STOP, "{}", env)

    path = run_dir / "boundaries.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not valid json\n")

    summary = hooks.derive_boundaries_summary(path)

    assert summary is not None
    assert summary["total"] == 4
    assert summary["skipped"] == 1
    assert summary["stops"] == 2
    assert summary["guard_fire_count"] == 1
    assert summary["guard_fires"][0]["blocked"] is True
    assert summary["guard_fires"][0]["at"]
    # The run's last word was clean, even though an earlier Stop blocked.
    assert summary["final_stop_block"] is False
    assert summary["final_stop_block_reason"] is None
    assert summary["final_stop_at"]


def test_derive_boundaries_summary_final_stop_blocked_is_the_case_that_matters(tmp_path):
    """A run whose *last* Stop was still live under a block — the accepted defect."""
    env, run_dir = _transcript_env(tmp_path)
    _portal(tmp_path, token="t1", pending=1,
            events=[{"id": "evt-2", "source": "telegram", "body": "finish this"}])
    hooks.run_hook(hooks.PHASE_STOP, "{}", env)

    summary = hooks.derive_boundaries_summary(run_dir / "boundaries.jsonl")

    assert summary["stops"] == 1
    assert summary["guard_fire_count"] == 1
    assert summary["final_stop_block"] is True
    assert "finish this" in summary["final_stop_block_reason"]


# ── #1113: a row that asks for a reply nobody receives ──────────────────────


def test_a_daemon_minted_event_says_a_reply_reaches_nobody():
    """The instruction above the rows says "address each with an `event:`
    reply". For a `spawn_completed` or a `schedule` firing that reply is
    accepted, clears the event, and is delivered **nowhere** — the run
    learns it from a "NOT delivered" notice, after the decision.

    Observed live in the run that wrote this: a `⚙ spawn_completed` row
    sitting under the identical generic instruction as a correspondent's
    message.
    """
    minted = {"id": "evt-1", "source": "spawn_completed", "created": None}
    letter = {"id": "evt-2", "source": "telegram", "created": None}

    assert "no correspondent" in hooks._event_header(minted, size=10)
    assert "`note:`" in hooks._event_header(minted, size=10)
    assert "no correspondent" not in hooks._event_header(letter, size=10)


def test_the_gateless_set_agrees_with_the_daemons():
    """The two predicates are derived from different places and must agree.

    `hooks` asks `protocol.INTERNAL_SOURCES` ("brnrd mints this"); the
    daemon asks `_gate_owns_source` ("some gate delivers this"). They are
    the same class read from opposite ends, and #1118 is what happens when
    one list drifts from the thing it claims to describe — so this asserts
    they never do, over the union of both vocabularies.
    """
    from brr import daemon, protocol
    from brr.gates import BUILTIN_GATES

    for source in set(protocol.INTERNAL_SOURCES) | set(BUILTIN_GATES):
        assert hooks._reaches_nobody(source) == (
            not daemon._gate_owns_source(source)
        ), source
# ── #1116: the typed channel ────────────────────────────────────────────────


def test_every_bar_segment_declares_its_class():
    """The class is structural, not a list (#1118's lesson, applied).

    `_BarSegment.klass` has no default, so a segment added later has to
    answer *"what turns this off?"* rather than inherit an answer — and the
    cheap inherited answer would be AMBIENT, which is exactly how a new
    obligation would go silent on the boundary it exists for.
    """
    for segment in hooks.BAR_SEGMENTS:
        assert segment.klass in hooks._SEGMENT_CLASSES, segment.key
    # And every key the renderer can emit is classified, including the
    # inline `✉?` chip that has no vocabulary entry.
    assert set(hooks.SEGMENT_CLASS) >= {s.key for s in hooks.BAR_SEGMENTS}
    assert hooks.SEGMENT_CLASS["pending_unknown"] == hooks.OBLIGATION


def test_a_quiet_boundary_keeps_the_vitals_and_drops_the_wallpaper():
    """The maintainer's rule, rendered.

    *Every notification is actionable and turn-off-able — unless it is a
    necessary tick update: cost, execution time, spawns.* So a boundary
    with no ambient news still carries cost and quota (VITAL: no
    discharge, and losing them would cost the cost-awareness the whole
    exemption exists for) and drops the ones that are neither news nor
    obligation.
    """
    payload = _bar_payload(
        budget={"elapsed_seconds": 60, "budget_seconds": 7200},
        resources={"quota": {"status": "known", "summary": "session 80% left"}},
        outbound={"replies_current": 0, "replies_other": 0,
                  "outbound_messages": 0},
        produce={"known": False, "counts": {}},
    )
    loud = hooks.format_delta(payload, orient=(1, 3), census="wake 40 KB",
                              ambient_emit=True)
    quiet = hooks.format_delta(payload, orient=(1, 3), census="wake 40 KB",
                               ambient_emit=False)

    assert loud is not None and quiet is not None
    # Vitals survive: this is the exemption, and it is the difference
    # between cost-aware navigation and a number you see twice a run.
    assert "⏱" in quiet and "q S" in quiet
    # Obligations survive.
    assert "orient 1/3" in quiet
    # Wallpaper does not: a run-long constant is neither news nor a debt.
    assert "wake 40 KB" in loud
    assert "wake 40 KB" not in quiet


# ── The in-process subagent boundary (#1095) ─────────────────────────────
#
# The payloads below are *captured*, not invented: dumped from live claude
# `PostToolBatch` hook fires on 2026-08-05 (run-260805-1041-c1b3), one from
# the resident's own boundary and one from an `Agent`-tool subagent's, with
# the ids shortened. They are the evidence the discriminator rests on — every
# other field, `session_id` and `transcript_path` included, was byte-identical
# across the two, because the child shares the parent's session.

_PARENT_PAYLOAD = {
    "cwd": "/home/gurio/src/misc/brr",
    "effort": {"level": "high"},
    "hook_event_name": "PostToolBatch",
    "permission_mode": "bypassPermissions",
    "prompt_id": "e738ebed-c29b-4e74-9397-fb9d3a28f305",
    "session_id": "46435b5d-b82e-4fb6-9a2c-b3f382c0bd44",
    "tool_calls": [],
    "transcript_path": "/home/gurio/.claude/projects/x/46435b5d.jsonl",
}

_SUBAGENT_PAYLOAD = {
    "agent_id": "aaa08adde62eae409",
    "agent_type": "Explore",
    "cwd": "/home/gurio/src/misc/brr",
    "hook_event_name": "PostToolBatch",
    "permission_mode": "bypassPermissions",
    "prompt_id": "e738ebed-c29b-4e74-9397-fb9d3a28f305",
    "session_id": "46435b5d-b82e-4fb6-9a2c-b3f382c0bd44",
    "tool_calls": [],
    "transcript_path": "/home/gurio/.claude/projects/x/46435b5d.jsonl",
}


def _run_env(tmp_path):
    """`_env` plus a run directory — the latch and transcript need one."""
    env = _env(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir(exist_ok=True)
    env["BRR_BOOT_SCORE"] = str(run_dir / "boot-score.json")
    return env


def test_the_resident_is_never_mistaken_for_a_child(tmp_path):
    """The discriminator, against both captured payloads.

    The expensive failure is the *false positive*: a resident boundary read as
    a subagent's would starve the run of its own correspondence silently, and
    a starved resident renders identically to a quiet one.
    """
    assert hooks.subagent_identity(_PARENT_PAYLOAD) is None
    assert hooks.subagent_identity({}) is None
    assert hooks.subagent_identity({"tool_calls": []}) is None

    identity = hooks.subagent_identity(_SUBAGENT_PAYLOAD)
    assert identity == {
        "agent_id": "aaa08adde62eae409",
        "agent_type": "Explore",
    }


def test_a_subagent_boundary_carries_none_of_the_parents_correspondence(tmp_path):
    """#1095: found live when a worker read the maintainer's chat messages."""
    _portal(tmp_path, token="t1", pending=1, events=[{
        "id": "evt-2", "source": "telegram", "summary": "merge it",
        "body": "merge the PR and deploy the batch",
        "cloud_user": "Gurio", "cloud_username": "StasisRush",
    }])
    env = _run_env(tmp_path)

    parent, _ = hooks.run_hook(
        hooks.PHASE_POST_TOOL, json.dumps(_PARENT_PAYLOAD), env)
    parent_text = parent["hookSpecificOutput"]["additionalContext"]
    assert "merge the PR and deploy the batch" in parent_text
    assert "evt-2" in parent_text

    child, code = hooks.run_hook(
        hooks.PHASE_POST_TOOL, json.dumps(_SUBAGENT_PAYLOAD), env)
    assert code == 0
    child_text = child["hookSpecificOutput"]["additionalContext"]
    # Not the message, not the sender, not the event, not the obligation.
    for leak in ("merge the PR", "evt-2", "Gurio", "StasisRush",
                 "pending", "event:", "note:"):
        assert leak not in child_text, leak
    # What it does carry is itself.
    assert "Explore" in child_text
    assert "run-1" in child_text


def test_a_subagent_boundary_never_spends_the_parents_state(tmp_path):
    """A limb reads and writes none of the dispatcher's memory.

    The seen-ledger, the once-per-run latches and the change token are all
    per-*run* memory held in one file. A child consuming them would suppress
    the parent's next injection — the same defect one layer quieter than the
    leak.
    """
    _portal(tmp_path, token="t1", pending=1, events=[{
        "id": "evt-2", "source": "telegram", "summary": "hi", "body": "hi",
    }])
    env = _run_env(tmp_path)

    hooks.run_hook(hooks.PHASE_POST_TOOL, json.dumps(_PARENT_PAYLOAD), env)
    state_path = tmp_path / hooks.HOOK_STATE_NAME
    before = state_path.read_bytes()
    flush_before = (tmp_path / hooks.FLUSH_SIGNAL_NAME).read_bytes()

    for _ in range(3):
        hooks.run_hook(
            hooks.PHASE_POST_TOOL, json.dumps(_SUBAGENT_PAYLOAD), env)

    assert state_path.read_bytes() == before
    # Nor does it flush the daemon's portal on the parent's behalf.
    assert (tmp_path / hooks.FLUSH_SIGNAL_NAME).read_bytes() == flush_before


def test_the_child_names_itself_once_then_falls_silent(tmp_path):
    """An ambient line that repeats every boundary is the noise class."""
    _portal(tmp_path, token="t1", pending=0)
    env = _run_env(tmp_path)

    first, _ = hooks.run_hook(
        hooks.PHASE_POST_TOOL, json.dumps(_SUBAGENT_PAYLOAD), env)
    assert "subagent" in first["hookSpecificOutput"]["additionalContext"]

    second, _ = hooks.run_hook(
        hooks.PHASE_POST_TOOL, json.dumps(_SUBAGENT_PAYLOAD), env)
    assert second == {} or not second.get("hookSpecificOutput", {}).get(
        "additionalContext")

    # A *different* limb is a different addressee and gets its own greeting.
    sibling = dict(_SUBAGENT_PAYLOAD, agent_id="bbb11ceef", agent_type="Plan")
    other, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, json.dumps(sibling), env)
    assert "Plan" in other["hookSpecificOutput"]["additionalContext"]


def test_a_child_can_never_be_blocked_at_the_parents_closeout(tmp_path):
    """A child owes the parent's correspondents nothing, so it cannot be held."""
    _portal(tmp_path, token="t1", pending=2,
            events=[{"id": "evt-2", "source": "telegram", "summary": "hi"}])
    env = _run_env(tmp_path)

    child, code = hooks.run_hook(
        hooks.PHASE_STOP, json.dumps(_SUBAGENT_PAYLOAD), env)
    assert code == 0
    assert child.get("decision") != "block"

    # And the parent's own block is still live — the child did not spend it.
    parent, _ = hooks.run_hook(
        hooks.PHASE_STOP, json.dumps(_PARENT_PAYLOAD), env)
    assert parent["decision"] == "block"


def test_a_childs_boundary_is_tagged_and_left_out_of_the_runs_verdict(tmp_path):
    """`boundaries.json` answers *did this run end over an objection*."""
    _portal(tmp_path, token="t1", pending=0)
    env = _run_env(tmp_path)
    run_dir = tmp_path / "run"

    hooks.run_hook(hooks.PHASE_STOP, json.dumps(_PARENT_PAYLOAD), env)
    hooks.run_hook(hooks.PHASE_STOP, json.dumps(_SUBAGENT_PAYLOAD), env)

    transcript = run_dir / hooks.BOUNDARIES_NAME
    records = [json.loads(line) for line in
               transcript.read_text(encoding="utf-8").splitlines() if line]
    tagged = [r for r in records if r.get("subagent")]
    assert len(tagged) == 1
    assert tagged[0]["subagent"]["agent_type"] == "Explore"

    summary = hooks.derive_boundaries_summary(transcript)
    # The child's Stop is later in file order; it must not become the run's.
    assert summary["stops"] == 1
    assert summary["total"] == len(records) - 1


def _dispatch_payload(n=1):
    return dict(
        _PARENT_PAYLOAD,
        tool_calls=[{"tool_name": "Agent", "tool_input": {}} for _ in range(n)],
    )


def test_a_dispatch_with_no_recognised_child_says_the_seam_may_have_moved(tmp_path):
    """The isolation keys on the Shell's field, so it can be taken away.

    brnrd fails open on purpose. This is the second source that should agree
    with the first: the parent counts its own dispatches, the children leave
    latch files. Disagreement is the only evidence available that the
    discriminator stopped working, and without it the leak returns silently.
    """
    _portal(tmp_path, token="t1", pending=0)
    env = _run_env(tmp_path)

    hooks.run_hook(hooks.PHASE_POST_TOOL, json.dumps(_dispatch_payload(2)), env)
    stop, _ = hooks.run_hook(hooks.PHASE_STOP, json.dumps(_PARENT_PAYLOAD), env)
    text = stop["hookSpecificOutput"]["additionalContext"]
    assert "subagent isolation unverified" in text
    assert "2 subagent(s)" in text
    # Annotation, never a block: the signal is real but not exact.
    assert stop.get("decision") != "block"

    # Once. A warning that repeats every boundary stops being read.
    again, _ = hooks.run_hook(hooks.PHASE_STOP, json.dumps(_PARENT_PAYLOAD), env)
    assert "subagent isolation unverified" not in (
        again.get("hookSpecificOutput", {}).get("additionalContext") or "")


def test_a_recognised_child_clears_the_drift_warning(tmp_path):
    """The healthy case is silent — and must be reachable, or it is a nag."""
    _portal(tmp_path, token="t1", pending=0)
    env = _run_env(tmp_path)

    hooks.run_hook(hooks.PHASE_POST_TOOL, json.dumps(_dispatch_payload(1)), env)
    hooks.run_hook(hooks.PHASE_POST_TOOL, json.dumps(_SUBAGENT_PAYLOAD), env)
    stop, _ = hooks.run_hook(hooks.PHASE_STOP, json.dumps(_PARENT_PAYLOAD), env)
    text = stop.get("hookSpecificOutput", {}).get("additionalContext") or ""
    assert "subagent isolation unverified" not in text


def test_a_run_that_dispatched_nothing_is_never_warned(tmp_path):
    """A guard that fires for a non-reason stops being read by the tenth."""
    _portal(tmp_path, token="t1", pending=0)
    env = _run_env(tmp_path)

    hooks.run_hook(hooks.PHASE_POST_TOOL, json.dumps(_PARENT_PAYLOAD), env)
    stop, _ = hooks.run_hook(hooks.PHASE_STOP, json.dumps(_PARENT_PAYLOAD), env)
    text = stop.get("hookSpecificOutput", {}).get("additionalContext") or ""
    assert "subagent isolation unverified" not in text


# ── The bolt (design-the-bolt.md §Accretion): the boundary gauge ─────────────


def test_bolt_chip_absent_when_the_run_has_done_nothing():
    """A wake that has seen no ask, made no promise, and produced nothing
    renders no `bolt` chip — even when the caller opts in with T=0."""
    rendered = hooks.format_delta(
        _bar_payload(produce={"known": False, "counts": {}}),
        mood="smug_", bolt_asks_total=0,
    )
    assert "bolt" not in rendered.splitlines()[0]


def test_bolt_chip_requires_caller_opt_in():
    """Every existing call site that never passes `bolt_asks_total` (the
    default) must never grow a `bolt` chip — backward compatibility for the
    dozens of `format_delta` call sites already in this file."""
    rendered = hooks.format_delta(_bar_payload(), mood="smug_")
    assert "bolt" not in rendered.splitlines()[0]


def test_bolt_chip_renders_asks_owed_produce_omitting_zero_parts():
    from brr import promises

    plan = promises.blueprint([{"what": "commit", "count": 2}], {"commit": 1})
    payload = _bar_payload(
        attention={"pending_event_count": 1, "pending_outbox_file_count": 0},
        inbound={"events": [
            {"id": "evt-9", "source": "telegram", "summary": "ping"},
        ]},
        produce={"known": True, "counts": {"commit": 1, "kb": 1}},
    )
    rendered = hooks.format_delta(
        payload, mood="smug_", plan=plan, bolt_asks_total=3, bolt_edge=True,
    )
    bar = rendered.splitlines()[0]
    # T=3 asks ever seen, 1 still pending ⇒ A=2 dispositioned. owed 1 (2
    # promised commits, 1 landed). produce 2 (1 commit + 1 kb).
    assert "bolt 2/3 asks · owed 1 · produce 2" in bar
    assert "- bolt: 2/3 asks dispositioned · 1 owed · 2 produce —" in rendered
    assert "`brnrd cut`" in rendered


def test_bolt_chip_omits_the_asks_part_when_t_is_zero():
    """Only produce/promise triggered it this boundary — no asks part."""
    rendered = hooks.format_delta(
        _bar_payload(produce={"known": True, "counts": {"commit": 2}}),
        mood="smug_", bolt_asks_total=0,
    )
    bar = rendered.splitlines()[0]
    assert "bolt produce 2" in bar
    assert "asks" not in bar


def test_bolt_detail_line_silent_off_its_own_edge_and_route_prompt():
    """The chip is gateless (`owed`/`course`'s pattern); the detail line is
    latched — it must not repeat every boundary just because the chip is
    showing."""
    payload = _bar_payload(produce={"known": True, "counts": {"commit": 2}})
    rendered = hooks.format_delta(
        payload, mood="smug_", bolt_asks_total=2, bolt_edge=False,
    )
    assert "bolt 2/2 asks · produce 2" in rendered.splitlines()[0]
    assert "- bolt:" not in rendered


def test_bolt_detail_line_rerenders_on_a_fresh_event():
    """"fresh event ⇒ re-render your position against it" — the same trigger
    `route_prompt` already carries for the course line."""
    payload = _bar_payload(produce={"known": True, "counts": {"commit": 2}})
    rendered = hooks.format_delta(
        payload, mood="smug_", bolt_asks_total=2, bolt_edge=False,
        route_prompt=True,
    )
    assert "- bolt:" in rendered


def test_bolt_stop_closeout_names_brnrd_cut(tmp_path):
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "`brnrd cut`" in ctx


def test_bolt_asks_total_accumulates_across_boundaries_and_survives_disposition(
    tmp_path,
):
    """T (distinct asks ever seen) only grows; A (dispositioned) tracks the
    current pending count against it — an answered ask keeps counting
    toward T even after it drops off the pending list."""
    env = _env(tmp_path)
    ev1 = {"id": "evt-1001", "source": "telegram", "summary": "first"}
    ev2 = {"id": "evt-1002", "source": "telegram", "summary": "second"}

    _portal(tmp_path, token="t1", pending=1, events=[ev1])
    first, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    ctx1 = first["hookSpecificOutput"]["additionalContext"]
    assert "bolt 0/1 asks" in ctx1

    # ev1 answered (no longer pending), ev2 arrives fresh.
    _portal(tmp_path, token="t2", pending=1, events=[ev2])
    second, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    ctx2 = second["hookSpecificOutput"]["additionalContext"]
    # T=2 (ev1 and ev2, ever seen), A=1 (ev1 dispositioned; ev2 still
    # pending) — T survived ev1 leaving the pending list.
    assert "bolt 1/2 asks" in ctx2


# ── Compression-on-repeat (#1116 residue, design-the-live-loop.md §1) ────────


def test_notices_detail_compresses_on_the_third_consecutive_boundary():
    notices = [{"at": "2026-07-24T03:36:00Z", "text": "reply NOT delivered"}]
    payload = _bar_payload(notices=notices)
    full = hooks.format_delta(payload, repeat_streaks={"notices": 2})
    compact = hooks.format_delta(payload, repeat_streaks={"notices": 3})
    assert "refused/dropped this run" in full
    assert "refused/dropped this run" not in compact
    assert "- !1 · seen ×3 — portal-state.json → notices" in compact


def test_running_long_detail_compresses_on_the_third_consecutive_boundary():
    payload = _bar_payload(
        budget={"elapsed_seconds": 4000, "budget_seconds": 3600,
                "long_running": True},
    )
    full = hooks.format_delta(payload, repeat_streaks={"running_long": 2})
    compact = hooks.format_delta(payload, repeat_streaks={"running_long": 4})
    assert "extend via .keepalive if the work needs it" in full
    assert "extend via .keepalive if the work needs it" not in compact
    assert "- running long · seen ×4 — .keepalive" in compact


def test_name_nudge_detail_compresses_on_the_third_consecutive_boundary():
    payload = _bar_payload(
        budget={"elapsed_seconds": 300, "budget_seconds": 7200},
        card={"active": True, "stale": False},
    )
    full = hooks.format_delta(payload, repeat_streaks={"name_nudge": 1})
    compact = hooks.format_delta(payload, repeat_streaks={"name_nudge": 3})
    assert "still unwritten" in full
    assert "still unwritten" not in compact
    assert "- .name? · seen ×3 — write .name" in compact


def test_card_stale_detail_compresses_on_the_third_consecutive_boundary():
    payload = _bar_payload(
        card={"active": True, "stale": True, "age_seconds": 900,
              "state_moved_seconds": 500},
    )
    full = hooks.format_delta(payload, repeat_streaks={"card_stale": 2})
    compact = hooks.format_delta(payload, repeat_streaks={"card_stale": 3})
    assert "hasn't been rewritten" in full
    assert "hasn't been rewritten" not in compact
    assert "- card stale (900s) · seen ×3 — rewrite .card" in compact


def test_repeat_streak_resets_when_the_obligation_clears():
    """A line that clears and later reappears starts over at "new" (streak
    0, next bump 1) — the streak is per-*consecutive* laden boundary, not a
    lifetime count. Unit-level on `_bump_repeat_streaks` itself (the
    persisted-counter primitive `compute_neutral` calls every post-tool
    boundary), independent of whatever else does or doesn't open the bar on
    a given boundary."""
    state: dict = {}
    assert hooks._bump_repeat_streaks(state, {"running_long": True}) == {
        "running_long": 1
    }
    assert hooks._bump_repeat_streaks(state, {"running_long": True}) == {
        "running_long": 2
    }
    assert hooks._bump_repeat_streaks(state, {"running_long": True}) == {
        "running_long": 3
    }
    # The obligation clears — reset to 0, not held or decremented.
    assert hooks._bump_repeat_streaks(state, {"running_long": False}) == {
        "running_long": 0
    }
    # It reappears — starts over at 1, not "seen ×4".
    assert hooks._bump_repeat_streaks(state, {"running_long": True}) == {
        "running_long": 1
    }


def test_repeat_streaks_are_tracked_independently_per_key():
    state: dict = {}
    hooks._bump_repeat_streaks(state, {"notices": True, "card_stale": False})
    hooks._bump_repeat_streaks(state, {"notices": True, "card_stale": True})
    result = hooks._bump_repeat_streaks(
        state, {"notices": True, "card_stale": True}
    )
    assert result == {"notices": 3, "card_stale": 2}


def test_long_running_alone_reaches_the_resident_end_to_end(tmp_path):
    """One integration pin, combined with a pending event so the boundary
    actually opens (a bare `long_running` with nothing else pending/
    delivered/notice-worthy does not by itself open `_render_bar`'s own
    ladenness gate — a pre-existing, unrelated gap between that gate and
    `_has_post_tool_obligations`, out of this change's scope). Confirms the
    wiring from `compute_neutral` through to the rendered detail line."""
    env = _env(tmp_path)
    ev = {"id": "evt-2001", "source": "telegram", "summary": "hi"}
    budget_on = {"elapsed_seconds": 4000, "budget_seconds": 3600,
                 "long_running": True}
    _portal(tmp_path, token="t1", pending=1, events=[ev], budget=budget_on)
    out, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "running long: past the 3600s soft budget" in ctx


def test_stop_phase_is_exempt_from_compression(tmp_path):
    """The closeout channel is exempt from dedupe by law (hooks.py test at
    #963's pin) — running long must render in full at Stop even after many
    post-tool boundaries already compressed it."""
    env = _env(tmp_path)
    budget_on = {"elapsed_seconds": 4000, "budget_seconds": 3600,
                 "long_running": True}
    for i in range(4):
        _portal(tmp_path, token=f"t{i}", pending=0, budget=budget_on)
        hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)

    _portal(tmp_path, token="tstop", pending=0, budget=budget_on)
    stop, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", env)
    ctx = stop["hookSpecificOutput"]["additionalContext"]
    assert "running long: past the 3600s soft budget" in ctx
    assert "seen ×" not in ctx


def _add_bolt_facet(tmp_path, accepted=True, annotated=0):
    """Splice the daemon's `bolt` portal facet into an already-written portal
    state — the shape `_write_live_portal_state` emits after an accepted
    `cut:` (absent entirely when the run was never cut)."""
    path = tmp_path / "portal-state.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["bolt"] = {
        "accepted": accepted,
        "annotated": annotated,
        "accepted_at": "2026-08-08T00:00:00Z",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_an_accepted_bolt_collapses_the_closeout_capsule(tmp_path):
    """The polarity flip (design-the-bolt.md): once the daemon accepted the
    declaration, the negative capsule ('did you forget anything?') stands
    down — the same bad closeout that blocks an un-cut run passes a cut one."""
    (tmp_path / "outbox").mkdir()
    env = _env(tmp_path)
    env["BRR_OUTBOX_DIR"] = str(tmp_path / "outbox")
    env["BRR_NEXT_MOVE_GUARD"] = "1"

    # Un-cut: the next-move guard blocks this closeout shape.
    _portal(tmp_path, token="t1", pending=0)
    out, _ = hooks.run_hook(
        hooks.PHASE_STOP, _stdin("done, I guess"), env,
    )
    assert out.get("decision") == "block"

    # Cut: a *distinct* bad closeout (a second identical reply would be
    # eaten by the guard's own reply-digest latch and pass vacuously —
    # measured, not hypothesized), guard still armed — the capsule stands
    # down because the bolt is accepted, not because the latch spent.
    _portal(tmp_path, token="t1", pending=0)
    _add_bolt_facet(tmp_path)
    out, _ = hooks.run_hook(
        hooks.PHASE_STOP, _stdin("finished, or whatever"), env,
    )
    assert out.get("decision") != "block"


def test_an_accepted_bolt_still_blocks_on_a_pending_event(tmp_path):
    """Deliberately NOT collapsed: a message that lands after the cut is real
    attention, bolt or no bolt."""
    _portal(
        tmp_path, token="t1", pending=1,
        events=[{"id": "evt-9", "source": "telegram", "summary": "late ask"}],
    )
    _add_bolt_facet(tmp_path)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, _stdin("bye"), _env(tmp_path))
    assert out.get("decision") == "block"


def test_stop_affirms_an_accepted_bolt_instead_of_nagging(tmp_path):
    _portal(tmp_path, token="t1", pending=0)
    _add_bolt_facet(tmp_path)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "bolt: accepted — the cut stands." in ctx
    assert "declare this run's completion" not in ctx


def test_stop_names_the_annotated_count_on_a_forced_accept(tmp_path):
    _portal(tmp_path, token="t1", pending=0)
    _add_bolt_facet(tmp_path, annotated=2)
    out, _ = hooks.run_hook(hooks.PHASE_STOP, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "bolt: accepted, annotated — 2 check(s) unresolved" in ctx

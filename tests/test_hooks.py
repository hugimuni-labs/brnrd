"""Tests for the runner hooks back channel (``brnrd hook <phase>``)."""

from __future__ import annotations

import datetime
import json
import subprocess
import threading
import time

from brr import hooks


def _portal(tmp_path, *, token="t1", pending=0, events=None, scm=None, produce=None,
            resources=None, budget=None, outbound=None, card=None,
            name=None, current_event="evt-1", current_event_replyable=True):
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
    if name is not None:
        payload["name"] = name
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
    # hit hardest the runs that had already reported on telegram. Once
    # anything was delivered anywhere, silence is the success state.
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
    rendered = hooks.format_delta(_bar_payload(), mood="stoked")
    bar = rendered.splitlines()[0]

    assert bar == (
        "⌁ 3jy8 │ ⏱ 16/120m │ q S57·W50·F27 │ ▷1 │ rb3h │ ⇡2+3 │ ⚒4 │ "
        "mood stoked │ card ok"
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
    assert "- pending evt-9 (telegram): ping" in rendered


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
    _portal(tmp_path, token="t1", pending=0)
    (tmp_path / hooks.MOOD_NAME).write_text("curious", encoding="utf-8")
    out, _ = hooks.run_hook(hooks.PHASE_SESSION_START, "{}", _env(tmp_path))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "- mood: curious" in ctx
    assert "a mood worth showing is one the work moved" in ctx


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
    chip = hooks._mood_chip("a-very-long-mood-name-that-overflows-the-chip")
    assert chip == "a-very-long-mood…"
    assert len(chip) <= hooks._MOOD_DISPLAY_MAX_CHARS + 1


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
    assert hooks._emote_glyph(name) is None
    assert hooks._mood_chip(name) == name


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
    rendered = hooks.format_delta(_bar_payload(), mood="stoked", orient=(3, 5))
    assert rendered.splitlines()[0] == (
        "⌁ 3jy8 │ ⏱ 16/120m │ q S57·W50·F27 │ orient 3/5 │ ▷1 │ rb3h │ "
        "⇡2+3 │ ⚒4 │ mood stoked │ card ok"
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
    rendered = hooks.format_delta(_bar_payload(notices=notices), mood="stoked")
    bar = rendered.splitlines()[0]
    # !1 is present
    assert "!1" in bar
    # Order: ⚒ before !1 before mood before card
    assert bar.index("⚒") < bar.index("!1") < bar.index("mood") < bar.index("card ok")


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

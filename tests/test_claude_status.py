"""Claude result-JSON spend/context collector for head-less daemon runs."""

import json
import shutil

from brr import claude_status, facets


_RESULT = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "final reply\n",
    "total_cost_usd": 0.022774,
    "modelUsage": {
        "claude-haiku-4-5-20251001": {
            "inputTokens": 515,
            "outputTokens": 95,
            "cacheReadInputTokens": 0,
            "cacheCreationInputTokens": 10892,
            "costUSD": 0.022774,
            "contextWindow": 200000,
        }
    },
}


def test_supported_is_per_vessel():
    assert claude_status.supported("claude") is True
    assert claude_status.supported("claude-bare-api-only") is True
    assert claude_status.supported("codex") is False
    assert claude_status.supported(None) is False


def test_parse_result_spend_and_context_but_no_quota():
    levels = claude_status.parse_result(_RESULT)
    assert levels["source"] == "claude result JSON"
    assert levels["spend"]["total_cost_usd"] == 0.022774
    assert "$0.0228" in levels["spend"]["summary"]
    # #1225: ``context_window`` used to be derived from ``modelUsage``'s
    # cumulative-session token fields divided by a single ``contextWindow``
    # (the same #1178 shape already fixed for ``context_window_used_percent``
    # below) -- this payload's numbers happened to sit under 100% saturation,
    # so it asserted a plausible-looking 90-100% remaining band. That was
    # exactly the wrong reading: no ``session_id`` here means no transcript,
    # so there is no *instantaneous* occupancy snapshot to report either.
    # The fixed collector omits ``context_window`` entirely rather than fall
    # back to the cumulative estimate -- a pessimistic absence beats a
    # confident wrong number (#1178's standing rule, applied to this field
    # too).
    assert "context_window" not in levels
    assert levels["tokens"]["input_tokens"] == 515
    assert levels["tokens"]["output_tokens"] == 95
    assert levels["tokens"]["cache_read_input_tokens"] == 0
    assert levels["tokens"]["cache_creation_input_tokens"] == 10892
    # #1178: no ``session_id`` on this payload -> no transcript -> no
    # instantaneous reading to report. Absent, not a fabricated percentage.
    assert "context_window_used_percent" not in levels["tokens"]
    assert "quota" not in levels


def test_capture_stdout_writes_snapshot_and_unwraps_result(tmp_path):
    stdout = json.dumps(_RESULT)
    reply = claude_status.capture_stdout(stdout, {"BRR_OUTBOX_DIR": str(tmp_path)})

    assert reply == "final reply\n"
    snap = claude_status.load_snapshot(tmp_path)
    assert snap["spend"]["total_cost_usd"] == 0.022774
    assert "quota" not in snap


def test_capture_stdout_passes_plain_text_through(tmp_path):
    assert (
        claude_status.capture_stdout(
            "plain reply\n", {"BRR_OUTBOX_DIR": str(tmp_path)}
        )
        == "plain reply\n"
    )
    assert claude_status.load_snapshot(tmp_path) is None


def test_capture_stdout_writes_shared_copy_alongside_outbox(tmp_path):
    """#1027: the reading must also land somewhere the outbox sweep spares.

    ``BRR_SHARED_DIR`` names the account-shared ``.brr`` dir a *different*,
    later run's portal assembly can still read from after this run's own
    outbox directory is deleted (``daemon._remove_outbox``). Both copies are
    written — the outbox one is unchanged and still feeds same-run readers
    plus the closeout preservation copy (#1049).
    """
    outbox_dir = tmp_path / "outbox" / "evt-1"
    shared_dir = tmp_path / "shared"
    stdout = json.dumps(_RESULT)

    reply = claude_status.capture_stdout(
        stdout,
        {"BRR_OUTBOX_DIR": str(outbox_dir), "BRR_SHARED_DIR": str(shared_dir)},
    )

    assert reply == "final reply\n"
    outbox_snap = claude_status.load_snapshot(outbox_dir)
    shared_snap = claude_status.load_snapshot(shared_dir)
    assert outbox_snap["spend"]["total_cost_usd"] == 0.022774
    assert shared_snap["spend"]["total_cost_usd"] == 0.022774


def test_capture_stdout_shared_copy_survives_outbox_deletion(tmp_path):
    """The whole point: delete the outbox dir, the shared reading remains."""
    outbox_dir = tmp_path / "outbox" / "evt-1"
    shared_dir = tmp_path / "shared"
    stdout = json.dumps(_RESULT)

    claude_status.capture_stdout(
        stdout,
        {"BRR_OUTBOX_DIR": str(outbox_dir), "BRR_SHARED_DIR": str(shared_dir)},
    )
    shutil.rmtree(outbox_dir)

    assert claude_status.load_snapshot(outbox_dir) is None
    shared_snap = claude_status.load_snapshot(shared_dir)
    assert shared_snap["spend"]["total_cost_usd"] == 0.022774


def test_capture_stdout_without_shared_dir_only_writes_outbox(tmp_path):
    """No ``BRR_SHARED_DIR`` (older daemon, bare CLI test) -> no crash, no stray write."""
    reply = claude_status.capture_stdout(
        json.dumps(_RESULT), {"BRR_OUTBOX_DIR": str(tmp_path)}
    )
    assert reply == "final reply\n"
    assert claude_status.load_snapshot(tmp_path)["spend"]["total_cost_usd"] == 0.022774


def test_capture_stdout_empty_env_dict_does_not_fall_back_to_process_environ(
    tmp_path, monkeypatch
):
    """A real-but-empty ``env={}`` must not silently read the daemon's own environ.

    ``RunnerInvocation.env`` defaults to ``{}``, not ``None`` — an empty dict
    is a legitimate "nothing set" from a caller, and ``{} or os.environ``
    would have quietly substituted the *daemon process's* own environment
    (which carries no ``BRR_OUTBOX_DIR``/``BRR_SHARED_DIR`` of its own,
    contaminating the write with whatever the daemon's host env happens to
    carry — or, as here, writing nowhere the test can find it).
    """
    stray_dir = tmp_path / "stray-process-environ-dir"
    monkeypatch.setenv("BRR_OUTBOX_DIR", str(stray_dir))

    claude_status.capture_stdout(json.dumps(_RESULT), {})

    assert not stray_dir.exists()


def test_capture_stdout_stamps_run_id_when_the_caller_names_one(tmp_path):
    """#1027 follow-up (maintainer review): a snapshot served across a run
    boundary needs to say whose run it is, or the honest-looking summary
    text becomes a misattributed one. ``BRR_RUN_ID`` is the provenance."""
    claude_status.capture_stdout(
        json.dumps(_RESULT),
        {"BRR_OUTBOX_DIR": str(tmp_path), "BRR_RUN_ID": "run-example-abcd"},
    )
    assert claude_status.load_snapshot(tmp_path)["run_id"] == "run-example-abcd"


def test_capture_stdout_no_run_id_key_when_caller_names_none(tmp_path):
    claude_status.capture_stdout(
        json.dumps(_RESULT), {"BRR_OUTBOX_DIR": str(tmp_path)},
    )
    assert "run_id" not in claude_status.load_snapshot(tmp_path)


def test_mark_cross_run_attributes_the_carried_reading():
    levels = {
        "run_id": "run-earlier-abcd",
        "spend": {"summary": "$1.23 this session (estimated)", "total_cost_usd": 1.23},
        "context_window": {"summary": "40% context left (est)"},
    }

    marked = claude_status.mark_cross_run(levels)

    assert marked["spend"]["summary"] == (
        "$1.23 this session (estimated) — run-earlier-abcd's reading, "
        "carried (not this run's)"
    )
    assert marked["context_window"]["summary"] == (
        "40% context left (est) — run-earlier-abcd's reading, "
        "carried (not this run's)"
    )
    # The numeric field a reader might still aggregate on is untouched.
    assert marked["spend"]["total_cost_usd"] == 1.23
    # The original dict is not mutated in place.
    assert levels["spend"]["summary"] == "$1.23 this session (estimated)"


def test_mark_cross_run_is_a_noop_without_a_run_id():
    """An older snapshot written before ``run_id`` existed has nothing to
    attribute to — passed through exactly as it would have rendered before
    this fix, rather than inventing an "unknown run" label."""
    levels = {"spend": {"summary": "$1.23 this session (estimated)"}}
    assert claude_status.mark_cross_run(levels) is levels


def test_mark_cross_run_tolerates_none():
    assert claude_status.mark_cross_run(None) is None


def test_result_error_text_uses_errors_when_result_absent():
    payload = {"type": "result", "is_error": True, "errors": ["Reached budget"]}
    assert claude_status.result_text(payload, "{}") == "Reached budget\n"


def test_result_text_does_not_leak_raw_envelope_on_empty_result():
    """Regression: an aborted stream can return success with result: "".

    Observed for real (run-260704-1704-ttrd, terminal_reason
    aborted_streaming): result_text's old fallback returned the raw JSON
    stdout verbatim, which got written to the response file and indexed
    into conversation history as that run's "response" — a raw JSON blob
    standing in for a reply. The reply must name the gap, not the envelope.
    """
    payload = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "",
        "terminal_reason": "aborted_streaming",
    }
    raw_stdout = json.dumps(payload)
    text = claude_status.result_text(payload, raw_stdout)
    assert text == "(runner produced no reply text: aborted_streaming)\n"
    assert "aborted_streaming" in text
    assert '"type":"result"' not in text
    assert "modelUsage" not in text


def test_result_text_still_falls_back_for_non_envelope_json():
    """A dict with neither ``result`` nor ``errors`` keys at all is not a
    Claude result envelope (e.g. a custom command's own intentional JSON
    stdout) — that case keeps the pre-existing passthrough behaviour."""
    payload = {"foo": "bar"}
    assert claude_status.result_text(payload, '{"foo": "bar"}') == '{"foo": "bar"}'


def test_parse_result_exposes_real_model_id_from_model_usage_keys():
    """Regression #255: runner_core resolved to the placeholder "default" for
    every unpinned Claude run because ``_model_usage_tokens`` iterated
    ``modelUsage.values()`` for token totals and threw the keys away. The
    real model id is the dict's key, not anything inside its value."""
    levels = claude_status.parse_result(_RESULT)
    assert levels["model_ids"] == ["claude-haiku-4-5-20251001"]
    assert claude_status.resolved_model_id(levels) == "claude-haiku-4-5-20251001"


def test_parse_result_joins_multiple_model_ids_sorted():
    payload = {
        "type": "result",
        "result": "ok\n",
        "modelUsage": {
            "claude-opus-4-8": {"inputTokens": 10},
            "claude-haiku-4-5-20251001": {"inputTokens": 5},
        },
    }
    levels = claude_status.parse_result(payload)
    assert levels["model_ids"] == ["claude-haiku-4-5-20251001", "claude-opus-4-8"]
    assert (
        claude_status.resolved_model_id(levels)
        == "claude-haiku-4-5-20251001+claude-opus-4-8"
    )


def test_resolved_model_id_absent_when_model_usage_missing():
    payload = {"type": "result", "result": "ok\n"}
    levels = claude_status.parse_result(payload)
    assert "model_ids" not in levels
    assert claude_status.resolved_model_id(levels) is None
    assert claude_status.resolved_model_id(None) is None


def test_facets_claude_collector_marks_quota_absent_not_known():
    levels = claude_status.parse_result(_RESULT)
    res = facets.build(levels=levels, levels_collector=claude_status.COLLECTED_SLOTS)
    assert res["spend"]["status"] == "known"
    # #1225: ``_RESULT`` carries no ``session_id`` -> no transcript -> no
    # instantaneous reading, so ``context_window`` is omitted from
    # ``levels`` entirely (same standing rule as
    # ``test_parse_result_spend_and_context_but_no_quota`` above) and the
    # facet built from it reads "absent", not the pre-fix "known" this
    # fixture used to get from the cumulative fallback formula.
    assert res["context_window"]["status"] == "absent"
    assert res["quota"]["status"] == "absent"


# --- substitution-reason capture (2026-07-16) --------------------------------

# The documented server-side fallback envelope: Fable declined, Opus served.
# Shape from platform.claude.com refusals-and-fallback cookbook.
_FALLBACK_RESULT = {
    "type": "result",
    "model": "claude-opus-4-8",
    "content": [
        {
            "type": "fallback",
            "from": {"model": "claude-fable-5"},
            "to": {"model": "claude-opus-4-8"},
        },
        {"type": "text", "text": "Hi! How can I help?"},
    ],
    "stop_reason": "end_turn",
    "stop_details": None,
    "usage": {
        "input_tokens": 412,
        "output_tokens": 264,
        "iterations": [
            {"type": "message", "model": "claude-fable-5", "output_tokens": 0},
            {"type": "fallback_message", "model": "claude-opus-4-8", "output_tokens": 264},
        ],
    },
}

# An all-models-declined refusal: no fallback served, category named.
_REFUSAL_RESULT = {
    "type": "result",
    "model": "claude-fable-5",
    "content": [],
    "stop_reason": "refusal",
    "stop_details": {"type": "refusal", "category": "cyber", "explanation": "x"},
    "usage": {"input_tokens": 412, "output_tokens": 0},
}


def test_fallback_signals_none_for_non_dict():
    assert claude_status.fallback_signals("not a dict") is None
    assert claude_status.fallback_signals(None) is None


def test_fallback_signals_always_records_envelope_keys():
    # Even a clean success run yields forensics (the schema the CLI emits),
    # so a substituted run's snapshot shows exactly what is and isn't present.
    signals = claude_status.fallback_signals(_RESULT)
    assert signals is not None
    assert "modelUsage" in signals["envelope_keys"]
    assert signals["subtype"] == "success"


def test_fallback_signals_captures_fallback_block_and_iterations():
    signals = claude_status.fallback_signals(_FALLBACK_RESULT)
    assert signals["fallback_blocks"][0]["to"]["model"] == "claude-opus-4-8"
    types = [i["type"] for i in signals["iterations"]]
    assert "fallback_message" in types


def test_substitution_reason_none_on_clean_success():
    # end_turn is a benign terminal reason, not a substitution.
    levels = claude_status.parse_result(_RESULT)
    assert claude_status.substitution_reason(levels) is None


def test_substitution_reason_names_served_fallback_model():
    levels = claude_status.parse_result(_FALLBACK_RESULT)
    reason = claude_status.substitution_reason(levels)
    assert reason is not None
    assert "fallback->claude-opus-4-8" in reason
    assert "fallback_message:claude-opus-4-8" in reason
    # end_turn must not leak in as a "reason".
    assert "end_turn" not in reason


def test_substitution_reason_names_refusal_category():
    levels = claude_status.parse_result(_REFUSAL_RESULT)
    reason = claude_status.substitution_reason(levels)
    assert "stop_reason=refusal" in reason
    assert "category=cyber" in reason


def test_substitution_reason_none_without_signals():
    assert claude_status.substitution_reason({}) is None
    assert claude_status.substitution_reason(None) is None


# --- Session-transcript refusal capture (2026-07-16) -------------------------
# History, so the intent survives a reread: for three days every fable-pinned
# wake was silently served Opus. The reason existed the whole time, but the
# capture mined the result envelope, which was measured to carry *none* of it —
# a genuinely refused run answers ``terminal_reason: "completed"``,
# ``stop_reason: "end_turn"``, ``is_error: false``, no ``content``, no
# ``stop_details``, ``usage.iterations: []``. The reason lives only in Claude
# Code's session transcript, keyed by the envelope's ``session_id``.

_REFUSAL_ROW = {
    "type": "system",
    "subtype": "model_refusal_fallback",
    "direction": "retry",
    "trigger": "refusal",
    "apiRefusalCategory": "reasoning_extraction",
    "originalModel": "claude-fable-5",
    "fallbackModel": "claude-opus-4-8",
    "content": "Fable 5's safeguards flagged this message. Switched to Opus 4.8.",
}


def _transcript(root, session_id, rows):
    project = root / "-home-user-repo"
    project.mkdir(parents=True, exist_ok=True)
    path = project / f"{session_id}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def test_session_refusal_reads_the_reason_the_envelope_never_carries(tmp_path):
    _transcript(tmp_path, "sess-1", [{"type": "user"}, _REFUSAL_ROW])
    reason = claude_status.session_refusal("sess-1", projects_root=tmp_path)
    assert reason == {
        "count": 1,
        "category": "reasoning_extraction",
        "trigger": "refusal",
        "direction": "retry",
        "from": "claude-fable-5",
        "to": "claude-opus-4-8",
        "message": "Fable 5's safeguards flagged this message. Switched to Opus 4.8.",
    }


def test_session_refusal_is_none_for_a_clean_run(tmp_path):
    # The overwhelmingly common case: a transcript exists and never refused.
    _transcript(tmp_path, "sess-clean", [{"type": "user"}, {"type": "assistant"}])
    assert claude_status.session_refusal("sess-clean", projects_root=tmp_path) is None


def test_session_refusal_survives_missing_ids_and_junk_lines(tmp_path):
    _transcript(tmp_path, "sess-2", [_REFUSAL_ROW])
    for bad in (None, "", "no-such-session"):
        assert claude_status.session_refusal(bad, projects_root=tmp_path) is None
    # A truncated/corrupt line must not take the reason down with it.
    project = tmp_path / "-home-user-repo"
    (project / "sess-3.jsonl").write_text(
        '{"type": "system", "subtype": "model_refusal_fallback"  <-- truncated\n'
        + json.dumps(_REFUSAL_ROW)
        + "\n",
        encoding="utf-8",
    )
    reason = claude_status.session_refusal("sess-3", projects_root=tmp_path)
    assert reason is not None and reason["category"] == "reasoning_extraction"


def test_session_refusal_keeps_the_last_of_repeated_refusals(tmp_path):
    second = dict(_REFUSAL_ROW, apiRefusalCategory="cyber")
    _transcript(tmp_path, "sess-4", [_REFUSAL_ROW, second])
    reason = claude_status.session_refusal("sess-4", projects_root=tmp_path)
    assert reason["count"] == 2
    assert reason["category"] == "cyber"


def test_session_transcript_found_by_uuid_not_by_cwd_slug(tmp_path):
    # The lookup must not depend on how Claude Code encodes a cwd into a
    # directory name (``/`` and ``.`` both fold to ``-``); the session id is
    # unique, so any project directory is fair game.
    (tmp_path / "-some-utterly-unrelated--brr-worktrees-run-x").mkdir(parents=True)
    path = (
        tmp_path / "-some-utterly-unrelated--brr-worktrees-run-x" / "sess-5.jsonl"
    )
    path.write_text(json.dumps(_REFUSAL_ROW) + "\n", encoding="utf-8")
    assert claude_status.session_transcript_path("sess-5", projects_root=tmp_path) == path


def test_parse_result_merges_the_transcript_refusal_into_signals(tmp_path):
    _transcript(tmp_path, "sess-6", [_REFUSAL_ROW])
    payload = dict(_RESULT, session_id="sess-6")
    levels = claude_status.parse_result(payload, projects_root=tmp_path)
    assert levels["fallback_signals"]["refusal"]["category"] == "reasoning_extraction"


def test_substitution_reason_renders_the_transcript_refusal(tmp_path):
    _transcript(tmp_path, "sess-7", [_REFUSAL_ROW])
    # A real substituted envelope: success-shaped, benign stop_reason, nothing
    # of the reason in it. The reason must still render.
    payload = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "stop_reason": "end_turn",
        "terminal_reason": "completed",
        "usage": {"iterations": []},
        "session_id": "sess-7",
    }
    levels = claude_status.parse_result(payload, projects_root=tmp_path)
    assert claude_status.substitution_reason(levels) == (
        "refusal=reasoning_extraction;fallback=claude-fable-5->claude-opus-4-8"
    )


def test_substitution_reason_stays_none_when_the_run_was_clean(tmp_path):
    _transcript(tmp_path, "sess-8", [{"type": "assistant"}])
    levels = claude_status.parse_result(
        dict(_RESULT, session_id="sess-8"), projects_root=tmp_path
    )
    assert claude_status.substitution_reason(levels) is None


# --- context_window_used_percent: cumulative vs instantaneous (#1178) ----

def _assistant_row(model, usage):
    return {"type": "assistant", "message": {"model": model, "usage": usage}}


def test_cumulative_model_usage_alone_saturates_wrongly():
    """Pin down the bug this issue reports, red against the pre-fix formula.

    A 40-turn resumed session's ``modelUsage`` sums every turn's own cache
    reads into one running total (mirrors ``total_cost_usd``, which Claude
    Code's own ``/cost`` describes as cumulative "for the current session").
    That total trivially exceeds a 200k window after a handful of turns —
    this is the exact 100.0-on-a-short-clean-run shape #1178 reports. The
    *sum of the model's own cumulative tokens* is what the old formula (and
    ``_model_usage_cost``/spend accounting, which legitimately wants this
    same cumulative total) would divide by the window and clamp to 100.
    """
    cumulative = {
        "claude-sonnet-4-6": {
            "inputTokens": 80,
            "outputTokens": 12000,
            "cacheReadInputTokens": 4_800_000,  # 40 turns x ~120k reads, summed
            "cacheCreationInputTokens": 50_000,
            "contextWindow": 200_000,
        }
    }
    used = 80 + 4_800_000 + 50_000
    wrongly_saturated = max(0.0, min(100.0, 100.0 * used / 200_000))
    assert wrongly_saturated == 100.0  # the shape #1178 measured on real rows

    # The fixed collector no longer derives the field from this cumulative
    # sum at all (no session transcript here -> no instantaneous fallback
    # either) -> absent, not a re-clamped 100.0.
    tokens = claude_status.parse_result({"modelUsage": cumulative})["tokens"]
    assert "context_window_used_percent" not in tokens
    # The cumulative totals remain -- they are legitimate session-lifetime
    # spend/volume accounting, just not a context-occupancy reading.
    assert tokens["cache_read_input_tokens"] == 4_800_000


def test_instantaneous_reading_uses_the_last_turn_not_the_running_total(tmp_path):
    """The fix: read the transcript's last assistant turn, not the sum.

    Same blown-up cumulative ``modelUsage`` as above, but now a session
    transcript exists whose *last* turn's own usage is modest -- the honest
    reading is that turn's occupancy, not the session-lifetime sum.
    """
    _transcript(
        tmp_path,
        "sess-instant",
        [
            _assistant_row(
                "claude-sonnet-4-6",
                {
                    "inputTokens": 2,
                    "cacheReadInputTokens": 20000,
                    "cacheCreationInputTokens": 1000,
                    "outputTokens": 300,
                },
            ),
            # Growth turn over turn is real occupancy growth, not a sum --
            # the *last* row is what is currently occupying the window.
            _assistant_row(
                "claude-sonnet-4-6",
                {
                    "inputTokens": 2,
                    "cacheReadInputTokens": 60000,
                    "cacheCreationInputTokens": 2000,
                    "outputTokens": 150,
                },
            ),
        ],
    )
    cumulative = {
        "claude-sonnet-4-6": {
            "inputTokens": 80,
            "outputTokens": 12000,
            "cacheReadInputTokens": 4_800_000,
            "cacheCreationInputTokens": 50_000,
            "contextWindow": 200_000,
        }
    }
    payload = {"modelUsage": cumulative, "session_id": "sess-instant"}
    levels = claude_status.parse_result(payload, projects_root=tmp_path)
    pct = levels["tokens"]["context_window_used_percent"]
    # (2 + 60000 + 2000) / 200000 * 100 = 31.001 -- the last turn's own
    # occupancy, nowhere near the cumulative sum's forced 100.0.
    assert 30 < pct < 32


def test_context_window_remaining_percentage_tracks_instantaneous_not_cumulative(
    tmp_path,
):
    """#1225: the ``context_window`` chip is the *other* #1178 consumer.

    Same fixture shape as ``test_instantaneous_reading_uses_the_last_turn_
    not_the_running_total`` above (a blown-up cumulative ``modelUsage`` that
    would saturate the old formula to ~0% remaining, plus a transcript whose
    last turn is modest) -- but this test reads
    ``context_window.remaining_percentage`` instead of
    ``tokens.context_window_used_percent``, pinning down that the fix
    covers both consumers of the same instantaneous reading, not just the
    one #1224 already wired up.
    """
    _transcript(
        tmp_path,
        "sess-instant-remaining",
        [
            _assistant_row(
                "claude-sonnet-4-6",
                {
                    "inputTokens": 2,
                    "cacheReadInputTokens": 20000,
                    "cacheCreationInputTokens": 1000,
                    "outputTokens": 300,
                },
            ),
            _assistant_row(
                "claude-sonnet-4-6",
                {
                    "inputTokens": 2,
                    "cacheReadInputTokens": 60000,
                    "cacheCreationInputTokens": 2000,
                    "outputTokens": 150,
                },
            ),
        ],
    )
    cumulative = {
        "claude-sonnet-4-6": {
            "inputTokens": 80,
            "outputTokens": 12000,
            "cacheReadInputTokens": 4_800_000,
            "cacheCreationInputTokens": 50_000,
            "contextWindow": 200_000,
        }
    }
    payload = {"modelUsage": cumulative, "session_id": "sess-instant-remaining"}
    levels = claude_status.parse_result(payload, projects_root=tmp_path)
    # Last turn's own occupancy: (2 + 60000 + 2000) / 200000 * 100 = 31.001%
    # used -> ~68.999% remaining. The old cumulative formula would instead
    # divide the session-lifetime sum (4.85M+ tokens) by 200_000 and clamp
    # to 0% remaining -- nowhere close.
    remaining = levels["context_window"]["remaining_percentage"]
    assert 68 < remaining < 70
    assert "context left (est)" in levels["context_window"]["summary"]


def test_instantaneous_reading_absent_when_no_transcript_matches():
    payload = {
        "modelUsage": {
            "claude-sonnet-4-6": {
                "inputTokens": 10,
                "cacheReadInputTokens": 10,
                "contextWindow": 200_000,
            }
        },
        "session_id": "no-such-session",
    }
    levels = claude_status.parse_result(payload, projects_root="/nonexistent/path")
    assert "context_window_used_percent" not in levels["tokens"]


def test_instantaneous_reading_absent_when_transcript_has_no_usage(tmp_path):
    _transcript(tmp_path, "sess-empty", [{"type": "user"}, {"type": "assistant"}])
    payload = dict(_RESULT, session_id="sess-empty")
    levels = claude_status.parse_result(payload, projects_root=tmp_path)
    assert "context_window_used_percent" not in levels["tokens"]


def test_instantaneous_reading_matches_context_window_by_model_prefix(tmp_path):
    """The transcript's model id and ``modelUsage``'s key need not match exactly."""
    _transcript(
        tmp_path,
        "sess-prefix",
        [
            _assistant_row(
                "claude-haiku-4-5-20251001",  # dated id, like the real CLI emits
                {"inputTokens": 2, "cacheReadInputTokens": 998, "outputTokens": 10},
            )
        ],
    )
    payload = {
        "modelUsage": {"claude-haiku-4-5": {"contextWindow": 1000}},  # catalog id
        "session_id": "sess-prefix",
    }
    levels = claude_status.parse_result(payload, projects_root=tmp_path)
    assert levels["tokens"]["context_window_used_percent"] == 100.0


def test_runner_facet_exposes_the_substitution_reason():
    # The portal could say "mismatch" but never say why; that gap is what cost
    # three days of guesswork.
    levels = {
        "model_ids": ["claude-opus-4-8"],
        "fallback_signals": {
            "refusal": {
                "category": "reasoning_extraction",
                "from": "claude-fable-5",
                "to": "claude-opus-4-8",
                "count": 1,
            }
        },
    }
    block = facets.build(
        runner_name="claude-fable",
        runner_meta={"model": "claude-fable-5"},
        levels=levels,
    )["runner"]
    assert block["attestation"] == "mismatch"
    assert block["substitution_reason"] == (
        "refusal=reasoning_extraction;fallback=claude-fable-5->claude-opus-4-8"
    )

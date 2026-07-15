"""Claude result-JSON spend/context collector for head-less daemon runs."""

import json

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
    assert "context left (est)" in levels["context_window"]["summary"]
    assert 90 < levels["context_window"]["remaining_percentage"] < 100
    assert levels["tokens"]["input_tokens"] == 515
    assert levels["tokens"]["output_tokens"] == 95
    assert levels["tokens"]["cache_read_input_tokens"] == 0
    assert levels["tokens"]["cache_creation_input_tokens"] == 10892
    assert 5 < levels["tokens"]["context_window_used_percent"] < 6
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
    assert res["context_window"]["status"] == "known"
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

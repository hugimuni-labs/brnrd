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


def test_facets_claude_collector_marks_quota_absent_not_known():
    levels = claude_status.parse_result(_RESULT)
    res = facets.build(levels=levels, levels_collector=claude_status.COLLECTED_SLOTS)
    assert res["spend"]["status"] == "known"
    assert res["context_window"]["status"] == "known"
    assert res["quota"]["status"] == "absent"

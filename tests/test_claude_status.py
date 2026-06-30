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


def test_facets_claude_collector_marks_quota_absent_not_known():
    levels = claude_status.parse_result(_RESULT)
    res = facets.build(levels=levels, levels_collector=claude_status.COLLECTED_SLOTS)
    assert res["spend"]["status"] == "known"
    assert res["context_window"]["status"] == "known"
    assert res["quota"]["status"] == "absent"

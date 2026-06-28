"""Claude statusLine level collector — the spend/quota/context source (§8)."""

import json

from brr import statusline


def test_supported_is_per_vessel():
    assert statusline.supported("claude") is True
    assert statusline.supported("claude-sonnet") is True
    assert statusline.supported("codex") is False
    assert statusline.supported(None) is False


def test_parse_session_full_payload():
    levels = statusline.parse_session({
        "rate_limits": {
            "five_hour": {"used_percentage": 42, "resets_at": 1719560000},
            "seven_day": {"used_percentage": 12},
        },
        "context_window": {"remaining_percentage": 62},
        "cost": {"total_cost_usd": 0.4231},
    })
    # used% → headroom = 100 - used; reset window folded in.
    assert "5h 58% left" in levels["quota"]["summary"]
    assert "resets" in levels["quota"]["summary"]
    assert "7d 88% left" in levels["quota"]["summary"]
    assert levels["spend"]["total_cost_usd"] == 0.4231
    assert "$0.42" in levels["spend"]["summary"]
    assert levels["context_window"]["summary"] == "62% context left"
    assert levels["source"] == "claude statusLine"


def test_parse_session_tolerates_remaining_percentage_directly():
    levels = statusline.parse_session({
        "rate_limits": {"five_hour": {"remaining_percentage": 70}},
    })
    assert "5h 70% left" in levels["quota"]["summary"]


def test_parse_session_empty_yields_no_level_slots():
    levels = statusline.parse_session({})
    assert "quota" not in levels
    assert "spend" not in levels
    assert "context_window" not in levels
    # The snapshot still carries provenance so a reader knows it ran.
    assert levels["source"] == "claude statusLine"


def test_run_writes_snapshot_and_returns_footer(tmp_path):
    payload = json.dumps({
        "rate_limits": {"five_hour": {"used_percentage": 30}},
        "context_window": {"remaining_percentage": 80},
        "cost": {"total_cost_usd": 1.5},
    })
    footer, code = statusline.run(payload, {"BRR_OUTBOX_DIR": str(tmp_path)})
    assert code == 0
    assert "brr" in footer
    assert "ctx 80%" in footer
    assert "$1.50" in footer
    snap = statusline.load_snapshot(tmp_path)
    assert snap["spend"]["total_cost_usd"] == 1.5
    assert snap["context_window"]["remaining_percentage"] == 80


def test_run_survives_garbage_stdin(tmp_path):
    footer, code = statusline.run("not json", {"BRR_OUTBOX_DIR": str(tmp_path)})
    assert code == 0
    assert footer.startswith("brr")
    # Even an unparseable fire writes a (level-less) snapshot, never crashes.
    assert statusline.load_snapshot(tmp_path)["source"] == "claude statusLine"


def test_load_snapshot_missing_is_none(tmp_path):
    assert statusline.load_snapshot(tmp_path) is None
    assert statusline.load_snapshot(None) is None

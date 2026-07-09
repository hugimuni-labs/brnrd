"""Codex session-rollout quota collector — the head-less subscription-quota
source for the Codex Shell (§8). Verified live 2026-06-28: ``token_count``
events in a rollout JSONL carry ``rate_limits`` (5h + weekly)."""

import json

from brr import codex_status, facets


# A real-shape token_count payload (trimmed from a live rollout, 2026-06-28).
_PAYLOAD = {
    "type": "token_count",
    "info": {
        "total_token_usage": {"total_tokens": 6965046},
        "last_token_usage": {"input_tokens": 198358, "total_tokens": 199850},
        "model_context_window": 258400,
    },
    "rate_limits": {
        "limit_id": "codex",
        "primary": {"used_percent": 80.0, "window_minutes": 300, "resets_at": 1782572885},
        "secondary": {"used_percent": 27.0, "window_minutes": 10080, "resets_at": 1783095178},
        "plan_type": "plus",
    },
}


def test_supported_is_per_vessel():
    assert codex_status.supported("codex") is True
    assert codex_status.supported("codex-mini") is True
    assert codex_status.supported("claude") is False
    assert codex_status.supported(None) is False


def test_parse_token_count_quota_and_context():
    levels = codex_status.parse_token_count(_PAYLOAD)
    # used_percent → headroom = 100 - used; window_minutes → human label.
    assert "5h 20% left" in levels["quota"]["summary"]
    assert "7d 73% left" in levels["quota"]["summary"]
    assert "resets" in levels["quota"]["summary"]
    assert levels["plan_type"] == "plus"
    # Both windows' used_percent survive numerically now, not just the 5h
    # one — the weekly (secondary) window was previously discarded past the
    # rendered summary string (kb/design-director-loop.md §B1).
    assert levels["quota"]["primary_used_percent"] == 80.0
    assert levels["quota"]["secondary_used_percent"] == 27.0
    assert levels["quota"]["primary_remaining_percent"] == 20.0
    assert levels["quota"]["secondary_remaining_percent"] == 73.0
    # Raw reset epochs pass through alongside the formatted summary text —
    # the dashboard's window-track visual needs a machine-parseable instant.
    assert levels["quota"]["primary_resets_at"] == 1782572885.0
    assert levels["quota"]["secondary_resets_at"] == 1783095178.0
    # context headroom estimated from last input_tokens / window.
    assert "context left (est)" in levels["context_window"]["summary"]
    assert 20 < levels["context_window"]["remaining_percentage"] < 30
    assert levels["tokens"]["input_tokens"] == 198358
    assert levels["tokens"]["output_tokens"] == 1492
    assert 76 < levels["tokens"]["context_window_used_percent"] < 77
    assert levels["source"] == "codex session rollout"


def test_parse_token_count_unrecognized_shape_is_empty():
    levels = codex_status.parse_token_count({"nope": True})
    assert "quota" not in levels and "context_window" not in levels


def test_parse_token_count_missing_secondary_stays_none():
    payload = {
        "rate_limits": {
            "primary": {"used_percent": 40.0, "window_minutes": 300},
        },
    }
    levels = codex_status.parse_token_count(payload)
    assert levels["quota"]["primary_used_percent"] == 40.0
    assert levels["quota"]["primary_remaining_percent"] == 60.0
    assert levels["quota"]["secondary_used_percent"] is None
    assert levels["quota"]["secondary_remaining_percent"] is None
    # No resets_at on either window in this payload — both stay None, not a
    # fabricated guess.
    assert levels["quota"]["primary_resets_at"] is None
    assert levels["quota"]["secondary_resets_at"] is None


def test_load_levels_reads_newest_rollout(tmp_path, monkeypatch):
    sessions = tmp_path / ".codex" / "sessions" / "2026" / "06" / "28"
    sessions.mkdir(parents=True)
    rollout = sessions / "rollout-2026-06-28T00-00-00-abc.jsonl"
    rollout.write_text(
        "\n".join(
            json.dumps({"timestamp": "t", "type": "event_msg", "payload": p})
            for p in (
                {"type": "user_message", "message": "hi"},
                _PAYLOAD,
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    levels = codex_status.load_levels()
    assert levels is not None
    assert "5h 20% left" in levels["quota"]["summary"]


def test_load_levels_no_sessions_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "absent"))
    assert codex_status.load_levels() is None


def test_load_levels_updated_at_is_the_events_own_timestamp_not_scrape_time(
    tmp_path, monkeypatch
):
    """Live-caught 2026-07-09: a screenshot showed the 5h window rendered
    'critical, resets in now' while the weekly window read a healthy 81% —
    traced to ``updated_at`` being stamped with wall-clock "now" on every
    daemon poll tick, even when the rollout file hadn't been written to in
    hours (no codex run active). ``activity_dashboard.py::_quota_views``'s
    staleness check trusts this field, so an hours-stale snapshot always
    looked freshly-scraped — the same "lying usage panel" class already
    fixed for Claude (2026-07-07), reproduced here because this collector
    was assumed to have no idle-gap. ``updated_at`` must reflect the
    rollout event's own real time, not whenever brr happened to re-read it."""
    sessions = tmp_path / ".codex" / "sessions" / "2026" / "07" / "08"
    sessions.mkdir(parents=True)
    rollout = sessions / "rollout-2026-07-08T00-00-00-abc.jsonl"
    old_timestamp = "2026-07-08T20:18:25.753Z"
    rollout.write_text(
        json.dumps({"timestamp": old_timestamp, "type": "event_msg", "payload": _PAYLOAD}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    levels = codex_status.load_levels()
    assert levels is not None
    # Reformatted to the shared updated_at shape, not left as wall-clock now.
    assert levels["updated_at"] == "2026-07-08T20:18:25Z"


def test_fmt_event_timestamp_falls_back_to_none_for_garbage():
    assert codex_status._fmt_event_timestamp("not-a-timestamp") is None
    assert codex_status._fmt_event_timestamp(None) is None
    assert codex_status._fmt_event_timestamp("2026-07-08T20:18:25.753Z") == "2026-07-08T20:18:25Z"


def test_facets_codex_collector_marks_spend_unimplemented():
    """Per-slot honesty: Codex collects quota + context, but has no $-spend
    gauge, so ``spend`` must read ``unimplemented`` (not ``absent``)."""
    levels = codex_status.parse_token_count(_PAYLOAD)
    res = facets.build(levels=levels, levels_collector=codex_status.COLLECTED_SLOTS)
    assert res["quota"]["status"] == "known"
    assert res["context_window"]["status"] == "known"
    assert res["spend"]["status"] == "unimplemented"


def test_facets_levels_collector_bool_back_compat():
    """``levels_collector=True`` still means all level slots are wired."""
    res = facets.build(levels={}, levels_collector=True)
    assert res["spend"]["status"] == "absent"
    assert res["context_window"]["status"] == "absent"

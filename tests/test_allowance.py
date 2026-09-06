"""design-the-allowance.md, slice 1 — parsing, formatting, per-Shell metering."""

from __future__ import annotations

import json

from brr import allowance, codex_status


# ── parse_tokens / parse_signed_tokens ───────────────────────────────────


def test_parse_tokens_accepts_k_and_m_suffixes():
    assert allowance.parse_tokens("120k") == 120_000
    assert allowance.parse_tokens("1.2m") == 1_200_000
    assert allowance.parse_tokens("1500") == 1500
    assert allowance.parse_tokens("  90K  ") == 90_000


def test_parse_tokens_rejects_junk_and_non_positive():
    assert allowance.parse_tokens("") is None
    assert allowance.parse_tokens("nope") is None
    assert allowance.parse_tokens("0") is None
    assert allowance.parse_tokens("-5k") is None


def test_parse_signed_tokens_carries_the_sign():
    assert allowance.parse_signed_tokens("+50k") == 50_000
    assert allowance.parse_signed_tokens("-10000") == -10_000
    assert allowance.parse_signed_tokens("50k") == 50_000
    assert allowance.parse_signed_tokens("bogus") is None


def test_format_tokens_matches_the_bar_shape():
    assert allowance.format_tokens(38_000) == "38k"
    assert allowance.format_tokens(1_200_000) == "1.2m"
    assert allowance.format_tokens(500) == "500"
    assert allowance.format_tokens(None) == "?"


def test_spend_pct_is_none_without_a_denominator():
    assert allowance.spend_pct(50, 100) == 50.0
    assert allowance.spend_pct(50, 0) is None
    assert allowance.spend_pct(None, 100) is None


# ── claude: transcript metering (step zero's live per-run reader) ───────


def _write_transcript(path, *usages):
    with path.open("w", encoding="utf-8") as handle:
        for usage in usages:
            handle.write(json.dumps({
                "type": "assistant",
                "message": {"model": "claude-sonnet-4-6", "usage": usage},
            }) + "\n")


def test_claude_transcript_tokens_sums_every_assistant_turn(tmp_path):
    path = tmp_path / "session.jsonl"
    _write_transcript(
        path,
        {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 0,
         "cache_creation_input_tokens": 200},
        {"input_tokens": 20, "output_tokens": 30, "cache_read_input_tokens": 300,
         "cache_creation_input_tokens": 0},
    )
    # cost-weighted (TOKEN_WEIGHTS): turn 1 = 100 + 50*5 + 0 + 200*1.25 = 600;
    # turn 2 = 20 + 30*5 + 300*0.1 + 0 = 200  → 800
    assert allowance.claude_transcript_tokens(path) == 800


def test_weighted_tokens_uses_the_providers_price_ratios():
    # A cache re-read of the same context is a tenth of a fresh read; output
    # is five reads. The measured strand (146m raw, 145m of it re-reads)
    # lands near 17m weighted — the whole reason the unit is weighted.
    assert allowance.weighted_tokens(input=100) == 100
    assert allowance.weighted_tokens(cache_read=1000) == 100
    assert allowance.weighted_tokens(output=10) == 50
    assert allowance.weighted_tokens(cache_creation=100) == 125
    assert allowance.weighted_tokens(
        input=1_032, output=342_527, cache_read=144_941_516,
        cache_creation=919_718,
    ) == 17_357_466


def test_claude_transcript_tokens_ignores_non_assistant_rows(tmp_path):
    path = tmp_path / "session.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n")
    assert allowance.claude_transcript_tokens(path) is None


def test_claude_transcript_tokens_none_when_path_missing(tmp_path):
    assert allowance.claude_transcript_tokens(tmp_path / "nope.jsonl") is None
    assert allowance.claude_transcript_tokens(None) is None


# ── claude: the first turn's own boot cost (brnrd#1810, design-the-seat-
# that-never-quits.md §"The measurement") ────────────────────────────────


def test_claude_first_turn_boot_tokens_reads_only_the_first_turn(tmp_path):
    path = tmp_path / "session.jsonl"
    _write_transcript(
        path,
        {"input_tokens": 100, "output_tokens": 999, "cache_read_input_tokens": 0,
         "cache_creation_input_tokens": 200},
        # A second turn with a much larger usage must never move the
        # reading — boot cost is turn one, once, forever.
        {"input_tokens": 9_000, "output_tokens": 9_000,
         "cache_read_input_tokens": 9_000, "cache_creation_input_tokens": 9_000},
    )
    # output and cache_read are excluded even on the first turn — only
    # input (1x) + cache_creation (1.25x): 100 + 200*1.25 = 350.
    assert allowance.claude_first_turn_boot_tokens(path) == 350


def test_claude_first_turn_boot_tokens_excludes_output_and_cache_read(tmp_path):
    path = tmp_path / "session.jsonl"
    _write_transcript(
        path, {"output_tokens": 500, "cache_read_input_tokens": 500},
    )
    assert allowance.claude_first_turn_boot_tokens(path) is None


def test_claude_first_turn_boot_tokens_ignores_non_assistant_rows(tmp_path):
    path = tmp_path / "session.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n")
        handle.write(json.dumps({
            "type": "assistant",
            "message": {"model": "claude-sonnet-4-6",
                        "usage": {"input_tokens": 42}},
        }) + "\n")
    assert allowance.claude_first_turn_boot_tokens(path) == 42


def test_claude_first_turn_boot_tokens_none_when_path_missing(tmp_path):
    assert allowance.claude_first_turn_boot_tokens(tmp_path / "nope.jsonl") is None
    assert allowance.claude_first_turn_boot_tokens(None) is None


# ── claude: the last turn's own context occupancy (design-the-seat-that-
# never-quits.md §slice 4, "context rebirth") ─────────────────────────────


def test_claude_last_turn_context_tokens_reads_only_the_last_turn(tmp_path):
    path = tmp_path / "session.jsonl"
    _write_transcript(
        path,
        # A first turn with a much smaller usage must never move the
        # reading — occupancy is the *last* turn, always, not turn one.
        {"input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 1,
         "cache_creation_input_tokens": 1},
        {"input_tokens": 100, "output_tokens": 999,
         "cache_read_input_tokens": 8_000, "cache_creation_input_tokens": 200},
    )
    # output is excluded even on the last turn — only input + cache_read +
    # cache_creation, unweighted (occupancy, not cost): 100 + 8000 + 200.
    assert allowance.claude_last_turn_context_tokens(path) == 8_300


def test_claude_last_turn_context_tokens_excludes_output_only(tmp_path):
    path = tmp_path / "session.jsonl"
    _write_transcript(path, {"output_tokens": 500})
    assert allowance.claude_last_turn_context_tokens(path) is None


def test_claude_last_turn_context_tokens_ignores_non_assistant_rows(tmp_path):
    path = tmp_path / "session.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "type": "assistant",
            "message": {"model": "claude-sonnet-4-6",
                        "usage": {"input_tokens": 42}},
        }) + "\n")
        handle.write(json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n")
    # The trailing user row carries no usage — the last *assistant* row
    # still wins, not a bare "last line in the file" reading.
    assert allowance.claude_last_turn_context_tokens(path) == 42


def test_claude_last_turn_context_tokens_none_when_path_missing(tmp_path):
    assert allowance.claude_last_turn_context_tokens(tmp_path / "nope.jsonl") is None
    assert allowance.claude_last_turn_context_tokens(None) is None


# ── claude: the measured compaction-boundary row (design-the-seat-that-
# never-quits.md §slice 4b) ────────────────────────────────────────────────


def test_claude_transcript_compacted_true_on_a_real_compaction_row(tmp_path):
    """The measured shape (2026-09-06, three real compactions on this
    account's own transcripts): a top-level ``isCompactSummary: true`` on a
    ``type: "user"`` row, with the CLI's own continuation preamble as its
    message content."""
    path = tmp_path / "session.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "type": "assistant",
            "message": {"model": "claude-sonnet-4-6", "usage": {"input_tokens": 42}},
        }) + "\n")
        handle.write(json.dumps({
            "type": "user",
            "isCompactSummary": True,
            "isVisibleInTranscriptOnly": True,
            "message": {
                "role": "user",
                "content": "This session is being continued from a previous "
                "conversation that ran out of context. The summary below "
                "covers the earlier portion of the conversation.\n\nSummary:\n1. ...",
            },
        }) + "\n")
    assert allowance.claude_transcript_compacted(path) is True


def test_claude_transcript_compacted_false_without_the_marker(tmp_path):
    path = tmp_path / "session.jsonl"
    _write_transcript(path, {"input_tokens": 42})
    assert allowance.claude_transcript_compacted(path) is False


def test_claude_transcript_compacted_false_when_the_key_reads_falsy(tmp_path):
    """The marker string can appear without the boolean actually being
    ``True`` — a stray mention must not read as a real compaction."""
    path = tmp_path / "session.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "type": "user", "isCompactSummary": False, "message": {"content": "hi"},
        }) + "\n")
    assert allowance.claude_transcript_compacted(path) is False


def test_claude_transcript_compacted_false_when_path_missing(tmp_path):
    assert allowance.claude_transcript_compacted(tmp_path / "nope.jsonl") is False
    assert allowance.claude_transcript_compacted(None) is False


def test_latest_claude_transcript_finds_the_newest_under_the_cwd_slug(tmp_path):
    root = tmp_path / "projects"
    cwd = "/Users/x/worktrees/run-1"
    slug_dir = root / cwd.replace("/", "-")
    slug_dir.mkdir(parents=True)
    older = slug_dir / "older.jsonl"
    newer = slug_dir / "newer.jsonl"
    older.write_text("{}\n", encoding="utf-8")
    newer.write_text("{}\n", encoding="utf-8")
    import os
    import time
    now = time.time()
    os.utime(older, (now - 100, now - 100))
    os.utime(newer, (now, now))
    found = allowance.latest_claude_transcript(cwd, projects_root=root)
    assert found == newer


def test_latest_claude_transcript_none_with_no_projects_dir(tmp_path):
    assert allowance.latest_claude_transcript(
        "/some/cwd", projects_root=tmp_path / "absent",
    ) is None
    assert allowance.latest_claude_transcript(None) is None


# ── codex: rollout's own cumulative counter (step zero's other reader) ──


def test_codex_total_tokens_used_reads_total_token_usage(tmp_path):
    root = tmp_path / "sessions" / "2026" / "09" / "05"
    root.mkdir(parents=True)
    thread_id = "11111111-1111-1111-1111-111111111111"
    rollout = root / f"rollout-2026-09-05T00-00-00-{thread_id}.jsonl"
    rollout.write_text(
        json.dumps({
            "timestamp": "2026-09-05T00:00:00Z",
            "payload": {
                "type": "token_count",
                "info": {
                    "model_context_window": 200000,
                    "total_token_usage": {
                        "input_tokens": 10_000, "cached_input_tokens": 9_000,
                        "cache_write_input_tokens": 0, "output_tokens": 100,
                        "reasoning_output_tokens": 20, "total_tokens": 10_100,
                    },
                    "last_token_usage": {"input_tokens": 1000, "total_tokens": 1200},
                },
            },
        }) + "\n",
        encoding="utf-8",
    )
    env = {"CODEX_HOME": str(tmp_path)}
    # fresh 1,000 + cached 9,000*0.1 + output 100*5 = 2,400 (input_tokens
    # includes the cached share — measured on a real rollout).
    assert codex_status.total_tokens_used(env, thread_id=thread_id) == 2_400
    # Metering dispatch picks codex over claude when the runner is codex.
    assert allowance.collect_spent("codex", None, codex_thread_id=thread_id) is None
    # (no CODEX_HOME wired through env for the dispatch path — this asserts
    # only that collect_spent doesn't crash and defers to the real env; the
    # per-Shell reader itself is covered directly above.)


def test_codex_total_tokens_used_falls_back_to_the_raw_total(tmp_path):
    root = tmp_path / "sessions" / "2026" / "09" / "05"
    root.mkdir(parents=True)
    thread_id = "22222222-2222-2222-2222-222222222222"
    rollout = root / f"rollout-2026-09-05T00-00-00-{thread_id}.jsonl"
    rollout.write_text(
        json.dumps({
            "timestamp": "2026-09-05T00:00:00Z",
            "payload": {
                "type": "token_count",
                "info": {"total_token_usage": {"total_tokens": 123456}},
            },
        }) + "\n",
        encoding="utf-8",
    )
    env = {"CODEX_HOME": str(tmp_path)}
    assert codex_status.total_tokens_used(env, thread_id=thread_id) == 123456


def test_codex_total_tokens_used_none_without_a_rollout(tmp_path):
    env = {"CODEX_HOME": str(tmp_path)}
    assert codex_status.total_tokens_used(env, thread_id="not-a-uuid") is None
    assert codex_status.total_tokens_used(env) is None


def test_directive_line_names_park_and_ask():
    line = allowance.directive_line(125_000, 120_000)
    assert "125k/120k" in line
    assert "submit: true" in line
    assert "brnrd await" in line
    assert "ask: allowance +<tokens>" in line


# ── the resident seat's own standing allowance (design-the-allowance.md
# §2, slice 2) — config-owned ceiling, window keyed off the reset clock,
# never a quota-percent-to-token conversion ──────────────────────────────


def test_resident_ceiling_tokens_config_first_then_default():
    assert allowance.resident_ceiling_tokens(None) == (
        allowance.DEFAULT_RESIDENT_ALLOWANCE_TOKENS
    )
    assert allowance.resident_ceiling_tokens({}) == (
        allowance.DEFAULT_RESIDENT_ALLOWANCE_TOKENS
    )
    assert allowance.resident_ceiling_tokens(
        {"resident.allowance_tokens": "2m"}
    ) == 2_000_000
    # Unparsable config value degrades to the default, never a crash.
    assert allowance.resident_ceiling_tokens(
        {"resident.allowance_tokens": "not-a-number"}
    ) == allowance.DEFAULT_RESIDENT_ALLOWANCE_TOKENS


def test_resident_window_key_stable_until_the_reset_instant_moves():
    assert allowance.resident_window_key(None) is None
    assert allowance.resident_window_key(1_700_000_000.4) == "1700000000"
    assert allowance.resident_window_key(1_700_000_000.9) == "1700000000"
    assert allowance.resident_window_key(1_700_003_600.0) != (
        allowance.resident_window_key(1_700_000_000.0)
    )


def test_resident_allowance_state_reports_the_ceiling_before_any_reading():
    meta: dict = {}
    facet = allowance.resident_allowance_state(
        meta, cfg=None, reset_epoch=None, live_spent=None,
    )
    assert facet == {
        "tokens": allowance.DEFAULT_RESIDENT_ALLOWANCE_TOKENS,
        "spent": None, "scope": "resident",
    }
    assert meta["resident_allowance_spent"] is None


def test_resident_allowance_state_baselines_on_first_reading():
    meta: dict = {}
    facet = allowance.resident_allowance_state(
        meta, cfg=None, reset_epoch=1000.0, live_spent=500_000,
    )
    # A continuous seat's transcript is cumulative from long before this
    # ceiling existed — the first reading in a window baselines to zero
    # spend, never a fabricated 500k already "spent" against a fresh grant.
    assert facet["spent"] == 0
    assert meta["resident_allowance_window"] == "1000"
    assert meta["resident_allowance_baseline"] == 500_000


def test_resident_allowance_state_accrues_within_the_same_window():
    meta = {
        "resident_allowance_window": "1000",
        "resident_allowance_baseline": 500_000,
    }
    facet = allowance.resident_allowance_state(
        meta, cfg=None, reset_epoch=1000.0, live_spent=540_000,
    )
    assert facet["spent"] == 40_000
    assert meta["resident_allowance_baseline"] == 500_000  # unmoved


def test_resident_allowance_state_rebaselines_on_a_window_roll():
    meta = {
        "resident_allowance_window": "1000",
        "resident_allowance_baseline": 500_000,
    }
    facet = allowance.resident_allowance_state(
        meta, cfg=None, reset_epoch=2000.0, live_spent=560_000,
    )
    assert facet["spent"] == 0
    assert meta["resident_allowance_window"] == "2000"
    assert meta["resident_allowance_baseline"] == 560_000


def test_resident_allowance_state_keeps_the_window_when_reset_is_unknown():
    """A heartbeat with no quota reading this tick must not look like a
    window roll — it keeps whatever window/baseline is already stamped."""
    meta = {
        "resident_allowance_window": "1000",
        "resident_allowance_baseline": 500_000,
    }
    facet = allowance.resident_allowance_state(
        meta, cfg=None, reset_epoch=None, live_spent=530_000,
    )
    assert facet["spent"] == 30_000
    assert meta["resident_allowance_window"] == "1000"


def test_resident_allowance_state_never_reports_negative_spend():
    """A meter reading that regresses (a rare cross-run/clock artifact)
    must clamp to zero, never a negative "spend"."""
    meta = {
        "resident_allowance_window": "1000",
        "resident_allowance_baseline": 500_000,
    }
    facet = allowance.resident_allowance_state(
        meta, cfg=None, reset_epoch=1000.0, live_spent=480_000,
    )
    assert facet["spent"] == 0

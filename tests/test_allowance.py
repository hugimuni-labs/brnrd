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
    # 100+50+0+200 + 20+30+300+0 = 700
    assert allowance.claude_transcript_tokens(path) == 700


def test_claude_transcript_tokens_ignores_non_assistant_rows(tmp_path):
    path = tmp_path / "session.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n")
    assert allowance.claude_transcript_tokens(path) is None


def test_claude_transcript_tokens_none_when_path_missing(tmp_path):
    assert allowance.claude_transcript_tokens(tmp_path / "nope.jsonl") is None
    assert allowance.claude_transcript_tokens(None) is None


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
                    "total_token_usage": {"total_tokens": 123456},
                    "last_token_usage": {"input_tokens": 1000, "total_tokens": 1200},
                },
            },
        }) + "\n",
        encoding="utf-8",
    )
    env = {"CODEX_HOME": str(tmp_path)}
    assert codex_status.total_tokens_used(env, thread_id=thread_id) == 123456
    # Metering dispatch picks codex over claude when the runner is codex.
    assert allowance.collect_spent("codex", None, codex_thread_id=thread_id) is None
    # (no CODEX_HOME wired through env for the dispatch path — this asserts
    # only that collect_spent doesn't crash and defers to the real env; the
    # per-Shell reader itself is covered directly above.)


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

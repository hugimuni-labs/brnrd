"""Tests for runner quota snapshot parsing and display."""

from __future__ import annotations

import json

from brr import runner_quota


def test_describe_runner_quota_reads_specific_env(tmp_path):
    brr_dir = tmp_path / ".brr"
    brr_dir.mkdir()

    summary = runner_quota.describe_runner_quota(
        "codex",
        {},
        brr_dir,
        environ={
            "BRR_RUNNER_QUOTA": "generic",
            "BRR_RUNNER_QUOTA_CODEX": "weekly 12% - resets soon",
        },
    )

    assert summary == "weekly 12% - resets soon"


def test_describe_runner_quota_reads_config_summary(tmp_path):
    brr_dir = tmp_path / ".brr"
    brr_dir.mkdir()

    summary = runner_quota.describe_runner_quota(
        "codex",
        {"runner.quota.codex": "weekly 0% - resets 2026-06-17T01:29Z"},
        brr_dir,
        environ={},
    )

    assert summary == "weekly 0% - resets 2026-06-17T01:29Z"


def test_describe_runner_quota_formats_snapshot_file(tmp_path):
    brr_dir = tmp_path / ".brr"
    brr_dir.mkdir()
    (brr_dir / "runner-quota.json").write_text(
        json.dumps(
            {
                "runners": {
                    "codex": {
                        "source": "operator",
                        "buckets": [
                            {
                                "label": "weekly",
                                "remaining_percent": 0,
                                "reset_at": "2026-06-17T01:29:00+00:00",
                            },
                            {
                                "label": "5h",
                                "remaining_percent": 42.5,
                                "reset_after_seconds": 5400,
                            },
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    summary = runner_quota.describe_runner_quota(
        "codex", {}, brr_dir, environ={},
    )

    assert summary == "weekly 0% - resets 2026-06-17T01:29Z; 5h 42.5% - resets in 1h30m"


def test_describe_runner_quota_uses_provider_key_for_alias(tmp_path):
    brr_dir = tmp_path / ".brr"
    brr_dir.mkdir()

    summary = runner_quota.describe_runner_quota(
        "claude-bare-api-only",
        {"runner.quota.claude": "requests 31% - resets in 2h"},
        brr_dir,
        environ={},
    )

    assert summary == "requests 31% - resets in 2h"


def test_removed_gemini_family_is_not_a_bundled_provider_prefix():
    assert runner_quota._provider_key("gemini-pro") == "gemini-pro"


def test_describe_runner_quota_returns_none_without_signal(tmp_path):
    brr_dir = tmp_path / ".brr"
    brr_dir.mkdir()
    (brr_dir / "runner-quota.json").write_text("{bad json", encoding="utf-8")

    assert runner_quota.describe_runner_quota("gemini", {}, brr_dir, environ={}) is None


def test_describe_runner_quota_ignores_boolean_config(tmp_path):
    brr_dir = tmp_path / ".brr"
    brr_dir.mkdir()

    assert (
        runner_quota.describe_runner_quota(
            "codex", {"runner.quota.codex": False}, brr_dir, environ={},
        )
        is None
    )


def test_inline_json_snapshot_is_supported(tmp_path):
    brr_dir = tmp_path / ".brr"
    brr_dir.mkdir()

    summary = runner_quota.describe_runner_quota(
        "gemini",
        {
            "runner.quota": json.dumps(
                {
                    "runner": "gemini",
                    "buckets": {
                        "daily": {
                            "remaining_percent": 88,
                            "reset_at": "2026-06-18T12:00:00Z",
                        }
                    },
                }
            )
        },
        brr_dir,
        environ={},
    )

    assert summary == "daily 88% - resets 2026-06-18T12:00Z"


def test_relative_snapshot_file_resolves_under_brr_dir(tmp_path):
    brr_dir = tmp_path / ".brr"
    brr_dir.mkdir()
    (brr_dir / "quota.json").write_text(
        json.dumps(
            {
                "codex": {
                    "buckets": [
                        {"label": "weekly", "remaining_percent": 75},
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    summary = runner_quota.describe_runner_quota(
        "codex",
        {"runner.quota.file": "quota.json"},
        brr_dir,
        environ={},
    )

    assert summary == "weekly 75%"


def test_summary_from_levels_reads_quota_slot():
    summary = runner_quota.summary_from_levels(
        {"quota": {"summary": "session 12% left; week 40% left"}},
    )
    assert summary == "session 12% left; week 40% left"


def test_summary_from_levels_returns_none_without_quota():
    assert runner_quota.summary_from_levels({}) is None
    assert runner_quota.summary_from_levels(None) is None


def test_binding_quota_remaining_pct_reads_claude_shape():
    # Claude's buckets: session, week, and per-model week buckets. A
    # week_models bucket only binds when *model* names the Core spending it
    # (#561) — with no model named (a tick that hasn't committed to a
    # runner), every per-model bucket is excluded and the account-wide
    # session/week buckets alone decide the binding percent.
    levels = {
        "quota": {
            "summary": "session 90% left; week 55% left",
            "buckets": {
                "session": {"remaining_percentage": 90.0},
                "week": {"remaining_percentage": 55.0},
                "week_models": {"Fable": {"remaining_percentage": 14.2}},
            },
        }
    }
    assert runner_quota.binding_quota_remaining_pct(levels) == 55.0
    # Naming a different Core still excludes Fable's bucket.
    assert runner_quota.binding_quota_remaining_pct(levels, model="opus") == 55.0
    # Naming the matching Core (case-insensitively) pulls it back in.
    assert runner_quota.binding_quota_remaining_pct(levels, model="fable") == 14.2
    assert runner_quota.binding_quota_remaining_pct(levels, model="Fable") == 14.2


def test_binding_quota_remaining_pct_live_shape_561():
    # The exact live shape from issue #561: a run dispatched to `opus` must
    # not have its pacing throttled by an unrelated, near-exhausted `fable`
    # weekly bucket.
    levels = {
        "quota": {
            "summary": "session 96% left; week 44% left; Fable week 4% left",
            "buckets": {
                "session": {"remaining_percentage": 96.0},
                "week": {"remaining_percentage": 44.0},
                "week_models": {"Fable": {"remaining_percentage": 4.0}},
            },
        }
    }
    assert runner_quota.binding_quota_remaining_pct(levels) == 44.0
    assert runner_quota.binding_quota_remaining_pct(levels, model="opus") == 44.0
    assert runner_quota.binding_quota_remaining_pct(levels, model="fable") == 4.0


def test_binding_quota_remaining_pct_session_week_only_unchanged():
    # A snapshot with no week_models at all is unaffected by the model arg.
    levels = {
        "quota": {
            "buckets": {
                "session": {"remaining_percentage": 96.0},
                "week": {"remaining_percentage": 44.0},
            },
        }
    }
    assert runner_quota.binding_quota_remaining_pct(levels) == 44.0
    assert runner_quota.binding_quota_remaining_pct(levels, model="opus") == 44.0


def test_excluded_week_model_buckets_reports_non_binding_buckets():
    levels = {
        "quota": {
            "buckets": {
                "session": {"remaining_percentage": 96.0},
                "week": {"remaining_percentage": 44.0},
                "week_models": {
                    "Fable": {"remaining_percentage": 4.0},
                    "Opus": {"remaining_percentage": 70.0},
                },
            },
        }
    }
    assert runner_quota.excluded_week_model_buckets(levels, None) == {
        "Fable": 4.0, "Opus": 70.0,
    }
    assert runner_quota.excluded_week_model_buckets(levels, "fable") == {
        "Opus": 70.0,
    }
    assert runner_quota.excluded_week_model_buckets(levels, "sonnet") == {
        "Fable": 4.0, "Opus": 70.0,
    }
    assert runner_quota.excluded_week_model_buckets(None, None) == {}
    assert runner_quota.excluded_week_model_buckets({}, None) == {}


def test_binding_quota_remaining_pct_reads_codex_shape():
    levels = {
        "quota": {
            "summary": "5h 80% left; weekly 30% left",
            "primary_used_percent": 20.0,
            "secondary_used_percent": 70.0,
            "primary_remaining_percent": 80.0,
            "secondary_remaining_percent": 30.0,
        }
    }
    assert runner_quota.binding_quota_remaining_pct(levels) == 30.0


def test_binding_quota_remaining_pct_none_without_signal():
    assert runner_quota.binding_quota_remaining_pct(None) is None
    assert runner_quota.binding_quota_remaining_pct({}) is None
    assert runner_quota.binding_quota_remaining_pct({"quota": {"summary": "x"}}) is None
    # A quota slot that is a bare string (older shape) has no numeric field.
    assert runner_quota.binding_quota_remaining_pct({"quota": "5h 80% left"}) is None


def test_binding_quota_remaining_pct_ignores_missing_fields_in_mix():
    # Some fields present, some absent/None — only the numeric ones count,
    # and a None used_percent's derived remaining is also None, not 0/guessed.
    levels = {
        "quota": {
            "primary_used_percent": None,
            "secondary_used_percent": 40.0,
            "primary_remaining_percent": None,
            "secondary_remaining_percent": 60.0,
        }
    }
    assert runner_quota.binding_quota_remaining_pct(levels) == 60.0


def test_latest_claude_usage_outbox_dir_picks_freshest(tmp_path):
    """claude_usage caches into a *run's* outbox dir, never brr_dir itself —
    the shared-level readers (schedule pacing, dashboard quota publish) have
    to find the freshest one a recent run left behind."""
    import time

    brr_dir = tmp_path / ".brr"
    older = brr_dir / "outbox" / "evt-older"
    newer = brr_dir / "outbox" / "evt-newer"
    older.mkdir(parents=True)
    newer.mkdir(parents=True)
    (older / ".claude-usage-levels.json").write_text('{"quota": {"buckets": {"session": {"remaining_percentage": 10.0}}}}', encoding="utf-8")
    time.sleep(0.01)
    (newer / ".claude-usage-levels.json").write_text('{"quota": {"buckets": {"session": {"remaining_percentage": 90.0}}}}', encoding="utf-8")

    result = runner_quota.latest_claude_usage_outbox_dir(brr_dir)

    assert result == newer


def test_latest_claude_usage_outbox_dir_none_when_no_snapshot_cached(tmp_path):
    brr_dir = tmp_path / ".brr"
    (brr_dir / "outbox" / "evt-empty").mkdir(parents=True)
    assert runner_quota.latest_claude_usage_outbox_dir(brr_dir) is None
    assert runner_quota.latest_claude_usage_outbox_dir(tmp_path / "missing" / ".brr") is None

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

"""Claude interactive /usage quota collector."""

import json
from datetime import datetime, timezone

from brr import claude_usage


_USAGE_SCREEN = """
Settings Status Config Usage Stats
Current session
0% used
Resets 11:59pm (Europe/Berlin)
Current week (all models)
██████████████████████▌ 45% used
Resets Jul 2, 11:59pm (Europe/Berlin)
Usage credits
Usage credits are off
"""


def test_supported_is_per_vessel():
    assert claude_usage.supported("claude") is True
    assert claude_usage.supported("claude-haiku") is True
    assert claude_usage.supported("codex") is False
    assert claude_usage.supported(None) is False


def test_parse_usage_text_extracts_session_and_week_quota():
    levels = claude_usage.parse_usage_text(_USAGE_SCREEN)

    assert levels["source"] == "claude /usage PTY"
    assert levels["session_used_percentage"] == 0
    assert levels["week_used_percentage"] == 45
    assert (
        levels["quota"]["summary"]
        == "session 100% left (resets 11:59pm (Europe/Berlin)); "
        "week 55% left (resets Jul 2, 11:59pm (Europe/Berlin))"
    )
    # Numeric buckets ride alongside the rendered summary — the pacing seam
    # (kb/design-director-loop.md §B1) needs a number, not just prose.
    assert levels["quota"]["buckets"]["session"] == {"remaining_percentage": 100.0}
    assert levels["quota"]["buckets"]["week"] == {"remaining_percentage": 55.0}
    assert "week_models" not in levels["quota"]["buckets"]
    # Computed reset epochs ride alongside the scraped text, additively.
    assert isinstance(levels["session_resets_at"], float)
    assert isinstance(levels["week_resets_at"], float)


def test_reset_epoch_dated_form_uses_named_year():
    now = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)
    epoch = claude_usage._reset_epoch("Jul 10, 12am (Europe/Berlin)", now=now)
    expected = datetime(2026, 7, 10, 0, 0, tzinfo=claude_usage.ZoneInfo("Europe/Berlin"))
    assert epoch == expected.timestamp()


def test_reset_epoch_dated_form_rolls_year_forward_past_boundary():
    # "now" is deep into a year; a dated reset that reads as far in the past
    # relative to "now" must mean next year, not a stale date.
    now = datetime(2026, 12, 30, 12, 0, tzinfo=timezone.utc)
    epoch = claude_usage._reset_epoch("Jan 2, 6am (Europe/Berlin)", now=now)
    expected = datetime(2027, 1, 2, 6, 0, tzinfo=claude_usage.ZoneInfo("Europe/Berlin"))
    assert epoch == expected.timestamp()


def test_reset_epoch_date_only_form_defaults_to_midnight():
    now = datetime(2026, 7, 7, 12, 0, tzinfo=timezone.utc)
    epoch = claude_usage._reset_epoch("Aug 1 (Europe/Berlin)", now=now)
    expected = datetime(2026, 8, 1, 0, 0, tzinfo=claude_usage.ZoneInfo("Europe/Berlin"))
    assert epoch == expected.timestamp()


def test_reset_epoch_undated_form_already_passed_today_resolves_tomorrow():
    zone = claude_usage.ZoneInfo("Europe/Berlin")
    now = datetime(2026, 7, 5, 8, 0, tzinfo=zone)  # 8am local
    epoch = claude_usage._reset_epoch("4:50am (Europe/Berlin)", now=now)
    expected = datetime(2026, 7, 6, 4, 50, tzinfo=zone)  # tomorrow
    assert epoch == expected.timestamp()


def test_reset_epoch_undated_form_still_upcoming_resolves_today():
    zone = claude_usage.ZoneInfo("Europe/Berlin")
    now = datetime(2026, 7, 5, 8, 0, tzinfo=zone)  # 8am local
    epoch = claude_usage._reset_epoch("11:59pm (Europe/Berlin)", now=now)
    expected = datetime(2026, 7, 5, 23, 59, tzinfo=zone)  # later today
    assert epoch == expected.timestamp()


def test_reset_epoch_unparseable_or_unknown_zone_is_none():
    assert claude_usage._reset_epoch("not a reset string") is None
    assert claude_usage._reset_epoch("") is None
    assert claude_usage._reset_epoch(None) is None
    assert claude_usage._reset_epoch("11:59pm (Not/AZone)") is None


def test_parse_usage_text_tolerates_squashed_tui_words():
    levels = claude_usage.parse_usage_text(
        "Currentsession\n0%used\nResets11:59pm(Europe/Berlin)\n"
        "Currentweek(allmodels)\n45%used\nResetsJul2,11:59pm(Europe/Berlin)\n"
    )

    assert "session 100% left" in levels["quota"]["summary"]
    assert "week 55% left" in levels["quota"]["summary"]


def test_parse_usage_text_handles_compact_one_line_buckets():
    levels = claude_usage.parse_usage_text(
        "Current session: 7% used · resets Jun 30, 6:20pm (Europe/Berlin)\n"
        "Current week (all models): 70% used · resets Jul 3, 12am (Europe/Berlin)\n"
    )

    assert levels["session_used_percentage"] == 7
    assert levels["week_used_percentage"] == 70
    assert "session 93% left" in levels["quota"]["summary"]
    assert "week 30% left" in levels["quota"]["summary"]


def test_parse_usage_text_handles_screen_reader_duplicate_percentages():
    levels = claude_usage.parse_usage_text(
        "Current session\n7% 7% used\nResets 6:20pm (Europe/Berlin)\n"
        "Current week (all models)\n70% 70% used\nResets Jul 3, 12am (Europe/Berlin)\n"
    )

    assert levels["session_used_percentage"] == 7
    assert levels["week_used_percentage"] == 70


def test_parse_usage_text_handles_glued_session_header_and_usage_credits():
    levels = claude_usage.parse_usage_text(
        "Esc to cancelCurrent session\n"
        "100% 100% used\n"
        "Resets 12:20am (Europe/Berlin)\n"
        "Current week (all models)\n"
        "91% 91% used\n"
        "Resets Jul 10, 12am (Europe/Berlin)\n"
        "Usage credits\n"
        "22% 21% used\n"
        "\u20ac8.69 / \u20ac40.00 spent · Resets Aug 1 (Europe/Berlin)\n"
    )

    assert levels["session_used_percentage"] == 100
    assert levels["quota"]["buckets"]["session"] == {"remaining_percentage": 0.0}
    assert levels["week_used_percentage"] == 91
    assert levels["quota"]["buckets"]["week"] == {"remaining_percentage": 9.0}
    assert levels["usage_credits"]["enabled"] is True
    assert levels["usage_credits"]["used_percentage"] == 21
    assert levels["usage_credits"]["remaining_percentage"] == 79
    assert levels["usage_credits"]["spent_amount"] == 8.69
    assert levels["usage_credits"]["limit_amount"] == 40.0
    assert levels["usage_credits"]["currency"] == "\u20ac"
    assert levels["usage_credits"]["reset"] == "Aug 1 (Europe/Berlin)"
    assert "\u20ac8.69 / \u20ac40.00 spent" in levels["usage_credits"]["summary"]


def test_parse_usage_text_keeps_model_week_bucket_separate():
    levels = claude_usage.parse_usage_text(
        "Current session\n0% 0% used\nResets 4:50pm (Europe/Berlin)\n"
        "Current week (all models)\n5% 5% used\n"
        "Resets Jul 10, 12am (Europe/Berlin)\n"
        "Current week (Fable)\n8% 8% used\n"
        "Resets Jul 10, 12am (Europe/Berlin)\n"
    )

    assert levels["week_used_percentage"] == 5
    fable = levels["week_models"]["Fable"]
    assert fable["used_percentage"] == 8
    assert fable["reset"] == "Jul 10, 12am (Europe/Berlin)"
    assert isinstance(fable["resets_at"], float)
    assert levels["quota"]["summary"] == (
        "session 100% left (resets 4:50pm (Europe/Berlin)); "
        "week 95% left (resets Jul 10, 12am (Europe/Berlin)); "
        "Fable week 92% left"
    )
    assert levels["quota"]["buckets"]["week_models"]["Fable"] == {
        "remaining_percentage": 92.0
    }


def test_parse_usage_text_bare_week_reset_paren_is_not_a_model_label():
    levels = claude_usage.parse_usage_text(
        "Current session: 7% used · resets Jun 30, 6:20pm (Europe/Berlin)\n"
        "Current week: 70% used · resets Jul 3, 12am (Europe/Berlin)\n"
    )

    assert levels["week_used_percentage"] == 70
    assert "week_models" not in levels


def test_usage_command_uses_ax_screen_reader_and_safe_mode():
    assert claude_usage._usage_command() == [
        "claude",
        "--ax-screen-reader",
        "--model",
        "haiku",
        "--safe-mode",
    ]


def test_capture_levels_returns_error_snapshot_on_probe_failure(monkeypatch):
    def _boom(**_kwargs):
        raise RuntimeError("no tty")

    monkeypatch.setattr(claude_usage, "capture_usage_raw", _boom)

    levels = claude_usage.capture_levels()

    assert levels["source"] == "claude /usage PTY"
    assert levels["error"] == "no tty"
    assert "quota" not in levels


def test_load_or_refresh_snapshot_uses_fresh_cache(tmp_path, monkeypatch):
    cached = {"source": "claude /usage PTY", "quota": {"summary": "week 55% left"}}
    path = tmp_path / claude_usage.SNAPSHOT_NAME
    path.write_text(json.dumps(cached), encoding="utf-8")

    def _unexpected(**_kwargs):  # pragma: no cover - should not be called
        raise AssertionError("fresh cache should be used")

    monkeypatch.setattr(claude_usage, "capture_levels", _unexpected)

    assert claude_usage.load_or_refresh_snapshot(tmp_path) == cached


def test_ttl_env_var_overrides_default():
    assert claude_usage._ttl_seconds({}) == claude_usage.DEFAULT_TTL_SECONDS
    assert claude_usage._ttl_seconds({claude_usage.TTL_ENV_VAR: "45"}) == 45.0
    assert (
        claude_usage._ttl_seconds({claude_usage.TTL_ENV_VAR: "bogus"})
        == claude_usage.DEFAULT_TTL_SECONDS
    )


def test_load_or_refresh_snapshot_writes_negative_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(
        claude_usage,
        "capture_levels",
        lambda **_kwargs: {
            "source": "claude /usage PTY",
            "error": "no quota buckets parsed from /usage screen",
        },
    )

    levels = claude_usage.load_or_refresh_snapshot(tmp_path, max_age_seconds=0)

    assert levels["error"] == "no quota buckets parsed from /usage screen"
    assert claude_usage.load_snapshot(tmp_path)["error"] == levels["error"]

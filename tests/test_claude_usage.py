"""Claude interactive /usage quota collector."""

import json

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


def test_parse_usage_text_tolerates_squashed_tui_words():
    levels = claude_usage.parse_usage_text(
        "Currentsession\n0%used\nResets11:59pm(Europe/Berlin)\n"
        "Currentweek(allmodels)\n45%used\nResetsJul2,11:59pm(Europe/Berlin)\n"
    )

    assert "session 100% left" in levels["quota"]["summary"]
    assert "week 55% left" in levels["quota"]["summary"]


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

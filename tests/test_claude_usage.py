"""Claude interactive /usage quota collector."""

import json
import os
import sys
from pathlib import Path
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

# The boot banner, captured live against a real account (2026-07-23) via
# `claude_usage.capture_usage_raw()` before this fix existed — issue #580's
# answer to "does the panel render a plan/tier label anywhere": not inside
# `/usage` itself, but the line right after the version banner does.
_USAGE_SCREEN_WITH_PLAN_BANNER = """
Claude Code v2.1.218
Haiku 4.5 · Claude Max
~/src/misc/brr/.brr/worktrees/run-260723-1345-r67i
you: /usage
Settings Status Config Usage Stats
Current session
1% used
Resets 8:30pm (Europe/Berlin)
Current week (all models)
58% used
Resets Jul 24, 12am (Europe/Berlin)
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
    # ...and are folded into `quota` too (design-the-allowance.md §2, slice
    # 2), not just left at the top level — `daemon._merge_level_snapshots`
    # only carries the `quota` key through, so a reader downstream of that
    # merge (`runner_quota.binding_quota_reset_epoch`) needs them there.
    assert levels["quota"]["session_resets_at"] == levels["session_resets_at"]
    assert levels["quota"]["week_resets_at"] == levels["week_resets_at"]


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
        "--tools",
        "",
    ]


def test_recorded_real_usage_screen_2026_10_12():
    fixtures = Path(__file__).parent / "fixtures"
    assert "quota" not in claude_usage.parse_usage_text(
        (fixtures / "claude_usage_trust_2026_10_12.txt").read_bytes()
    )
    levels = claude_usage.parse_usage_text(
        (fixtures / "claude_usage_screen_2026_10_12.txt").read_bytes()
    )
    assert levels["session_used_percentage"] == 10
    assert levels["week_used_percentage"] == 92
    assert levels["session_reset"] == "3:40pm (Europe/Paris)"
    assert levels["week_reset"] == "Oct 12 at 10pm (Europe/Paris)"
    assert levels["quota"]["buckets"] == {
        "session": {"remaining_percentage": 90.0},
        "week": {"remaining_percentage": 8.0},
    }
    now = datetime(2026, 10, 12, 10, tzinfo=timezone.utc)
    assert claude_usage._reset_epoch(levels["session_reset"], now=now) == 1791812400
    assert claude_usage._reset_epoch(levels["week_reset"], now=now) == 1791835200


def test_probe_handles_recorded_trust_before_usage_and_isolates_the_repo(
    tmp_path, monkeypatch, _no_claude_usage_pty_scrape,
):
    fixtures = Path(__file__).parent / "fixtures"
    binary = tmp_path / "claude"
    evidence = tmp_path / "probe.json"
    binary.write_text("#!" + sys.executable + "\n" + r'''
import json, os, sys, time, tty
from pathlib import Path
Path(os.environ['PROBE_EVIDENCE']).write_text(json.dumps({
    'cwd': os.getcwd(), 'argv': sys.argv[1:],
    'git_pins': {k: os.environ[k] for k in ('GIT_DIR', 'GIT_WORK_TREE') if k in os.environ},
}))
tty.setraw(0)
# The real trust screen arrived after the old fixed boot delay.
time.sleep(0.4)
os.write(1, Path(os.environ['PROBE_TRUST']).read_bytes())
received = b''
while b'\r' not in received:
    received += os.read(0, 1024)
assert received == b'y\r', received
os.write(1, b'Claude Code v2.1.291\r\n')
received = b''
while b'\r' not in received:
    received += os.read(0, 1024)
assert received == b'/usage\r', received
os.write(1, Path(os.environ['PROBE_SCREEN']).read_bytes())
''')
    binary.chmod(0o755)
    monkeypatch.setattr(claude_usage, "DEFAULT_BOOT_SECONDS", 0.1)
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ["PATH"],
               GIT_DIR="dangerous-pin", GIT_WORK_TREE=str(tmp_path),
               PROBE_EVIDENCE=str(evidence),
               PROBE_TRUST=str(fixtures / "claude_usage_trust_2026_10_12.txt"),
               PROBE_SCREEN=str(fixtures / "claude_usage_screen_2026_10_12.txt"))
    raw = _no_claude_usage_pty_scrape(cwd=tmp_path, timeout_seconds=3, env=env)
    assert claude_usage.parse_usage_text(raw)["week_used_percentage"] == 92
    probe = json.loads(evidence.read_text())
    assert probe["git_pins"] == {}
    probe_dir = Path(probe["cwd"])
    # One fixed directory, reused: a fresh one per probe leaves a trust entry
    # in ~/.claude.json every time.
    assert probe_dir != tmp_path and probe_dir.name == "brr-claude-usage"
    assert not any(probe_dir.iterdir())
    assert probe["argv"] == claude_usage._usage_command()[1:]


def test_suite_guard_prevents_real_pty_scrape(monkeypatch):
    def _unexpected(*_args, **_kwargs):  # pragma: no cover - must stay guarded
        raise AssertionError("the unit suite must not spawn claude")

    monkeypatch.setattr(claude_usage.subprocess, "Popen", _unexpected)
    assert claude_usage.capture_usage_raw() == b""


def test_capture_levels_returns_error_snapshot_on_probe_failure(monkeypatch):
    def _boom(**_kwargs):
        raise RuntimeError("no tty")

    monkeypatch.setattr(claude_usage, "capture_usage_raw", _boom)

    levels = claude_usage.capture_levels()

    assert levels["source"] == "claude /usage PTY"
    assert levels["error"] == "no tty"
    assert "quota" not in levels


def test_parse_usage_text_elides_model_reset_that_differs_by_a_minute_across_midnight(
    monkeypatch,
):
    """Issue #561, defect 2: the panel renders one instant two ways across a
    reset boundary. Live evidence (2026-07-23): `week_resets_at` rendered as
    "Jul 24, 12am (Europe/Berlin)" (epoch 1784844000) while the Fable
    per-model bucket rendered the *same* underlying instant as "Jul 23,
    11:59pm (Europe/Berlin)" (epoch 1784843940) — 60 seconds apart in text,
    one moment in fact. Comparing rendered strings flickers on this; comparing
    parsed epochs with tolerance must not."""
    epochs = {
        "Jul 24, 12am (Europe/Berlin)": 1784844000.0,
        "Jul 23, 11:59pm (Europe/Berlin)": 1784843940.0,
    }
    monkeypatch.setattr(
        claude_usage, "_reset_epoch", lambda text, **kw: epochs.get(text)
    )

    levels = claude_usage.parse_usage_text(
        "Current session\n0% 0% used\nResets 4:50pm (Europe/Berlin)\n"
        "Current week (all models)\n5% 5% used\n"
        "Resets Jul 24, 12am (Europe/Berlin)\n"
        "Current week (Fable)\n8% 8% used\n"
        "Resets Jul 23, 11:59pm (Europe/Berlin)\n"
    )

    assert levels["quota"]["summary"] == (
        "session 100% left (resets 4:50pm (Europe/Berlin)); "
        "week 95% left (resets Jul 24, 12am (Europe/Berlin)); "
        "Fable week 92% left"
    )


def test_parse_usage_text_does_not_elide_a_genuinely_different_model_reset(
    monkeypatch,
):
    """The other half of the same fix: two resets that are actually different
    (hours or days apart, not a rendering artifact of one instant) must keep
    showing both, exactly as before."""
    epochs = {
        "Jul 10, 12am (Europe/Berlin)": 1000000.0,
        "Jul 17, 12am (Europe/Berlin)": 1604800.0,  # a week later
    }
    monkeypatch.setattr(
        claude_usage, "_reset_epoch", lambda text, **kw: epochs.get(text)
    )

    levels = claude_usage.parse_usage_text(
        "Current session\n0% 0% used\nResets 4:50pm (Europe/Berlin)\n"
        "Current week (all models)\n5% 5% used\n"
        "Resets Jul 10, 12am (Europe/Berlin)\n"
        "Current week (Fable)\n8% 8% used\n"
        "Resets Jul 17, 12am (Europe/Berlin)\n"
    )

    assert levels["quota"]["summary"] == (
        "session 100% left (resets 4:50pm (Europe/Berlin)); "
        "week 95% left (resets Jul 10, 12am (Europe/Berlin)); "
        "Fable week 92% left (resets Jul 17, 12am (Europe/Berlin))"
    )


def test_parse_usage_text_extracts_plan_type_from_boot_banner():
    """Issue #580 part 1: the `/usage` panel body never names the account's
    plan, but the boot banner line right after the version string does
    (`<model> · Claude <Tier>`, confirmed live 2026-07-23 as `Haiku 4.5 ·
    Claude Max`). Parsed into `levels["plan_type"]` — the same key Codex's
    collector already uses for its own plan identity."""
    levels = claude_usage.parse_usage_text(_USAGE_SCREEN_WITH_PLAN_BANNER)

    assert levels["plan_type"] == "Max"


def test_parse_usage_text_plan_type_absent_without_a_banner_line():
    """Defensive by construction: no recognizable banner line means no
    `plan_type` key at all — never a guess standing in for a fact the capture
    didn't actually contain."""
    levels = claude_usage.parse_usage_text(_USAGE_SCREEN)

    assert "plan_type" not in levels


def test_reset_epoch_reads_the_at_form_the_panel_switched_to():
    # Live capture 2026-08-24: Anthropic's panel moved the week reset from
    # "Aug 29, 2pm" to "Aug 29 at 2pm". The comma was never the contract.
    zone = claude_usage.ZoneInfo("Europe/Paris")
    now = datetime(2026, 8, 24, 11, 40, tzinfo=timezone.utc)
    assert claude_usage._reset_epoch("Aug 29 at 2pm (Europe/Paris)", now=now) == (
        datetime(2026, 8, 29, 14, 0, tzinfo=zone).timestamp()
    )
    assert claude_usage._reset_epoch("Aug 29 at 1:59pm (Europe/Paris)", now=now) == (
        datetime(2026, 8, 29, 13, 59, tzinfo=zone).timestamp()
    )


def test_reset_epoch_day_number_is_never_read_as_the_hour():
    # "Aug 29 at 2pm" carries two numbers; the clock scan starts after the
    # date so 29 can never become an hour, and a date-only string stays
    # midnight rather than borrowing its own day number.
    now = datetime(2026, 8, 24, 11, 40, tzinfo=timezone.utc)
    zone = claude_usage.ZoneInfo("Europe/Paris")
    assert claude_usage._reset_epoch("Aug 29 (Europe/Paris)", now=now) == (
        datetime(2026, 8, 29, 0, 0, tzinfo=zone).timestamp()
    )


def test_reset_epoch_rejects_an_impossible_calendar_day():
    assert claude_usage._reset_epoch("Feb 30, 1am (Europe/Paris)") is None


def test_parse_usage_text_gives_the_week_window_an_epoch_in_the_at_form():
    # The regression the dashboard showed: the bar kept its percentage and the
    # countdown chip vanished, because `week_resets_at` alone went None while
    # `week_reset` still carried the text. Drive the real captured panel.
    levels = claude_usage.parse_usage_text(
        "Current session\n2% 2% used\nResets 5:40pm (Europe/Paris)\n"
        "Current week (all models)\n16% 16% used\n"
        "Resets Aug 29 at 1:59pm (Europe/Paris)\n"
        "Current week (Fable)\n9% 9% used\n"
        "Resets Aug 29 at 1:59pm (Europe/Paris)\n"
    )

    assert levels["week_reset"] == "Aug 29 at 1:59pm (Europe/Paris)"
    assert isinstance(levels["week_resets_at"], float)
    assert isinstance(levels["session_resets_at"], float)
    assert isinstance(levels["week_models"]["Fable"]["resets_at"], float)
    # Same instant as the primary week window ⇒ elided from the summary.
    assert levels["quota"]["summary"] == (
        "session 98% left (resets 5:40pm (Europe/Paris)); "
        "week 84% left (resets Aug 29 at 1:59pm (Europe/Paris)); "
        "Fable week 91% left"
    )

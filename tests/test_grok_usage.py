"""Quota values grounded in captured Grok Build 1.0.50 PTY bytes."""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

from brr import daemon, facets, grok_status, grok_usage, hooks, run_ledger, runner_quota
from brr.gates import cloud

FIXTURES = Path(__file__).parent / "fixtures"


def screen():
    return (FIXTURES / "grok_usage_screen_exhausted.txt").read_bytes()


def test_captured_exhausted_panel_values(monkeypatch):
    monkeypatch.setattr(grok_usage, "_reset_epoch", lambda reset: 1791806340.0)
    levels = grok_usage.parse_usage_text(screen())
    assert levels["plan_type"] == "SuperGrok"
    assert levels["week_used_percentage"] == 100.0
    assert levels["week_reset"] == "October 12, 13:59"
    assert levels["quota"]["buckets"]["week"]["remaining_percentage"] == 0.0
    assert levels["quota"]["week_resets_at"] == 1791806340.0
    assert levels["quota"]["summary"] == (
        "week 0% left (resets Oct 12, 11:59am (UTC))"
    )
    assert "session_used_percentage" not in levels
    assert "session" not in levels["quota"]["buckets"]
    assert runner_quota.binding_quota_remaining_pct(levels) == 0.0
    assert runner_quota.binding_quota_reset_epoch(levels) == 1791806340.0
    assert run_ledger.quota_used_percentages(levels) == (100.0, None)


def test_captured_starting_panel_does_not_fabricate_quota():
    raw = (FIXTURES / "grok_usage_screen_starting.txt").read_bytes()
    assert "quota" not in grok_usage.parse_usage_text(raw)


def test_footer_and_context_percent_are_not_quota():
    # Synthetic negative cases only; positive values above come from the capture.
    assert "quota" not in grok_usage.parse_usage_text(
        "Weekly limit left: 0%\nContext usage\n100%\nResets: October 12, 13:59"
    )
    assert "quota" not in grok_usage.parse_usage_text(
        "Weekly limit (SuperGrok)\nSession usage\n100%"
    )
    assert "quota" not in grok_usage.parse_usage_text(
        "Weekly limit (SuperGrok)\n101%"
    )


def test_reset_clock_uses_host_timezone_at_the_reset_date(monkeypatch):
    old_tz = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "Europe/Paris")
    time.tzset()
    try:
        assert grok_usage._reset_epoch(
            "October 12, 13:59", now=datetime(2026, 10, 9)
        ) == 1791806340.0
        # Year rollover and winter offset use the target date's local rules.
        assert grok_usage._reset_epoch(
            "January 2, 13:59", now=datetime(2026, 12, 30)
        ) == datetime(2027, 1, 2, 13, 59).timestamp()
        assert grok_usage._reset_epoch("February 30, 13:59") is None
        assert grok_usage._reset_epoch("unknown clock") is None
    finally:
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        time.tzset()


def test_cache_throttles_and_failed_refresh_does_not_claim_a_live_zero(tmp_path, monkeypatch):
    calls = []

    def capture(**kwargs):
        calls.append(kwargs)
        return screen()

    monkeypatch.setattr(grok_usage, "capture_usage_raw", capture)
    first = grok_usage.load_or_refresh_snapshot(tmp_path)
    assert first["week_used_percentage"] == 100.0
    assert grok_usage.load_or_refresh_snapshot(tmp_path) == first
    assert len(calls) == 1
    assert json.loads((tmp_path / grok_usage.SNAPSHOT_NAME).read_text()) == first
    monkeypatch.setattr(grok_usage, "capture_usage_raw", lambda **kw: b"")
    failed = grok_usage.load_or_refresh_snapshot(tmp_path, max_age_seconds=0)
    assert "quota" not in failed and failed["error"]
    assert grok_usage.load_snapshot(tmp_path) == failed


def test_corrupt_cache_reprobes_and_ttl_override(tmp_path, monkeypatch):
    (tmp_path / grok_usage.SNAPSHOT_NAME).write_text("broken JSON")
    calls = []
    monkeypatch.setattr(grok_usage, "capture_usage_raw", lambda **kw: calls.append(kw) or screen())
    levels = grok_usage.load_or_refresh_snapshot(tmp_path)
    assert levels["week_used_percentage"] == 100.0 and len(calls) == 1
    grok_usage.load_or_refresh_snapshot(tmp_path, env={grok_usage.TTL_ENV_VAR: "0"})
    assert len(calls) == 2


def test_pty_types_usage_and_isolates_inherited_git_pins(
    tmp_path, monkeypatch, _no_grok_usage_pty_scrape,
):
    binary = tmp_path / "grok"
    evidence = tmp_path / "probe.json"
    binary.write_text("#!" + sys.executable + "\n" + r'''
import json, os, sys, tty
from pathlib import Path
Path(os.environ['PROBE_EVIDENCE']).write_text(json.dumps({
    'cwd': os.getcwd(), 'argv': sys.argv[1:],
    'git_pins': {k: os.environ[k] for k in ('GIT_DIR', 'GIT_WORK_TREE') if k in os.environ},
}))
tty.setraw(sys.stdin.fileno())
os.write(1, b'\x1b[6n')
received = b''
while b'/usage\r' not in received:
    received += os.read(0, 1024)
os.write(1, Path(os.environ['PROBE_FIXTURE']).read_bytes())
''')
    binary.chmod(0o755)
    monkeypatch.setattr(grok_usage, "DEFAULT_BOOT_SECONDS", 0.3)
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ["PATH"],
               GIT_DIR="dangerous-pin", GIT_WORK_TREE=str(tmp_path),
               PROBE_EVIDENCE=str(evidence),
               PROBE_FIXTURE=str(FIXTURES / "grok_usage_screen_exhausted.txt"))
    raw = _no_grok_usage_pty_scrape(cwd=tmp_path, timeout_seconds=2, env=env)
    assert grok_usage.parse_usage_text(raw)["week_used_percentage"] == 100.0
    probe = json.loads(evidence.read_text())
    assert probe["git_pins"] == {}
    assert probe["cwd"] != str(tmp_path) and not Path(probe["cwd"]).exists()
    assert probe["argv"] == ["--fullscreen", "--no-alt-screen", "--no-subagents", "--no-auto-update"]


def test_probe_failure_is_best_effort(monkeypatch, _no_grok_usage_pty_scrape):
    monkeypatch.setattr(grok_usage, "capture_usage_raw", _no_grok_usage_pty_scrape)
    levels = grok_usage.capture_levels(env={"PATH": "/no-grok-here"})
    assert "quota" not in levels and levels["error"]


def test_daemon_merges_quota_preserving_own_tokens_and_read_only_boundaries(tmp_path, monkeypatch):
    shared, outbox = tmp_path / "shared", tmp_path / "outbox"
    usage = grok_usage.parse_usage_text(screen())
    grok_usage.write_snapshot(shared, usage)
    result = {"source": "grok result JSON", "run_id": "own",
              "tokens": {"input_tokens": 10},
              "spend": {"summary": "$0.42 this session"}}
    grok_status.write_snapshot(outbox, result)
    monkeypatch.setattr(grok_usage, "capture_usage_raw", lambda **kw: pytest.fail("read-only boundary probed"))
    levels, slots = daemon._collect_levels("grok-4.7", outbox, shared_dir=shared, refresh=False)
    assert slots == {"spend", "quota"}
    assert levels["quota"]["buckets"]["week"]["remaining_percentage"] == 0.0
    assert levels["tokens"]["input_tokens"] == 10 and levels["run_id"] == "own"
    assert levels["spend"]["summary"] == "$0.42 this session"
    assert levels["quota"]["updated_at"] == usage["updated_at"]
    projected = facets.build(levels=levels, levels_collector=slots)
    assert projected["quota"]["status"] == "known"
    reset_clause = hooks._QUOTA_RESET_RE.search(projected["quota"]["summary"])
    assert hooks._relative_reset(reset_clause["when"]) is not None
    assert hooks._quota_chip(projected).startswith("q W0↻")


def test_daemon_cold_shared_cache_and_cross_run_cost(tmp_path, monkeypatch):
    monkeypatch.setattr(grok_usage, "capture_usage_raw", lambda **kw: screen())
    grok_status.write_snapshot(tmp_path, {
        "source": "grok result JSON", "tokens": {"input_tokens": 99},
        "spend": {"summary": "$0.42 this session"},
    })
    levels, slots = daemon._collect_levels("grok", None, shared_dir=tmp_path)
    assert grok_usage.load_snapshot(tmp_path)["week_used_percentage"] == 100.0
    assert "tokens" not in levels
    assert "previous Grok session" in levels["spend"]["summary"]
    assert runner_quota.binding_quota_remaining_pct(levels) == 0.0


def test_dashboard_and_ledger_consume_weekly_quota(tmp_path, monkeypatch):
    monkeypatch.setattr(grok_usage, "capture_usage_raw", lambda **kw: screen())
    row = cloud._grok_quota_shell(tmp_path)
    assert row["shell"] == "grok" and row["plan_type"] == "SuperGrok"
    assert row["windows"] == [{
        "label": "weekly", "used": None, "limit": None, "percent": 0.0,
        "reset": "October 12, 13:59", "resets_at": grok_usage._reset_epoch("October 12, 13:59"),
    }]
    monkeypatch.setattr(cloud, "_claude_quota_shell", lambda cache: None)
    monkeypatch.setattr(cloud, "_codex_quota_shell", lambda cache: None)
    assert cloud._quota_snapshot(tmp_path) == [row]
    assert run_ledger.load_quota_levels(
        "grok", tmp_path, None, force_claude_refresh=True,
    )["week_used_percentage"] == 100.0


@pytest.fixture(autouse=True)
def _grok_on_path(monkeypatch):
    """Tests drive a fake capture; pretend the binary exists unless a test says not."""
    monkeypatch.setattr(grok_usage, "_grok_installed", lambda env=None: True)


def test_no_grok_binary_means_no_probe_and_no_error_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(grok_usage, "_grok_installed", lambda env=None: False)
    calls = []
    monkeypatch.setattr(grok_usage, "capture_usage_raw", lambda **kw: calls.append(kw) or screen())
    assert grok_usage.load_or_refresh_snapshot(tmp_path, max_age_seconds=0) is None
    assert calls == []
    assert not (tmp_path / grok_usage.SNAPSHOT_NAME).exists()


def test_publisher_reads_a_fresh_cache_without_reprobing(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(grok_usage, "capture_usage_raw", lambda **kw: calls.append(kw) or screen())
    cloud._grok_quota_shell(tmp_path)
    old = time.time() - 300  # inside the 600 s publish window
    os.utime(tmp_path / grok_usage.SNAPSHOT_NAME, (old, old))
    cloud._grok_quota_shell(tmp_path)
    assert len(calls) == 1
    from brr.gates import cloud_publisher
    assert cloud_publisher._GROK_QUOTA_PUBLISH_MAX_AGE_SECONDS >= 600
    assert grok_usage.DEFAULT_TTL_SECONDS >= 600

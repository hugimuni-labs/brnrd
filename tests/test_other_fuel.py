"""The other Shells' fuel: chip segment, stale mark, omit, portal-state rows."""

from __future__ import annotations

import time

from brr import daemon, facets, hooks, other_fuel

NOW = 1789403200.0  # 2026-09-14T16:26:40Z
FRESH = "2026-09-14T16:25:00Z"
OLD = "2026-09-14T16:00:00Z"  # 26 min before NOW


def _codex_levels(updated_at: str = FRESH, primary: float = 12.0) -> dict:
    return {
        "updated_at": updated_at,
        "quota": {
            "primary_remaining_percent": primary,
            "primary_window_minutes": 300,
            "primary_resets_at": NOW + 2 * 3600,
            "secondary_remaining_percent": 82.0,
            "secondary_window_minutes": 10080,
            "secondary_resets_at": NOW + 5 * 86400,
        },
    }


def _claude_levels() -> dict:
    return {
        "updated_at": FRESH,
        "quota": {
            "buckets": {
                "session": {"remaining_percentage": 93},
                "week": {"remaining_percentage": 75},
                "week_models": {"Fable": {"remaining_percentage": 1}},
            },
            "session_resets_at": NOW + 3600,
            "week_resets_at": NOW + 86400,
        },
    }


def test_row_shape_and_binding_bucket():
    row = other_fuel.fuel_row("codex", _codex_levels(), now=NOW)
    assert row == {
        "shell": "codex",
        "binding_remaining_pct": 12.0,
        "binding_bucket": "S",
        "resets_in": "2h00m",
        "read_at": FRESH,
        "stale": False,
        "buckets": [
            {"bucket": "S", "remaining_pct": 12.0, "resets_in": "2h00m"},
            {"bucket": "W", "remaining_pct": 82.0, "resets_in": "5d0h"},
        ],
    }


def test_claude_row_ignores_per_model_bucket():
    row = other_fuel.fuel_row("claude", _claude_levels(), now=NOW)
    assert row["binding_remaining_pct"] == 75 and row["binding_bucket"] == "W"


def test_chip_with_two_other_shells():
    rows = [
        other_fuel.fuel_row("codex", _codex_levels(), now=NOW),
        other_fuel.fuel_row("claude", _claude_levels(), now=NOW),
    ]
    assert other_fuel.chip(rows) == "codex S12↻2h00m·W82 · claude S93·W75↻1d0h"


def test_seat_chip_carries_own_and_others_and_omits_when_none():
    row = other_fuel.fuel_row("codex", _codex_levels(), now=NOW)
    resources = {"quota": {
        "status": "known", "summary": "session 93% left (resets 8pm (Europe/Paris))",
        "others": [row],
    }}
    chip = hooks._quota_chip(resources)
    assert chip.startswith("q S93") and chip.endswith(" · codex S12↻2h00m·W82")
    del resources["quota"]["others"]
    assert " · " not in hooks._quota_chip(resources)
    # others only (seat's own Shell unread): the segment still renders.
    assert hooks._quota_chip({"quota": {"status": "absent", "others": [row]}}) == (
        "q codex S12↻2h00m·W82"
    )


def test_stale_reading_is_marked_never_a_bare_number():
    row = other_fuel.fuel_row("codex", _codex_levels(updated_at=OLD), now=NOW)
    assert row["stale"] is True
    assert other_fuel.chip([row]) == "codex ?"
    unparseable = other_fuel.fuel_row("codex", _codex_levels(updated_at="nope"), now=NOW)
    assert unparseable["stale"] is True


def test_no_reading_is_omitted():
    assert other_fuel.fuel_row("codex", None, now=NOW) is None
    assert other_fuel.fuel_row("codex", {"updated_at": FRESH}, now=NOW) is None
    assert other_fuel.chip([]) is None


def test_quota_stamp_wins_over_fresh_spend_capture():
    levels = _claude_levels()
    levels["quota"]["updated_at"] = OLD
    row = other_fuel.fuel_row("claude", levels, now=NOW)
    assert row["read_at"] == OLD
    assert row["stale"] is True


def test_claude_fuel_is_dated_through_real_collector(tmp_path, monkeypatch):
    from brr import claude_usage

    monkeypatch.setattr(daemon, "_other_fuel_cache", {})
    shared = tmp_path / ".brr"
    outbox = shared / "outbox" / "evt-test"
    outbox.mkdir(parents=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    levels = _claude_levels()
    levels["updated_at"] = stamp
    claude_usage.write_snapshot(outbox, levels)
    rows = daemon._other_shells_fuel("codex", [{"shell": "claude"}], shared)
    assert len(rows) == 1
    assert rows[0]["stale"] is False
    assert rows[0]["read_at"] == stamp
    assert "claude S93" in other_fuel.chip(rows)


def test_portal_state_others_row_shape():
    row = other_fuel.fuel_row("codex", _codex_levels(), now=NOW)
    facet = facets.build(other_shells=[row])["quota"]
    other = facet["others"][0]
    assert {"shell", "binding_remaining_pct", "resets_in", "read_at"} <= set(other)
    assert (other["shell"], other["binding_remaining_pct"], other["resets_in"]) == (
        "codex", 12.0, "2h00m",
    )
    assert "others" not in facets.build()["quota"]


def test_daemon_reads_cache_only_skips_own_shell_and_caches(monkeypatch, tmp_path):
    calls: list[tuple[str, bool]] = []

    def fake_collect(runner_name, outbox, work, *, refresh=True, shared_dir=None, **_):
        calls.append((runner_name, refresh))
        return (_codex_levels(updated_at=time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime())) if runner_name == "codex" else None), False

    monkeypatch.setattr(daemon, "_collect_levels", fake_collect)
    monkeypatch.setattr(daemon, "_other_fuel_cache", {})
    catalog = [{"shell": "claude"}, {"shell": "codex"}, {"shell": "codex"}, {"shell": "gemini"}]
    rows = daemon._other_shells_fuel("claude", catalog, tmp_path)
    assert [r["shell"] for r in rows] == ["codex"] and rows[0]["stale"] is False
    assert calls == [("codex", False), ("gemini", False)]  # own skipped, never refresh
    daemon._other_shells_fuel("claude", catalog, tmp_path)
    assert len(calls) == 2  # TTL cache: second boundary does no read
    assert daemon._other_shells_fuel("claude", catalog, None) == []


def test_heartbeat_warms_grok_and_seed_and_boundary_show_recorded_fuel(tmp_path, monkeypatch):
    """Cold daemon, no cloud publisher: the seat still sees Grok's pool."""
    import dataclasses
    import json
    from pathlib import Path
    from brr import codex_status, codex_usage, grok_usage, hud
    from brr.run import Run
    from test_hooks import _env

    fixtures = Path(__file__).parent / "fixtures"
    codex = json.loads((fixtures / "codex_rate_limits_2026_10_12.json").read_text())
    grok = (fixtures / "grok_billing_2026_10_17.json").read_bytes()
    monkeypatch.setattr(codex_status, "load_levels", lambda **kw: None)
    monkeypatch.setattr(codex_usage, "probe_rate_limits", lambda **kw: codex_usage.parse_rate_limits(codex))
    monkeypatch.setattr(grok_usage, "_grok_installed", lambda env=None: True)
    calls = []
    monkeypatch.setattr(grok_usage, "capture_usage_raw", lambda **kw: calls.append(1) or grok)
    monkeypatch.setattr(daemon, "_other_fuel_cache", {})
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    inputs = hud.HUDInputs(
        outbox_dir=tmp_path / "outbox", inbox_dir=inbox, current_event_id="evt-1",
        task=Run(id="run-1", event_id="evt-1", body="", source="telegram"),
        phase="running", runner_name="codex", runner_meta={"shell": "codex"},
        runner_catalog=[{"shell": "codex"}, {"shell": "grok"}, {"shell": "grok"}],
        brr_dir=tmp_path / ".brr", refresh_levels=False,
    )
    cold = hud.build(inputs)
    assert "grok" not in (hooks._quota_chip(cold.resources) or "")
    assert calls == []
    warm = hud.build(dataclasses.replace(inputs, refresh_levels=True))
    portal = inputs.outbox_dir / "portal-state.json"
    portal.parent.mkdir(parents=True, exist_ok=True)
    portal.write_text(warm.to_json())
    env = _env(inputs.outbox_dir, "codex")
    seed, _ = hooks.run_hook(hooks.PHASE_SESSION_START, "{}", env)
    text = seed["hookSpecificOutput"]["additionalContext"]
    quota_line = next(line for line in text.splitlines() if "resources:" in line)
    assert "quota=" in quota_line and "grok W45" in quota_line
    boundary, _ = hooks.run_hook(hooks.PHASE_POST_TOOL, "{}", env)
    assert "grok W45" in boundary["hookSpecificOutput"]["additionalContext"]
    # Boundaries re-read caches; heartbeat repeats use the native 600s TTL.
    def no_probe(**kwargs):
        raise AssertionError("warm boundary must not make another Grok billing request")
    monkeypatch.setattr(grok_usage, "capture_usage_raw", no_probe)
    assert "grok W45" in hooks._quota_chip(hud.build(inputs).resources)
    assert "grok W45" in hooks._quota_chip(hud.build(dataclasses.replace(inputs, refresh_levels=True)).resources)
    assert calls == [1]

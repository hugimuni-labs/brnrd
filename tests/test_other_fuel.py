"""The other Shells' fuel: chip segment, stale mark, omit, portal-state rows."""

from __future__ import annotations


from brr import daemon, hooks

NOW = 1789403200.0  # 2026-09-14T16:26:40Z
FRESH = "2026-09-14T16:25:00Z"
OLD = "2026-09-14T16:00:00Z"  # 26 min before NOW


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

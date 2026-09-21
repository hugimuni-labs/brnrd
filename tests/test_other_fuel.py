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

"""Tests for self-scheduled thoughts (`brr.schedule`)."""

from __future__ import annotations

from pathlib import Path

from brr import schedule


def _write(dom: Path, text: str) -> Path:
    dom.mkdir(parents=True, exist_ok=True)
    (dom / schedule.SCHEDULE_FILE).write_text(text, encoding="utf-8")
    return dom


# ── duration / iso parsing ──────────────────────────────────────────


def test_parse_duration_units_and_sums():
    assert schedule.parse_duration("45s") == 45
    assert schedule.parse_duration("30m") == 1800
    assert schedule.parse_duration("1h") == 3600
    assert schedule.parse_duration("2d") == 172800
    assert schedule.parse_duration("1h30m") == 5400


def test_parse_duration_rejects_garbage():
    assert schedule.parse_duration("soon") is None
    assert schedule.parse_duration("1 week") is None
    assert schedule.parse_duration("") is None


def test_parse_iso_handles_z_and_naive():
    z = schedule.parse_iso("2026-06-10T09:00:00Z")
    offset = schedule.parse_iso("2026-06-10T09:00:00+00:00")
    naive = schedule.parse_iso("2026-06-10T09:00:00")
    assert z == offset == naive
    assert schedule.parse_iso("not-a-date") is None


# ── schedule.md parsing ──────────────────────────────────────────────


def test_parse_missing_file_is_empty(tmp_path: Path):
    assert schedule.parse_schedule(tmp_path / "dom") == []


def test_parse_every_entry(tmp_path: Path):
    dom = _write(tmp_path / "dom", "## Reconcile Dominion\nevery: 24h\ndo upkeep\n")
    (e,) = schedule.parse_schedule(dom)
    assert e.id == "reconcile-dominion"
    assert e.kind == "every"
    assert e.interval == 86400
    assert e.body == "do upkeep"


def test_parse_at_entry(tmp_path: Path):
    dom = _write(tmp_path / "dom", "## Followup\nat: 2026-06-10T09:00:00Z\ncheck CI\n")
    (e,) = schedule.parse_schedule(dom)
    assert e.kind == "at"
    assert e.at == schedule.parse_iso("2026-06-10T09:00:00Z")
    assert e.body == "check CI"


def test_parse_reads_optional_conversation_key(tmp_path: Path):
    dom = _write(
        tmp_path / "dom",
        "## Daily standup\nevery: 24h\nconversation_key: telegram:55:\nPost a summary\n",
    )
    (e,) = schedule.parse_schedule(dom)
    # The value keeps its inner colons (gate-thread fingerprint).
    assert e.conversation_key == "telegram:55:"
    assert e.body == "Post a summary"


def test_parse_conversation_key_optional(tmp_path: Path):
    dom = _write(tmp_path / "dom", "## Ping\nevery: 1h\ndo a thing\n")
    (e,) = schedule.parse_schedule(dom)
    assert e.conversation_key is None


def test_parse_reads_optional_reset_on(tmp_path: Path):
    dom = _write(
        tmp_path / "dom",
        "## director tick\nevery: 5h\nreset_on: spawn\nRe-derive the plan\n",
    )
    (e,) = schedule.parse_schedule(dom)
    assert e.id == "director-tick"
    assert e.reset_on == "spawn"
    assert e.body == "Re-derive the plan"


def test_parse_reset_on_optional(tmp_path: Path):
    dom = _write(tmp_path / "dom", "## Ping\nevery: 1h\ndo a thing\n")
    (e,) = schedule.parse_schedule(dom)
    assert e.reset_on is None


def test_parse_ignores_preamble_and_inert_entries(tmp_path: Path):
    dom = _write(
        tmp_path / "dom",
        "# header comment\nevery: ignored-before-heading\n\n"
        "## No trigger\njust prose\n\n"
        "## Bad duration\nevery: whenever\n\n"
        "## Good\nevery: 1h\nrun it\n",
    )
    ids = [e.id for e in schedule.parse_schedule(dom)]
    assert ids == ["good"]


def test_parse_every_wins_when_both_present(tmp_path: Path):
    dom = _write(tmp_path / "dom", "## Both\nevery: 1h\nat: 2026-06-10T09:00:00Z\nx\n")
    (e,) = schedule.parse_schedule(dom)
    assert e.kind == "every"


# ── due computation ──────────────────────────────────────────────────


def test_every_anchors_on_first_sight_without_firing():
    e = schedule.ScheduleEntry("x", "every", "", interval=3600)
    due, state = schedule.due_entries([e], {}, now=1000.0)
    assert due == []
    assert state["x"]["last_fired"] == 1000.0


def test_every_fires_after_interval():
    e = schedule.ScheduleEntry("x", "every", "", interval=3600)
    state = {"x": {"kind": "every", "last_fired": 1000.0}}
    early, _ = schedule.due_entries([e], state, now=1000.0 + 3599)
    assert early == []
    due, new_state = schedule.due_entries([e], state, now=1000.0 + 3600)
    assert [d.id for d in due] == ["x"]
    assert new_state["x"]["last_fired"] == 1000.0 + 3600


def test_at_fires_once_then_not_again():
    at = 5000.0
    e = schedule.ScheduleEntry("y", "at", "", at=at)
    not_yet, _ = schedule.due_entries([e], {}, now=at - 1)
    assert not_yet == []
    due, state = schedule.due_entries([e], {}, now=at + 1)
    assert [d.id for d in due] == ["y"]
    assert state["y"]["fired"] is True
    again, _ = schedule.due_entries([e], state, now=at + 100)
    assert again == []


def test_at_stale_one_shot_is_anchored_not_fired():
    at = 1000.0
    e = schedule.ScheduleEntry("z", "at", "", at=at)
    due, state = schedule.due_entries(
        [e], {}, now=at + schedule.DEFAULT_STALE_GRACE_S + 1,
    )
    assert due == []
    assert state["z"]["fired"] is True  # anchored so it won't fire later


def test_state_prunes_removed_entries():
    e = schedule.ScheduleEntry("keep", "every", "", interval=60)
    state = {
        "keep": {"kind": "every", "last_fired": 10.0},
        "gone": {"kind": "every", "last_fired": 10.0},
    }
    _, new_state = schedule.due_entries([e], state, now=20.0)
    assert "gone" not in new_state
    assert "keep" in new_state


# ── state persistence ────────────────────────────────────────────────


def test_state_round_trip(tmp_path: Path):
    brr = tmp_path / ".brr"
    brr.mkdir()
    assert schedule.load_state(brr) == {}
    schedule.save_state(brr, {"a": {"last_fired": 1.0}})
    assert schedule.load_state(brr) == {"a": {"last_fired": 1.0}}


# ── reset-on signals (director-tick-after-spawn feature) ─────────────


def test_record_signal_round_trips(tmp_path: Path):
    brr = tmp_path / ".brr"
    brr.mkdir()
    assert schedule.load_signals(brr) == {}
    schedule.record_signal(brr, "spawn", now=1234.5)
    assert schedule.load_signals(brr) == {"spawn": 1234.5}
    # A second signal is added, not clobbering the first.
    schedule.record_signal(brr, "other", now=1300.0)
    assert schedule.load_signals(brr) == {"spawn": 1234.5, "other": 1300.0}


def test_apply_reset_signals_pushes_cooldown_to_signal_time():
    e = schedule.ScheduleEntry(
        "director-tick", "every", "", interval=5 * 3600, reset_on="spawn",
    )
    state = {"director-tick": {"kind": "every", "last_fired": 1000.0}}
    signals = {"spawn": 4000.0}
    new_state = schedule.apply_reset_signals([e], state, signals, now=4000.0)
    assert new_state["director-tick"]["last_fired"] == 4000.0

    # The reset means the tick is not due right after, even though its
    # original interval (from the stale last_fired) would have made it due.
    due, _ = schedule.due_entries([e], new_state, now=4000.0 + 1)
    assert due == []


def test_apply_reset_signals_never_moves_last_fired_backwards():
    e = schedule.ScheduleEntry(
        "director-tick", "every", "", interval=5 * 3600, reset_on="spawn",
    )
    state = {"director-tick": {"kind": "every", "last_fired": 9000.0}}
    signals = {"spawn": 4000.0}  # an older signal than the last real firing
    new_state = schedule.apply_reset_signals([e], state, signals, now=9500.0)
    assert new_state["director-tick"]["last_fired"] == 9000.0


def test_apply_reset_signals_ignores_entries_without_reset_on():
    e = schedule.ScheduleEntry("other", "every", "", interval=3600)
    state = {"other": {"kind": "every", "last_fired": 1000.0}}
    new_state = schedule.apply_reset_signals(
        [e], state, {"spawn": 4000.0}, now=4000.0,
    )
    assert new_state == state


def test_apply_reset_signals_no_signals_is_a_noop():
    e = schedule.ScheduleEntry(
        "director-tick", "every", "", interval=3600, reset_on="spawn",
    )
    state = {"director-tick": {"kind": "every", "last_fired": 1000.0}}
    assert schedule.apply_reset_signals([e], state, {}, now=5000.0) == state

"""Claude interactive /usage quota collector."""

import json
from datetime import datetime, timezone

from datetime import timedelta

from brr import claude_usage


def _now_stamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def _usage_snapshot(stamp, *, credits=True, models=True):
    levels = {
        "source": "claude /usage PTY",
        "updated_at": stamp,
        "session_used_percentage": 6.0,
        "quota": {"summary": "session 94% left", "buckets": {"session": {"remaining_percentage": 94.0}}},
    }
    if credits:
        levels["usage_credits"] = {
            "spent_amount": 4.2,
            "limit_amount": 50.0,
            "summary": "usage credits $4.20 of $50.00",
        }
    if models:
        levels["week_models"] = {"fable": {"used_percentage": 75.0, "reset": "Jul 17"}}
        levels["quota"]["buckets"]["week_models"] = {"fable": {"remaining_percentage": 25.0}}
    return levels


def test_a_rate_limited_scrape_does_not_erase_credits_it_never_saw(tmp_path, monkeypatch):
    """The reported loss (2026-07-13): the Claude credits row disappeared from
    the dashboard. Not the parser, not the account — `/usage` fetches its
    per-model and credits region asynchronously and prints "unavailable (rate
    limited)" when it can't, so a heartbeat refresh that lands in that window
    parses cleanly with those sections simply *absent*, and the snapshot write
    replaced a complete reading with a partial one. A section that failed to
    render is not a section that is gone."""
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    claude_usage.write_snapshot(outbox, _usage_snapshot(_now_stamp()))
    monkeypatch.setattr(
        claude_usage,
        "capture_levels",
        lambda **kw: _usage_snapshot(_now_stamp(), credits=False, models=False),
    )

    levels = claude_usage.load_or_refresh_snapshot(outbox, max_age_seconds=0)

    assert levels is not None
    assert levels["usage_credits"]["spent_amount"] == 4.2
    # …and it says out loud that it wasn't seen this time round: a dollar
    # figure must never pass itself off as freshly scraped.
    assert levels["usage_credits"]["carried_from"]
    # The Fable weekly row rides the same async region and carries with it,
    # including its pacing bucket — the two halves of one reading stay in step.
    assert levels["week_models"]["fable"]["used_percentage"] == 75.0
    assert levels["quota"]["buckets"]["week_models"]["fable"]["remaining_percentage"] == 25.0
    # Everything the scrape *did* prove is the fresh reading, not the old one.
    assert levels["session_used_percentage"] == 6.0


def test_credits_turned_off_overwrites_rather_than_carrying(tmp_path, monkeypatch):
    """An explicit "usage credits are off" is the panel stating a fact, not
    failing to render one — it replaces the carried block immediately, or the
    dashboard would haunt an account that has genuinely switched them off."""
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    claude_usage.write_snapshot(outbox, _usage_snapshot(_now_stamp()))
    off = _usage_snapshot(_now_stamp(), credits=False)
    off["usage_credits"] = {"enabled": False, "summary": "usage credits off"}
    monkeypatch.setattr(claude_usage, "capture_levels", lambda **kw: off)

    levels = claude_usage.load_or_refresh_snapshot(outbox, max_age_seconds=0)

    assert levels["usage_credits"] == {"enabled": False, "summary": "usage credits off"}
    assert "carried_from" not in levels["usage_credits"]


def test_a_section_stops_carrying_once_the_reading_is_stale(tmp_path, monkeypatch):
    """Carrying is a bridge across a flaky panel, not a preservation order: a
    reading old enough that the account could plausibly have changed underneath
    it drops out rather than being shown as if it were still true."""
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    old = datetime.now(timezone.utc) - timedelta(hours=13)
    claude_usage.write_snapshot(
        outbox, _usage_snapshot(old.strftime("%Y-%m-%dT%H:%M:%SZ"))
    )
    monkeypatch.setattr(
        claude_usage,
        "capture_levels",
        lambda **kw: _usage_snapshot(_now_stamp(), credits=False, models=False),
    )

    levels = claude_usage.load_or_refresh_snapshot(outbox, max_age_seconds=0)

    assert "usage_credits" not in levels
    assert "week_models" not in levels


def test_carry_reaches_across_the_run_boundary(tmp_path, monkeypatch):
    """Snapshots are per-run, so a new run's outbox starts empty — and the run
    boundary is exactly where the reported loss became visible. A first scrape
    that lands on a rate-limited panel carries from the newest snapshot a
    sibling run left behind, rather than publishing the credits row as gone."""
    outbox_root = tmp_path / "outbox"
    old_run = outbox_root / "evt-old"
    new_run = outbox_root / "evt-new"
    old_run.mkdir(parents=True)
    new_run.mkdir(parents=True)
    claude_usage.write_snapshot(old_run, _usage_snapshot(_now_stamp()))
    monkeypatch.setattr(
        claude_usage,
        "capture_levels",
        lambda **kw: _usage_snapshot(_now_stamp(), credits=False, models=False),
    )

    levels = claude_usage.load_or_refresh_snapshot(new_run, max_age_seconds=0)

    assert levels["usage_credits"]["spent_amount"] == 4.2
    assert levels["usage_credits"]["carried_from"]
    assert levels["week_models"]["fable"]["used_percentage"] == 75.0


def test_carry_heals_a_hole_a_partial_scrape_already_wrote(tmp_path, monkeypatch):
    """The newest snapshot is often the *damaged* one — a partial scrape landed
    and wrote the hole to disk. Carrying "from the newest snapshot" would then
    carry the hole itself, and the credits row would stay gone until a complete
    scrape happened to land. Each section is taken from the newest snapshot that
    actually has it."""
    outbox_root = tmp_path / "outbox"
    complete = outbox_root / "evt-complete"
    damaged = outbox_root / "evt-damaged"
    complete.mkdir(parents=True)
    damaged.mkdir(parents=True)
    claude_usage.write_snapshot(complete, _usage_snapshot(_now_stamp()))
    # …and then a rate-limited scrape wrote a credits-less snapshot over it,
    # in a newer run's dir. That partial file is the freshest thing on disk.
    claude_usage.write_snapshot(
        damaged, _usage_snapshot(_now_stamp(), credits=False, models=False)
    )
    monkeypatch.setattr(
        claude_usage,
        "capture_levels",
        lambda **kw: _usage_snapshot(_now_stamp(), credits=False, models=False),
    )

    levels = claude_usage.load_or_refresh_snapshot(damaged, max_age_seconds=0)

    assert levels["usage_credits"]["spent_amount"] == 4.2
    assert levels["week_models"]["fable"]["used_percentage"] == 75.0


def test_a_total_miss_scrape_does_not_erase_a_prior_quota_reading(tmp_path, monkeypatch):
    """Issue #561, defect 1: when the PTY scrape renders no session/week rows
    at all, `parse_usage_text` returns no `quota` key and `capture_levels`
    stamps `error`. Before this fix `quota` was not in `_ASYNC_SECTIONS`, so
    that hole got written straight over a complete prior reading and became
    the newest snapshot on disk — the one
    `runner_quota.latest_claude_usage_outbox_dir` picks, silently blinding
    pacing. The scrape proved nothing; the known reading must survive."""
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    claude_usage.write_snapshot(outbox, _usage_snapshot(_now_stamp()))
    monkeypatch.setattr(
        claude_usage,
        "capture_levels",
        lambda **kw: {
            "source": "claude /usage PTY",
            "updated_at": _now_stamp(),
            "error": "no quota buckets parsed from /usage screen",
        },
    )

    levels = claude_usage.load_or_refresh_snapshot(outbox, max_age_seconds=0)

    assert levels["quota"]["summary"].startswith("session 94% left")
    # …and it says out loud that it wasn't seen this time round, the same
    # discipline `usage_credits` already uses.
    assert levels["quota"]["carried_from"]
    # Review fixup: `carried_from` alone has no reader for the `quota`
    # section — `gates.cloud` republishes only the *credits* copy. The
    # summary string is what the wake's posture line, the Runner line and
    # the dashboard all render, so the mark has to ride there or a carried
    # reading looks fresh everywhere anyone actually looks.
    assert f"[carried from {levels['quota']['carried_from']}]" in (
        levels["quota"]["summary"]
    )


def test_a_re_carried_quota_summary_is_marked_once_with_the_newest_source(tmp_path):
    """Two total-miss scrapes in a row must not accumulate marks — the
    summary carries one `[carried from …]`, naming the source it actually
    came from this round."""
    already = {
        "summary": "session 94% left [carried from 2026-07-23T04:00:00+00:00]",
        "carried_from": "2026-07-23T04:00:00+00:00",
        "buckets": {"session": {"remaining_percentage": 94.0}},
    }
    source_stamp = _now_stamp()
    fresh = claude_usage.carry_forward_sections(
        [{"updated_at": source_stamp, "quota": already}],
        {"source": "claude /usage PTY", "updated_at": _now_stamp()},
    )

    assert fresh["quota"]["summary"].count("[carried from") == 1
    assert "2026-07-23T04:00:00+00:00" not in fresh["quota"]["summary"]
    assert fresh["quota"]["carried_from"] == source_stamp
    assert f"[carried from {source_stamp}]" in fresh["quota"]["summary"]


def test_a_partial_but_present_quota_is_left_exactly_as_scraped(tmp_path, monkeypatch):
    """The boundary that matters: carrying is for the scrape that proved
    *nothing*, not for one that proved something incomplete. A fresh scrape
    that parsed even a partial quota block (session only, say) must be left
    exactly as parsed — patching it from history would let a stale reading
    silently override a fact the scrape just confirmed."""
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    claude_usage.write_snapshot(outbox, _usage_snapshot(_now_stamp()))
    partial_quota = {
        "summary": "session 10% left",
        "buckets": {"session": {"remaining_percentage": 10.0}},
    }
    monkeypatch.setattr(
        claude_usage,
        "capture_levels",
        lambda **kw: {
            "source": "claude /usage PTY",
            "updated_at": _now_stamp(),
            "quota": partial_quota,
        },
    )

    levels = claude_usage.load_or_refresh_snapshot(outbox, max_age_seconds=0)

    assert levels["quota"] == partial_quota
    assert "carried_from" not in levels["quota"]


def test_quota_carry_stops_once_the_reading_is_stale(tmp_path, monkeypatch):
    """Same staleness bound the other carried sections respect: a quota
    reading old enough that the account could plausibly have rolled over
    underneath it is not carried forward as if it were still true."""
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    old = datetime.now(timezone.utc) - timedelta(hours=13)
    claude_usage.write_snapshot(
        outbox, _usage_snapshot(old.strftime("%Y-%m-%dT%H:%M:%SZ"))
    )
    monkeypatch.setattr(
        claude_usage,
        "capture_levels",
        lambda **kw: {
            "source": "claude /usage PTY",
            "updated_at": _now_stamp(),
            "error": "no quota buckets parsed from /usage screen",
        },
    )

    levels = claude_usage.load_or_refresh_snapshot(outbox, max_age_seconds=0)

    assert "quota" not in levels


def test_carried_quota_does_not_import_a_week_models_bucket_from_a_different_source():
    """`quota` and `week_models` are carried independently — each searches the
    candidate list for its own newest source, and those sources can differ.
    If a carried quota's own record lacked a `week_models` pacing bucket, a
    week_models carry from a *different* snapshot must not be stitched in:
    that would pair one reading's session/week facts with another reading's
    per-model bucket, exactly the disagreement carrying is meant to prevent."""
    now = datetime.now(timezone.utc)
    quota_stamp = (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    models_stamp = (now - timedelta(minutes=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
    quota_source = {
        "updated_at": quota_stamp,
        "quota": {
            "summary": "session 94% left",
            "buckets": {"session": {"remaining_percentage": 94.0}},
        },
    }
    models_source = {
        "updated_at": models_stamp,
        "week_models": {"fable": {"used_percentage": 75.0}},
        "quota": {
            "summary": "session 90% left",
            "buckets": {
                "session": {"remaining_percentage": 90.0},
                "week_models": {"fable": {"remaining_percentage": 25.0}},
            },
        },
    }
    fresh = {
        "updated_at": _now_stamp(),
        "error": "no quota buckets parsed from /usage screen",
    }

    healed = claude_usage.carry_forward_sections([quota_source, models_source], fresh)

    assert healed["quota"]["carried_from"] == quota_stamp
    # quota_source itself had no week_models bucket — must not be patched in
    # from models_source's disagreeing reading.
    assert "week_models" not in healed["quota"]["buckets"]
    # The top-level week_models display section still carries independently.
    assert healed["week_models"]["fable"]["used_percentage"] == 75.0


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

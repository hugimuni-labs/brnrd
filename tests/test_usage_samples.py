"""The shell-agnostic quota sample store and the burn measured off it.

The burn scenarios here are ported from `test_codex_status.py`, which measured
the same thing by scanning Codex session rollouts. The discipline under test is
unchanged — same-window filtering, the minimum-span refusal, the non-monotonic
clamp, the `sustainable` rule — but the evidence now comes from one store fed by
the level reads brr already performs, for either Shell.
"""

import json
from datetime import datetime, timezone

import pytest

from brr import usage_samples


WEEK_RESETS = 1784490642.0
WEEK_MINUTES = 10080.0


def _write_samples(state_dir, rows):
    """A sample log carrying `(iso_timestamp, shell, used_percent)` rows."""
    state_dir.mkdir(parents=True, exist_ok=True)
    path = usage_samples.log_path(state_dir)
    lines = []
    for row in rows:
        stamp, shell, used = row[0], row[1], row[2]
        resets = row[3] if len(row) > 3 else WEEK_RESETS
        minutes = row[4] if len(row) > 4 else WEEK_MINUTES
        lines.append(
            json.dumps(
                {
                    "at": datetime.fromisoformat(
                        stamp.replace("Z", "+00:00")
                    ).timestamp(),
                    "shell": shell,
                    "used_percent": used,
                    "window_minutes": minutes,
                    "resets_at": resets,
                }
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --- burn: the ported discipline --------------------------------------------


def test_recent_burn_measures_the_climb_and_projects_the_landing(tmp_path):
    """The reading that replaces the 5h window OpenAI stopped publishing
    (2026-07-12): with only a weekly percentage left, "53% left" cannot say
    whether the account is drifting or sprinting. The burn does — measured off
    stored samples, never off a fabricated window."""
    now = datetime(2026, 7, 13, 18, 0, tzinfo=timezone.utc).timestamp()
    _write_samples(
        tmp_path / ".brr",
        [
            ("2026-07-13T14:00:00Z", "codex", 26.0),
            ("2026-07-13T16:00:00Z", "codex", 37.0),
            ("2026-07-13T18:00:00Z", "codex", 47.0),
        ],
    )
    burn = usage_samples.recent_burn(tmp_path / ".brr", "codex", now=now)
    assert burn is not None
    assert burn["window_minutes"] == 10080.0
    assert burn["burned_percent"] == 21.0          # 26% used → 47% used
    assert burn["to_remaining_percent"] == 53.0
    assert burn["span_minutes"] == 240.0
    # 21 points per 4h → 26.25 in the next 5h → 53 - 26.25 ≈ 26.8 left.
    assert burn["projected_remaining_percent"] == 26.8
    # …and empty ~10h out, well before the weekly window resets → not a pace
    # this account can hold.
    assert burn["sustainable"] is False
    assert burn["exhausts_at"] == pytest.approx(now + (53.0 / (21.0 / 240.0)) * 60.0)
    assert burn["source"] == "brr usage samples"


def test_recent_burn_works_for_claude_not_just_codex(tmp_path):
    """The whole point of the store. Claude's `/usage` scrape is a point reading
    with no on-disk history to recover, so burn was invisible on the Shell doing
    most of the spending. Same samples, same measurement, either Shell."""
    now = datetime(2026, 7, 13, 18, 0, tzinfo=timezone.utc).timestamp()
    _write_samples(
        tmp_path / ".brr",
        [
            ("2026-07-13T14:00:00Z", "claude", 26.0),
            ("2026-07-13T18:00:00Z", "claude", 47.0),
        ],
    )
    burn = usage_samples.recent_burn(tmp_path / ".brr", "claude", now=now)
    assert burn is not None
    assert burn["burned_percent"] == 21.0
    assert burn["to_remaining_percent"] == 53.0


def test_recent_burn_keeps_each_shell_to_its_own_samples(tmp_path):
    """One store, two accounts. A busy Codex week must never be read as Claude's
    burn — the shell key is part of the window's identity, not a label on it."""
    now = datetime(2026, 7, 13, 18, 0, tzinfo=timezone.utc).timestamp()
    _write_samples(
        tmp_path / ".brr",
        [
            ("2026-07-13T14:00:00Z", "codex", 10.0),
            ("2026-07-13T18:00:00Z", "codex", 90.0),
            ("2026-07-13T14:00:00Z", "claude", 20.0),
            ("2026-07-13T18:00:00Z", "claude", 24.0),
        ],
    )
    codex = usage_samples.recent_burn(tmp_path / ".brr", "codex", now=now)
    claude = usage_samples.recent_burn(tmp_path / ".brr", "claude", now=now)
    assert codex["burned_percent"] == 80.0
    assert claude["burned_percent"] == 4.0


def test_recent_burn_ignores_samples_from_a_window_that_has_since_reset(tmp_path):
    """A spent reset credit restarts the window: `used_percent` drops back to
    near zero (live 2026-07-12, weekly 39% → 1%). Measured naively that reads as
    a *negative* burn and would paint a sprinting account as idle. Only samples
    from the window that is currently live count."""
    now = datetime(2026, 7, 13, 18, 0, tzinfo=timezone.utc).timestamp()
    _write_samples(
        tmp_path / ".brr",
        [
            ("2026-07-13T14:00:00Z", "codex", 39.0, 1783000000.0),
            ("2026-07-13T14:30:00Z", "codex", 40.0, 1783000000.0),
            ("2026-07-13T15:00:00Z", "codex", 1.0, WEEK_RESETS),
            ("2026-07-13T18:00:00Z", "codex", 2.0, WEEK_RESETS),
        ],
    )
    burn = usage_samples.recent_burn(tmp_path / ".brr", "codex", now=now)
    assert burn is not None
    assert burn["samples"] == 2
    assert burn["burned_percent"] == 1.0        # not −38: the old window is gone
    assert burn["to_remaining_percent"] == 98.0
    # 1 point in 3h → ~294h to empty, and the weekly window (2026-07-19) resets
    # ~145h out: the window wins the race, so this pace is one you can hold.
    assert burn["sustainable"] is True


def test_recent_burn_measures_the_longest_window_on_record(tmp_path):
    """Where both a 5h and a weekly window are stored, the weekly one is the
    reading: the subscription ceiling that matters is the one you can't wait
    out."""
    now = datetime(2026, 7, 13, 18, 0, tzinfo=timezone.utc).timestamp()
    _write_samples(
        tmp_path / ".brr",
        [
            ("2026-07-13T14:00:00Z", "claude", 80.0, 1784300000.0, 300.0),
            ("2026-07-13T18:00:00Z", "claude", 95.0, 1784300000.0, 300.0),
            ("2026-07-13T14:00:00Z", "claude", 20.0, WEEK_RESETS, WEEK_MINUTES),
            ("2026-07-13T18:00:00Z", "claude", 24.0, WEEK_RESETS, WEEK_MINUTES),
        ],
    )
    burn = usage_samples.recent_burn(tmp_path / ".brr", "claude", now=now)
    assert burn["window_minutes"] == WEEK_MINUTES
    assert burn["burned_percent"] == 4.0


def test_recent_burn_refuses_to_project_from_a_span_too_short_to_mean_anything(
    tmp_path,
):
    """Two samples ten minutes apart can 'prove' any rate at all. A projection
    built on that is a guess wearing a bar — and the whole point of this reading
    is that it replaced a bar which had stopped being true."""
    now = datetime(2026, 7, 13, 18, 0, tzinfo=timezone.utc).timestamp()
    _write_samples(
        tmp_path / ".brr",
        [
            ("2026-07-13T17:50:00Z", "codex", 40.0),
            ("2026-07-13T18:00:00Z", "codex", 47.0),
        ],
    )
    assert usage_samples.recent_burn(tmp_path / ".brr", "codex", now=now) is None


def test_recent_burn_refuses_on_a_single_sample(tmp_path):
    """One reading is a level, not a rate. The blind period right after deploy
    lands here, and returning None is the honest answer for it."""
    now = datetime(2026, 7, 13, 18, 0, tzinfo=timezone.utc).timestamp()
    _write_samples(tmp_path / ".brr", [("2026-07-13T14:00:00Z", "codex", 40.0)])
    assert usage_samples.recent_burn(tmp_path / ".brr", "codex", now=now) is None


def test_recent_burn_clamps_a_non_monotonic_dip_rather_than_reporting_negative(
    tmp_path,
):
    """`used_percent` can dip inside a live window — the providers' own
    accounting is not strictly monotonic. A negative burn is never a fact about
    spending."""
    now = datetime(2026, 7, 13, 18, 0, tzinfo=timezone.utc).timestamp()
    _write_samples(
        tmp_path / ".brr",
        [
            ("2026-07-13T14:00:00Z", "codex", 41.0),
            ("2026-07-13T18:00:00Z", "codex", 40.0),
        ],
    )
    burn = usage_samples.recent_burn(tmp_path / ".brr", "codex", now=now)
    assert burn["burned_percent"] == 0.0
    assert burn["sustainable"] is True


def test_recent_burn_absent_without_a_store(tmp_path):
    assert usage_samples.recent_burn(tmp_path / "absent", "codex") is None
    assert usage_samples.recent_burn(None, "codex") is None


def test_recent_burn_ignores_samples_older_than_the_horizon(tmp_path):
    """Yesterday's pace is not this hour's. Samples outside the trailing horizon
    are not evidence about now."""
    now = datetime(2026, 7, 13, 18, 0, tzinfo=timezone.utc).timestamp()
    _write_samples(
        tmp_path / ".brr",
        [
            ("2026-07-13T02:00:00Z", "codex", 10.0),
            ("2026-07-13T03:00:00Z", "codex", 12.0),
        ],
    )
    assert usage_samples.recent_burn(tmp_path / ".brr", "codex", now=now) is None


# --- the store: recording -----------------------------------------------------


def test_record_captures_both_windows_from_a_codex_levels_snapshot(tmp_path):
    """Codex nests its windows under `quota.{primary,secondary}_*`, and the slot
    is not the window's identity — duration comes from `*_window_minutes`."""
    levels = {
        "quota": {
            "primary_used_percent": 47.0,
            "primary_window_minutes": 10080.0,
            "primary_resets_at": WEEK_RESETS,
            "secondary_used_percent": 12.0,
            "secondary_window_minutes": 300.0,
            "secondary_resets_at": 1784300000.0,
        }
    }
    written = usage_samples.record(tmp_path / ".brr", "codex", levels, now=1000.0)
    assert written == 2
    rows = [
        json.loads(line)
        for line in usage_samples.log_path(tmp_path / ".brr")
        .read_text()
        .splitlines()
    ]
    assert {r["window_minutes"] for r in rows} == {10080.0, 300.0}
    assert all(r["shell"] == "codex" for r in rows)


def test_record_captures_both_buckets_from_a_claude_levels_snapshot(tmp_path):
    """Claude flattens `session_*` / `week_*` onto the top level with the
    duration implied by the bucket name — the shape the PTY scrape produces."""
    levels = {
        "session_used_percentage": 21.0,
        "session_resets_at": 1784300000.0,
        "week_used_percentage": 36.0,
        "week_resets_at": WEEK_RESETS,
    }
    written = usage_samples.record(tmp_path / ".brr", "claude", levels, now=1000.0)
    assert written == 2
    rows = [
        json.loads(line)
        for line in usage_samples.log_path(tmp_path / ".brr")
        .read_text()
        .splitlines()
    ]
    by_window = {r["window_minutes"]: r for r in rows}
    assert by_window[300.0]["used_percent"] == 21.0
    assert by_window[10080.0]["used_percent"] == 36.0


def test_record_skips_a_window_missing_its_reset_instant(tmp_path):
    """Burn identifies a window by duration *and* reset instant. A window that
    cannot be compared to itself is worth nothing to the store — Claude's
    `_reset_epoch` is computed and legitimately returns None."""
    levels = {
        "session_used_percentage": 21.0,
        "session_resets_at": None,
        "week_used_percentage": 36.0,
        "week_resets_at": WEEK_RESETS,
    }
    assert usage_samples.record(tmp_path / ".brr", "claude", levels, now=1000.0) == 1


def test_record_throttles_repeat_reads_of_the_same_window(tmp_path):
    """The heartbeat runs every 30s and the publish paths add their own reads.
    Without a throttle the log carries several identical rows a minute for no
    added resolution."""
    levels = {"week_used_percentage": 36.0, "week_resets_at": WEEK_RESETS}
    assert usage_samples.record(tmp_path / ".brr", "claude", levels, now=1000.0) == 1
    assert usage_samples.record(tmp_path / ".brr", "claude", levels, now=1010.0) == 0
    assert usage_samples.record(tmp_path / ".brr", "claude", levels, now=1100.0) == 1


def test_record_never_throttles_across_a_reset(tmp_path):
    """A genuine reset mints a new `resets_at`, and that is exactly the moment
    worth catching. It must never be swallowed as a duplicate of the window it
    replaced."""
    first = {"week_used_percentage": 96.0, "week_resets_at": WEEK_RESETS}
    after = {"week_used_percentage": 1.0, "week_resets_at": WEEK_RESETS + 604800.0}
    assert usage_samples.record(tmp_path / ".brr", "claude", first, now=1000.0) == 1
    assert usage_samples.record(tmp_path / ".brr", "claude", after, now=1005.0) == 1


def test_record_prunes_samples_past_the_retention_horizon(tmp_path):
    """Append-only, but bounded: the log is never a file anyone has to think
    about."""
    state = tmp_path / ".brr"
    _write_samples(
        state,
        [
            ("2026-07-01T00:00:00Z", "codex", 10.0),
            ("2026-07-13T17:00:00Z", "codex", 40.0),
        ],
    )
    now = datetime(2026, 7, 13, 18, 0, tzinfo=timezone.utc).timestamp()
    levels = {
        "quota": {
            "primary_used_percent": 42.0,
            "primary_window_minutes": WEEK_MINUTES,
            "primary_resets_at": WEEK_RESETS,
        }
    }
    usage_samples.record(state, "codex", levels, now=now)
    rows = [
        json.loads(line)
        for line in usage_samples.log_path(state).read_text().splitlines()
    ]
    assert len(rows) == 2                       # the July-1 sample is gone
    assert min(r["at"] for r in rows) > now - 24 * 3600


def test_record_dates_a_sample_by_the_reading_not_by_the_look(tmp_path):
    """Two of the three level call sites read cache-only (`refresh=False`), so a
    reading can legitimately be minutes old. Stamping it "now" is the mistake
    this codebase already paid for on the display side (the lying usage panel,
    2026-07-07) — and on the measurement side it is worse: it invents a flat
    segment where nothing was observed and drags the burn toward zero."""
    levels = {
        "week_used_percentage": 36.0,
        "week_resets_at": WEEK_RESETS,
        "updated_at": "2026-07-13T14:00:00Z",
    }
    now = datetime(2026, 7, 13, 18, 0, tzinfo=timezone.utc).timestamp()
    usage_samples.record(tmp_path / ".brr", "claude", levels, now=now)
    row = json.loads(
        usage_samples.log_path(tmp_path / ".brr").read_text().splitlines()[0]
    )
    assert row["at"] == datetime(
        2026, 7, 13, 14, 0, tzinfo=timezone.utc
    ).timestamp()


def test_record_treats_a_re_read_of_one_cached_reading_as_a_duplicate(tmp_path):
    """Because samples are dated by the reading, polling a cache that has not
    refreshed adds nothing — which is the truth of it. The alternative is a
    series padded with fabricated observations."""
    levels = {
        "week_used_percentage": 36.0,
        "week_resets_at": WEEK_RESETS,
        "updated_at": "2026-07-13T14:00:00Z",
    }
    state = tmp_path / ".brr"
    base = datetime(2026, 7, 13, 14, 1, tzinfo=timezone.utc).timestamp()
    assert usage_samples.record(state, "claude", levels, now=base) == 1
    # An hour of polling later, the cache still holds the same scrape.
    assert usage_samples.record(state, "claude", levels, now=base + 3600) == 0


def test_record_falls_back_to_now_on_an_unusable_updated_at(tmp_path):
    """A missing, malformed, or clock-skewed-future stamp is not evidence about
    the past."""
    now = 1_784_000_000.0
    for stamp in (None, "not a date", "", 12345):
        state = tmp_path / f"brr-{stamp}"
        usage_samples.record(
            state,
            "claude",
            {
                "week_used_percentage": 36.0,
                "week_resets_at": WEEK_RESETS,
                "updated_at": stamp,
            },
            now=now,
        )
        row = json.loads(usage_samples.log_path(state).read_text().splitlines()[0])
        assert row["at"] == now


def test_record_is_silent_when_the_log_cannot_be_written(tmp_path):
    """A usage sample is telemetry about the work, never the work. Every I/O
    failure yields 0, never an exception — losing a sample must not fail a run."""
    state = tmp_path / ".brr"
    state.mkdir()
    # A directory where the log file belongs: every write path fails.
    usage_samples.log_path(state).mkdir()
    levels = {"week_used_percentage": 36.0, "week_resets_at": WEEK_RESETS}
    assert usage_samples.record(state, "claude", levels, now=1000.0) == 0


def test_record_tolerates_junk_shapes_without_raising(tmp_path):
    """The levels dicts are assembled from provider payloads brr does not own."""
    state = tmp_path / ".brr"
    for junk in (None, {}, {"quota": "nope"}, {"week_used_percentage": "abc"}, []):
        assert usage_samples.record(state, "claude", junk, now=1000.0) == 0
        assert usage_samples.record(state, "codex", junk, now=1000.0) == 0
    assert usage_samples.record(state, "gemini", {}, now=1000.0) == 0
    assert usage_samples.record(None, "claude", {}, now=1000.0) == 0


def test_recent_burn_survives_a_corrupt_log(tmp_path):
    """A truncated or half-written line is skipped, not raised on — the rest of
    the series is still evidence."""
    state = tmp_path / ".brr"
    state.mkdir()
    good = _write_samples(
        state,
        [
            ("2026-07-13T14:00:00Z", "codex", 26.0),
            ("2026-07-13T18:00:00Z", "codex", 47.0),
        ],
    )
    good.write_text(good.read_text() + "{not json at all\n\n", encoding="utf-8")
    now = datetime(2026, 7, 13, 18, 0, tzinfo=timezone.utc).timestamp()
    burn = usage_samples.recent_burn(state, "codex", now=now)
    assert burn is not None
    assert burn["burned_percent"] == 21.0


def test_record_then_measure_round_trips(tmp_path):
    """The seam as the daemon actually uses it: repeated level reads become a
    series, and the series becomes a burn."""
    state = tmp_path / ".brr"
    base = datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc).timestamp()
    for offset, used in ((0, 20.0), (3600, 24.0), (7200, 28.0)):
        usage_samples.record(
            state,
            "claude",
            {"week_used_percentage": used, "week_resets_at": WEEK_RESETS},
            now=base + offset,
        )
    burn = usage_samples.recent_burn(state, "claude", now=base + 7200)
    assert burn is not None
    assert burn["samples"] == 3
    assert burn["burned_percent"] == 8.0
    assert burn["span_minutes"] == 120.0

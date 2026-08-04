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


def test_parse_reads_reset_on_comma_list(tmp_path: Path):
    dom = _write(
        tmp_path / "dom",
        "## director tick\nevery: 5h\nreset_on: spawn, user-run\nRe-derive the plan\n",
    )
    (e,) = schedule.parse_schedule(dom)
    assert e.reset_on == "spawn, user-run"
    assert schedule.reset_on_names(e) == ["spawn", "user-run"]


def test_parse_reads_optional_premise(tmp_path: Path):
    dom = _write(
        tmp_path / "dom",
        "## Ship the thing\nat: 2026-06-10T09:00:00Z\n"
        "premise: the release branch is still green\ndeploy it\n",
    )
    (e,) = schedule.parse_schedule(dom)
    assert e.premise == "the release branch is still green"
    assert e.body == "deploy it"


def test_parse_premise_optional(tmp_path: Path):
    dom = _write(tmp_path / "dom", "## Followup\nat: 2026-06-10T09:00:00Z\ncheck CI\n")
    (e,) = schedule.parse_schedule(dom)
    assert e.premise is None


def test_parse_preserves_raw_heading(tmp_path: Path):
    dom = _write(
        tmp_path / "dom", "## Ship The Thing!\nat: 2026-06-10T09:00:00Z\ndeploy\n",
    )
    (e,) = schedule.parse_schedule(dom)
    assert e.id == "ship-the-thing"
    assert e.heading == "Ship The Thing!"


def test_parse_reads_optional_runner_fields(tmp_path: Path):
    dom = _write(
        tmp_path / "dom",
        "## Cheap sweep\nevery: 1h\n"
        "shell: codex\ncore: gpt-5.6-luna\nrunner: codex-mini\nrun it\n",
    )

    (e,) = schedule.parse_schedule(dom)

    assert e.shell == "codex"
    assert e.core == "gpt-5.6-luna"
    assert e.runner == "codex-mini"
    assert e.body == "run it"


def test_parse_runner_fields_default_to_none(tmp_path: Path):
    dom = _write(tmp_path / "dom", "## Ping\nevery: 1h\ndo a thing\n")

    (e,) = schedule.parse_schedule(dom)

    assert e.shell is None
    assert e.core is None
    assert e.runner is None


def test_parse_keeps_unknown_runner_value_for_daemon(tmp_path: Path):
    dom = _write(
        tmp_path / "dom",
        "## Typo\nevery: 1h\nshell: definitely-not-a-runner\ntry anyway\n",
    )

    (e,) = schedule.parse_schedule(dom)

    assert e.shell == "definitely-not-a-runner"


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


# ── armed dated-letters projection (#904) ────────────────────────────


def test_armed_at_entries_includes_unfired_at_excludes_every_and_fired():
    unfired = schedule.ScheduleEntry("y", "at", "check CI", at=5000.0)
    fired = schedule.ScheduleEntry("z", "at", "already done", at=1000.0)
    recurring = schedule.ScheduleEntry("w", "every", "upkeep", interval=60)
    state = {"z": {"kind": "at", "last_fired": 1000.0, "fired": True}}

    armed = schedule.armed_at_entries([unfired, fired, recurring], state)

    assert [e.id for e in armed] == ["y"]


def test_armed_letters_projects_when_heading_and_premise():
    e = schedule.ScheduleEntry(
        "ship-the-thing", "at", "deploy it", at=5000.0,
        raw_when="2026-06-10T09:00:00Z", heading="Ship the thing",
        premise="the release branch is still green",
    )
    rows = schedule.armed_letters([e], {})
    assert rows == [{
        "id": "ship-the-thing",
        "when": "2026-06-10T09:00:00Z",
        "at": 5000.0,
        "heading": "Ship the thing",
        "premise": "the release branch is still green",
    }]


def test_armed_letters_premise_none_when_not_stated():
    e = schedule.ScheduleEntry(
        "followup", "at", "check CI", at=5000.0, raw_when="2026-06-10T09:00:00Z",
        heading="Followup",
    )
    (row,) = schedule.armed_letters([e], {})
    assert row["premise"] is None


def test_armed_letters_falls_back_to_id_when_heading_missing():
    e = schedule.ScheduleEntry("followup", "at", "check CI", at=5000.0)
    (row,) = schedule.armed_letters([e], {})
    assert row["heading"] == "followup"


def test_armed_letters_excludes_fired_entries():
    e = schedule.ScheduleEntry("followup", "at", "check CI", at=1000.0)
    state = {"followup": {"kind": "at", "last_fired": 1000.0, "fired": True}}
    assert schedule.armed_letters([e], state) == []


def test_armed_letters_round_trips_through_disk(tmp_path: Path):
    brr = tmp_path / ".brr"
    brr.mkdir()
    assert schedule.load_armed_letters(brr) == []
    rows = [{
        "id": "followup", "when": "2026-06-10T09:00:00Z", "at": 5000.0,
        "heading": "Followup", "premise": None,
    }]
    schedule.save_armed_letters(brr, rows)
    assert schedule.load_armed_letters(brr) == rows


def test_load_armed_letters_tolerates_corrupt_file(tmp_path: Path):
    brr = tmp_path / ".brr" / "schedule"
    brr.mkdir(parents=True)
    (brr / schedule.ARMED_FILE).write_text("not json", encoding="utf-8")
    assert schedule.load_armed_letters(tmp_path / ".brr") == []


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


# ── reset_on comma list / user-run signal (#904 option 1) ────────────


def test_reset_on_names_splits_comma_list():
    e = schedule.ScheduleEntry(
        "director-tick", "every", "", interval=3600, reset_on="spawn, user-run",
    )
    assert schedule.reset_on_names(e) == ["spawn", "user-run"]


def test_reset_on_names_single_value():
    e = schedule.ScheduleEntry(
        "director-tick", "every", "", interval=3600, reset_on="spawn",
    )
    assert schedule.reset_on_names(e) == ["spawn"]


def test_reset_on_names_empty_when_unset():
    e = schedule.ScheduleEntry("director-tick", "every", "", interval=3600)
    assert schedule.reset_on_names(e) == []


def test_apply_reset_signals_comma_list_either_signal_resets():
    """A user ping (no concurrent spawn) still resets an entry whose
    `reset_on: spawn, user-run` opts into both signals."""
    e = schedule.ScheduleEntry(
        "director-tick", "every", "", interval=5 * 3600,
        reset_on="spawn, user-run",
    )
    state = {"director-tick": {"kind": "every", "last_fired": 1000.0}}
    signals = {"user-run": 4000.0}
    new_state = schedule.apply_reset_signals([e], state, signals, now=4000.0)
    assert new_state["director-tick"]["last_fired"] == 4000.0


def test_apply_reset_signals_comma_list_takes_most_recent_qualifying_signal():
    e = schedule.ScheduleEntry(
        "director-tick", "every", "", interval=5 * 3600,
        reset_on="spawn, user-run",
    )
    state = {"director-tick": {"kind": "every", "last_fired": 1000.0}}
    signals = {"spawn": 3000.0, "user-run": 4000.0}
    new_state = schedule.apply_reset_signals([e], state, signals, now=5000.0)
    assert new_state["director-tick"]["last_fired"] == 4000.0


def test_apply_reset_signals_comma_list_ignores_future_signal_of_either_name():
    e = schedule.ScheduleEntry(
        "director-tick", "every", "", interval=5 * 3600,
        reset_on="spawn, user-run",
    )
    state = {"director-tick": {"kind": "every", "last_fired": 1000.0}}
    signals = {"spawn": 1500.0, "user-run": 9999.0}  # user-run is in the future
    new_state = schedule.apply_reset_signals([e], state, signals, now=5000.0)
    assert new_state["director-tick"]["last_fired"] == 1500.0


def test_apply_reset_signals_default_behavior_unchanged_single_name():
    """Opt-in per entry, no default-behavior change: an entry that still
    names only `spawn` is unaffected by a `user-run` signal."""
    e = schedule.ScheduleEntry(
        "director-tick", "every", "", interval=5 * 3600, reset_on="spawn",
    )
    state = {"director-tick": {"kind": "every", "last_fired": 1000.0}}
    signals = {"user-run": 4000.0}
    new_state = schedule.apply_reset_signals([e], state, signals, now=4000.0)
    assert new_state == state


# ── lint_schedule (issue #579, mechanical half) ───────────────────────


def test_lint_empty_entries_is_empty():
    assert schedule.lint_schedule([], now=1000.0) == []


def test_lint_stale_at_flags_passed_instant():
    e = schedule.ScheduleEntry("y", "at", "check CI", at=1000.0, raw_when="2026-01-01T00:00:00Z")
    (finding,) = schedule.lint_schedule([e], now=2000.0)
    assert finding.rule == "stale-at"
    assert finding.entry_ids == ("y",)
    assert "passed" in finding.message


def test_lint_stale_at_ignores_future_instant():
    e = schedule.ScheduleEntry("y", "at", "check CI", at=3000.0)
    assert schedule.lint_schedule([e], now=2000.0) == []


def test_lint_stale_at_is_stronger_when_state_shows_it_already_fired():
    e = schedule.ScheduleEntry("y", "at", "check CI", at=1000.0)
    state = {"y": {"kind": "at", "fired": True, "last_fired": 1500.0}}
    (finding,) = schedule.lint_schedule([e], now=2000.0, state=state)
    assert finding.rule == "stale-at"
    assert "fired" in finding.message and "ago" in finding.message
    assert "nobody removed it" in finding.message


def test_lint_stale_at_not_yet_fired_reads_differently_from_already_fired():
    e = schedule.ScheduleEntry("y", "at", "check CI", at=1000.0)
    not_fired = schedule.lint_schedule([e], now=2000.0, state={})
    fired = schedule.lint_schedule(
        [e], now=2000.0, state={"y": {"kind": "at", "fired": True}},
    )
    assert not_fired[0].message != fired[0].message


def test_lint_overlap_trips_on_near_duplicate_every_bodies():
    # Paraphrase pair (~0.70 SequenceMatcher ratio) — see the
    # OVERLAP_RATIO_THRESHOLD comment in schedule.py for the calibration.
    a = schedule.ScheduleEntry(
        "hourly-dispatch", "every",
        "Every hour, check the queue for stalled release tickets and spawn a "
        "co-maintainer to pick up to two bounded, mechanical issues with "
        "concrete touch points. Skip when quota is under 25% or the spawn "
        "pool has fewer than two free slots. Post one line to the "
        "maintainer thread naming what was dispatched.",
        interval=3600,
    )
    b = schedule.ScheduleEntry(
        "hourly-redispatch", "every",
        "Every hour, scan the queue for stalled release tickets and dispatch "
        "a co-maintainer to pick up to two bounded, mechanical issues with "
        "concrete touch points. Skip when quota is below 25% or the spawn "
        "pool has under two free slots. Post a one-line note to the "
        "maintainer thread naming what was sent out.",
        interval=3600,
    )
    (finding,) = schedule.lint_schedule([a, b], now=1000.0)
    assert finding.rule == "overlap"
    assert finding.entry_ids == ("hourly-dispatch", "hourly-redispatch")


def test_lint_overlap_does_not_trip_on_unrelated_entries():
    a = schedule.ScheduleEntry(
        "morning-digest", "every",
        "Post a daily summary of open PRs to the telegram thread every "
        "morning.",
        interval=86400,
    )
    b = schedule.ScheduleEntry(
        "cache-rotate", "every",
        "Rotate the local forge PR cache and prune worktrees older than a "
        "week.",
        interval=86400,
    )
    assert schedule.lint_schedule([a, b], now=1000.0) == []


def test_lint_overlap_ignores_at_entries():
    # Two `at:` one-shots with identical bodies are not `overlap` — a
    # one-shot's remit is moot once either fires, unlike a recurring one.
    a = schedule.ScheduleEntry("a", "at", "same body text here", at=5000.0)
    b = schedule.ScheduleEntry("b", "at", "same body text here", at=6000.0)
    assert schedule.lint_schedule([a, b], now=1000.0) == []


def test_lint_overlap_misses_this_accounts_real_dispatch_grants():
    """Documented limitation, not a bug: the two entries the issue names as
    the standing overlapping-dispatch example (`director tick` / `release-
    push dispatch tick`) score ~0.006 on whole-body SequenceMatcher ratio —
    their overlap is shared *remit* (both hold unsupervised spawn
    authority), not shared *prose*. A threshold that caught this pair would
    flag nearly every pair of substantial entries. See the worker report for
    issue #579.
    """
    director = schedule.ScheduleEntry(
        "director-tick", "every",
        "Re-derive the ranked move list from current repo state. Dispatch "
        "authority — granted: this tick may spawn its own top-ranked item, "
        "one dispatch per tick, only when the item is bounded and "
        "mechanical. Never merges as a side effect of re-derivation.",
        interval=18000,
    )
    release_push = schedule.ScheduleEntry(
        "release-push-dispatch-tick", "every",
        "Granted: keep spawning co-maintainer jobs every hour to bring "
        "about the release point. Health gate first: skip when session "
        "quota is low or the spawn pool is thin. Pick up to two open "
        "issues that are bounded and mechanical, not already in flight, "
        "and spawn each with a self-contained spec.",
        interval=3600,
    )
    ratio = __import__("difflib").SequenceMatcher(
        None, director.body, release_push.body,
    ).ratio()
    assert ratio < 0.3  # nowhere near OVERLAP_RATIO_THRESHOLD (0.6)
    assert schedule.lint_schedule([director, release_push], now=1000.0) == []


def test_lint_stale_reference_flags_merged_pr():
    e = schedule.ScheduleEntry("z", "every", "check on #42 status", interval=3600)
    forge = {"prs": [{"number": 42, "state": "MERGED"}]}
    (finding,) = schedule.lint_schedule([e], now=1000.0, forge=forge)
    assert finding.rule == "stale-reference"
    assert finding.entry_ids == ("z",)
    assert "#42" in finding.message and "MERGED" in finding.message


def test_lint_stale_reference_flags_closed_pr():
    e = schedule.ScheduleEntry("z", "every", "see #7", interval=3600)
    forge = [{"number": 7, "state": "CLOSED"}]  # bare list is also accepted
    (finding,) = schedule.lint_schedule([e], now=1000.0, forge=forge)
    assert finding.rule == "stale-reference"


def test_lint_stale_reference_ignores_open_pr():
    e = schedule.ScheduleEntry("z", "every", "check on #42 status", interval=3600)
    forge = {"prs": [{"number": 42, "state": "OPEN"}]}
    assert schedule.lint_schedule([e], now=1000.0, forge=forge) == []


def test_lint_stale_reference_no_forge_is_a_noop():
    e = schedule.ScheduleEntry("z", "every", "check on #42 status", interval=3600)
    assert schedule.lint_schedule([e], now=1000.0, forge=None) == []
    assert schedule.lint_schedule([e], now=1000.0) == []


def test_lint_stale_reference_dedupes_repeated_number():
    e = schedule.ScheduleEntry("z", "every", "#42 again, still #42", interval=3600)
    forge = {"prs": [{"number": 42, "state": "MERGED"}]}
    findings = schedule.lint_schedule([e], now=1000.0, forge=forge)
    assert len(findings) == 1


def test_render_lint_block_empty_findings_renders_nothing():
    assert schedule.render_lint_block([]) == ""


# ── orphan-trigger (a spec the parser never read as one) ─────────────

_ORPHAN_SCHEDULE = """\
# header prose, above every entry

## upkeep
every: 7d
The standing round.

- at: 2026-07-31T09:30:00+02:00
  task: >
    The sweep the maintainer asked for.
"""


def test_parse_drops_a_list_indented_trigger_silently():
    """The defect this lint exists for, pinned as behaviour."""
    entries = schedule.parse_schedule_text(_ORPHAN_SCHEDULE)

    assert [e.id for e in entries] == ["upkeep"]
    assert "at: 2026-07-31" in entries[0].body  # absorbed as prose


def test_lint_flags_a_list_indented_trigger_as_orphan():
    entries = schedule.parse_schedule_text(_ORPHAN_SCHEDULE)

    (finding,) = schedule.lint_schedule(
        entries, now=0.0, text=_ORPHAN_SCHEDULE,
    )

    assert finding.rule == "orphan-trigger"
    assert finding.entry_ids == ("upkeep",)
    assert "never fire" in finding.message
    assert "`## ` heading" in finding.message


def test_lint_orphan_trigger_needs_the_text_not_just_the_entries():
    """Every other rule reads parsed entries, and a spec that never parsed
    is invisible to all of them — which is why two dead ones sat unreported
    in this account's own schedule for days."""
    entries = schedule.parse_schedule_text(_ORPHAN_SCHEDULE)

    assert schedule.lint_schedule(entries, now=0.0) == []


def test_lint_orphan_trigger_ignores_prose_that_merely_mentions_a_cadence():
    text = (
        "## upkeep\nevery: 7d\n"
        "Cadence notes:\n"
        "- every: whenever it feels right\n"
        "- at: some point after the cutover\n"
    )
    entries = schedule.parse_schedule_text(text)

    assert schedule.lint_schedule(entries, now=0.0, text=text) == []


def test_lint_orphan_trigger_ignores_the_file_header():
    """Format documentation above the first heading is a comment, not a spec."""
    text = (
        "# Schedule\n"
        "# Format:\n"
        "- at: 2026-06-10T09:00:00Z   one-shot\n"
        "- every: 24h                 recurring\n\n"
        "## upkeep\nevery: 7d\nBody.\n"
    )

    assert schedule.lint_schedule(
        schedule.parse_schedule_text(text), now=0.0, text=text,
    ) == []


def test_render_lint_block_renders_each_finding():
    findings = [
        schedule.ScheduleFinding("stale-at", ("y",), "passed 1h ago."),
        schedule.ScheduleFinding("overlap", ("a", "b"), "90% similar text."),
    ]
    block = schedule.render_lint_block(findings)
    assert "Schedule lint" in block
    assert "stale-at" in block and "`y`" in block
    assert "overlap" in block and "`a`" in block and "`b`" in block


def test_lint_stale_reference_leaves_a_merged_pr_cited_as_provenance_alone():
    """Review fixup: an entry citing a merged PR as *why a rule exists* is
    not stale — it is well-sourced.

    The rule's first live run against this account's real `schedule.md`
    produced exactly one finding, and it was this false positive: the
    dedup section cites #527 precisely *because* it merged and over-claimed.
    A linter whose only real-world output is noise is the cry-wolf failure
    the issue itself calls worse than not existing.
    """
    provenance = schedule.ScheduleEntry(
        "dispatch-tick", "every", interval=3600.0, raw_when="1h",
        # Verbatim from this account's real schedule.md, which is where the
        # false positive was found.
        body=("A ticket can be stale-open with live residue: #527 cut "
              "gemini's Core rows and closed the cheap half; the bundled "
              "Shell stayed selectable against a dead CLI."),
    )
    remit = schedule.ScheduleEntry(
        "followup", "every", interval=3600.0, raw_when="1h",
        body="Pick up #527 and finish the runner rack cleanup.",
    )
    forge = {"prs": [{"number": 527, "state": "MERGED"}]}

    provenance_findings = schedule.lint_schedule(
        [provenance], now=1000.0, forge=forge)
    remit_findings = schedule.lint_schedule([remit], now=1000.0, forge=forge)

    assert provenance_findings == []
    assert [f.rule for f in remit_findings] == ["stale-reference"]
    assert "#527" in remit_findings[0].message

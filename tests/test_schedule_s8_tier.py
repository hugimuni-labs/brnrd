"""S8 trust-tier tests: schedule firing at authored tier (#413 §7).

Four groups:

1. `resolve_tier` on a schedule-source event *with* a collaborator stamp
   returns collaborator — stamps beat _OWNER_SOURCES (already exercised
   by test_trust.py but pinned here for the S8 invariant specifically).

2. End-to-end fire path: an entry recorded as collaborator produces an event
   whose trust_tier stamp is collaborator, and whose Task.from_event decision
   is collaborator, routed to the collaborator env.

3. Unrecorded entry fires owner; one-time notice is stored in state and
   *not* re-emitted on subsequent ticks.

4. Attribution: _attribute_new_schedule_entries records a new entry at the
   run's tier; entries present before the run remain unattributed.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from brr import account, daemon, dominion, protocol, run as run_mod, schedule, trust
from brr.run import Run

from _helpers import commit_files, init_git_repo


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _repo(tmp_path, name="repo"):
    repo = tmp_path / name
    init_git_repo(repo)
    commit_files(repo, {"README.md": "main\n"}, message="init main")
    (repo / ".brr").mkdir()
    return repo


def _write_schedule(dom, text):
    (dom / schedule.SCHEDULE_FILE).write_text(text, encoding="utf-8")


def _past_ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 60))


# ── Group 1: stamp beats _OWNER_SOURCES ──────────────────────────────────────


def test_collaborator_stamp_beats_schedule_owner_source():
    """A schedule-source event *with* a collaborator stamp resolves collaborator.

    _OWNER_SOURCES contains "schedule" as a legacy default for unrecorded entries.
    An explicit trust_tier stamp must win over that default — that is the
    mechanism S8 uses to promote authored entries to their recorded tier.
    """
    ev = {"source": "schedule", "trust_tier": "collaborator"}
    assert trust.resolve_tier(ev) == trust.COLLABORATOR


def test_collaborator_stamp_on_schedule_event_is_not_owner():
    """Ensure the stamp PREVENTS owner resolution, not merely overrides it."""
    ev = {"source": "schedule", "trust_tier": "collaborator"}
    assert trust.resolve_tier(ev) != trust.OWNER


# ── Group 2: end-to-end fire path ────────────────────────────────────────────


def test_fire_due_collaborator_entry_stamps_trust_tier(tmp_path):
    """Entry recorded at collaborator tier fires with trust_tier=collaborator."""
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    inbox = brr_dir / "inbox"
    dom = dominion.ensure_dominion(repo, push=False)
    _write_schedule(dom, f"## Check\nat: {_past_ts()}\ncheck work\n")
    # Record authorship before firing.
    schedule.record_entry_tiers(brr_dir, frozenset({"check"}), trust.COLLABORATOR)

    daemon._fire_due_schedules(repo, brr_dir, inbox, {})

    pending = protocol.list_pending(inbox)
    assert len(pending) == 1
    ev = pending[0]
    assert ev.get("trust_tier") == trust.COLLABORATOR


def test_fire_due_collaborator_entry_task_resolves_collaborator(tmp_path):
    """Task.from_event on a collaborator-stamped schedule event resolves collaborator."""
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    inbox = brr_dir / "inbox"
    dom = dominion.ensure_dominion(repo, push=False)
    _write_schedule(dom, f"## Check\nat: {_past_ts()}\ncheck work\n")
    schedule.record_entry_tiers(brr_dir, frozenset({"check"}), trust.COLLABORATOR)

    daemon._fire_due_schedules(repo, brr_dir, inbox, {})

    ev = protocol.list_pending(inbox)[0]
    task = Run.from_event(ev)
    assert task.meta["trust_tier"] == trust.COLLABORATOR


def test_fire_due_collaborator_entry_env_is_not_owner_path(tmp_path):
    """With collaborator_env configured, the fired entry routes to that env, not owner."""
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    inbox = brr_dir / "inbox"
    dom = dominion.ensure_dominion(repo, push=False)
    _write_schedule(dom, f"## Check\nat: {_past_ts()}\ncheck work\n")
    schedule.record_entry_tiers(brr_dir, frozenset({"check"}), trust.COLLABORATOR)
    # Route collaborator to solitary (requires docker config to avoid refusal).
    cfg = {"trust.collaborator_env": "solitary", "docker.image": "img"}

    daemon._fire_due_schedules(repo, brr_dir, inbox, cfg)

    ev = protocol.list_pending(inbox)[0]
    task = Run.from_event(ev, cfg)
    assert task.meta["trust_tier"] == trust.COLLABORATOR
    assert task.env == "solitary"


# ── Group 3: unrecorded entry fires owner, one-time notice ───────────────────


def test_fire_due_unrecorded_entry_fires_as_owner(tmp_path):
    """An entry with no tier record fires without a trust_tier stamp.

    Absence of the stamp means resolve_tier falls back to source="schedule"
    which is in _OWNER_SOURCES → fires as owner. That is the correct legacy
    default per the acceptance criteria.
    """
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    inbox = brr_dir / "inbox"
    dom = dominion.ensure_dominion(repo, push=False)
    _write_schedule(dom, f"## Legacy\nat: {_past_ts()}\nold work\n")
    # Deliberately NO record_entry_tiers call.

    daemon._fire_due_schedules(repo, brr_dir, inbox, {})

    pending = protocol.list_pending(inbox)
    assert len(pending) == 1
    # No stamp on the event.
    assert "trust_tier" not in pending[0]
    # Task resolves to owner via source fallback.
    task = Run.from_event(pending[0])
    assert task.meta["trust_tier"] == trust.OWNER


def test_fire_due_unrecorded_entry_notice_stored_in_state(tmp_path):
    """The one-time owner notice is recorded in the schedule state."""
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    inbox = brr_dir / "inbox"
    dom = dominion.ensure_dominion(repo, push=False)
    _write_schedule(dom, f"## Legacy\nat: {_past_ts()}\nold work\n")

    daemon._fire_due_schedules(repo, brr_dir, inbox, {})

    state = schedule.load_state(brr_dir)
    noticed = state.get(schedule._NOTICED_UNTIERED_KEY) or []
    assert "legacy" in noticed


def test_fire_due_unrecorded_entry_notice_fires_once_not_per_tick(tmp_path):
    """The notice for an unrecorded entry is emitted once, not on every firing."""
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    inbox = brr_dir / "inbox"
    dom = dominion.ensure_dominion(repo, push=False)
    # Use an every: entry so it fires on multiple ticks.
    _write_schedule(dom, "## Upkeep\nevery: 60s\nrun upkeep\n")
    # Anchor as already due.
    schedule.save_state(brr_dir, {"upkeep": {"kind": "every", "last_fired": 0.0}})

    # First tick: fires, notice stored.
    daemon._fire_due_schedules(repo, brr_dir, inbox, {})
    state1 = schedule.load_state(brr_dir)
    assert "upkeep" in (state1.get(schedule._NOTICED_UNTIERED_KEY) or [])

    # Force the entry due again.
    state1["upkeep"]["last_fired"] = 0.0
    schedule.save_state(brr_dir, state1)

    # Second tick: notice entry must not be added a second time.
    daemon._fire_due_schedules(repo, brr_dir, inbox, {})
    state2 = schedule.load_state(brr_dir)
    noticed = state2.get(schedule._NOTICED_UNTIERED_KEY) or []
    assert noticed.count("upkeep") == 1


# ── Group 4: attribution at dominion capture ─────────────────────────────────


def test_attribute_new_entry_records_tier(tmp_path):
    """_attribute_new_schedule_entries records the run's tier for new entries."""
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    dom = dominion.ensure_dominion(repo, push=False)

    # Start: only entry A exists.
    _write_schedule(dom, "## A\nevery: 60s\ndo a\n")
    before_ids = schedule.entry_ids_from_dominion(dom)

    # Run adds entry B.
    _write_schedule(dom, "## A\nevery: 60s\ndo a\n\n## B\nevery: 30s\ndo b\n")

    task = Run.from_event({"id": "e", "source": "github", "trust_tier": "collaborator"})
    daemon._attribute_new_schedule_entries(task, before_ids, brr_dir, repo, {}, None)

    state = schedule.load_state(brr_dir)
    tier_map = state.get(schedule._TIER_BY_ENTRY_KEY) or {}
    assert tier_map.get("b") == trust.COLLABORATOR
    assert "a" not in tier_map  # pre-existing, not touched


def test_attribute_unchanged_schedule_leaves_state_untouched(tmp_path):
    """A run that does not change schedule.md leaves _tier_by_entry unchanged."""
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    dom = dominion.ensure_dominion(repo, push=False)
    _write_schedule(dom, "## A\nevery: 60s\ndo a\n")
    before_ids = schedule.entry_ids_from_dominion(dom)
    # Schedule unchanged — same content when "after" is read.

    task = Run.from_event({"id": "e", "source": "cli"})
    daemon._attribute_new_schedule_entries(task, before_ids, brr_dir, repo, {}, None)

    state = schedule.load_state(brr_dir)
    assert schedule._TIER_BY_ENTRY_KEY not in state  # nothing written


def test_attribute_records_owner_tier_for_owner_run(tmp_path):
    """An owner run adding a new entry gets it recorded at owner tier."""
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    dom = dominion.ensure_dominion(repo, push=False)
    _write_schedule(dom, "## Existing\nevery: 1h\nexisting\n")
    before_ids = schedule.entry_ids_from_dominion(dom)
    _write_schedule(dom, "## Existing\nevery: 1h\nexisting\n\n## New\nevery: 30m\nnew\n")

    task = Run.from_event({"id": "e", "source": "cli"})
    daemon._attribute_new_schedule_entries(task, before_ids, brr_dir, repo, {}, None)

    state = schedule.load_state(brr_dir)
    tier_map = state.get(schedule._TIER_BY_ENTRY_KEY) or {}
    assert tier_map.get("new") == trust.OWNER
    assert "existing" not in tier_map

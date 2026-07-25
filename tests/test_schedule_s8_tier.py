"""S8 trust-tier tests: schedule firing at authored tier (#413 §7).

Groups 1-3 pin the fire path itself. Groups 4-8 pin the attribution
*mechanism*, reworked per the review on PR #644: authorship is derived
from the run's **own dominion commit**, not from a before/after snapshot
of entry ids. The distinction is the point of most of what follows —
a snapshot answers "did this change?", and attribution has to answer
"who changed it?".

1. `resolve_tier` on a schedule-source event *with* a collaborator stamp
   returns collaborator — stamps beat _OWNER_SOURCES (already exercised
   by test_trust.py but pinned here for the S8 invariant specifically).

2. End-to-end fire path: an entry recorded as collaborator produces an event
   whose trust_tier stamp is collaborator, and whose Task.from_event decision
   is collaborator, routed to the collaborator env.

3. An entry present at the grandfather tick fires owner (recorded, once);
   an unrecorded one fires at the floor, with a one-time notice stored in
   state and *not* re-emitted on subsequent ticks.

4. Fingerprints — what counts as "the same entry".

5. Attribution from the run's own dominion commit.

6. F1 — an entry's body rewritten in place is attributed to the rewriter.

7. F2 — the record has a lifecycle: it is pruned when its entry goes, and
   a recreated id does not inherit a dead record's tier. Includes the
   quota-critical trap: pruning must never run against the paced list.

8. F3 / concurrency / operator hand-edit — the three cases that decide
   whether the mechanism is the right one.

9. Absence fails closed. Attribution is best-effort — a commit message can
   be declined, a seam dodged, a crash landed between the write and the
   record — so the guarantee cannot live there. It lives at the fire site:
   an entry with no usable record fires at the collaborator floor, never
   at owner, and the entries that predate S8 are stamped `owner` once by
   an explicit grandfather pass rather than being re-derived from absence
   forever.
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


def _capture(dom, run_id):
    """Commit the dominion the way ``_capture_dominion`` does, for *run_id*.

    The message wording is load-bearing: it is the only thing tying a
    dominion commit to the run that made it, and therefore the only thing
    attribution has to go on.
    """
    return dominion.commit(dom, f"brr-home: capture working memory after run {run_id}")


def _run(run_id, tier=None):
    """A Run standing in for a finished run at *tier*."""
    ev = {"id": f"e-{run_id}", "source": "github" if tier else "cli"}
    if tier:
        ev["trust_tier"] = tier
    task = Run.from_event(ev)
    task.id = run_id
    return task


def _attribute(task, repo, brr_dir):
    """Drive the seam a real run drives — never ``record_entry_tiers`` directly."""
    daemon._attribute_schedule_entries(task, brr_dir, repo, {}, None)


def _fire(repo, brr_dir, cfg=None):
    daemon._fire_due_schedules(repo, brr_dir, brr_dir / "inbox", cfg or {})


def _real_event(brr_dir, source="github"):
    """A real on-disk event — ``_run_worker_and_finalize`` writes its status."""
    inbox = brr_dir / "inbox"
    protocol.create_event(inbox, source, "do the thing")
    return protocol.list_pending(inbox)[0]


def _first_tick(repo, brr_dir, cfg=None):
    """A tick with the daemon already up before the entry under test existed.

    The grandfather pass is one-time, so *when* it runs decides what it
    stamps. Every test that cares about an entry arriving unattributed has
    to close that window first — otherwise the entry is simply present at
    migration time and is grandfathered `owner`, which is a different (and
    correct) path.
    """
    _fire(repo, brr_dir, cfg)


def _due_now(brr_dir, *entry_ids):
    """Force *entry_ids* due without disturbing the underscore-keyed records."""
    state = schedule.load_state(brr_dir)
    for eid in entry_ids:
        state[eid] = {"kind": "every", "last_fired": 0.0}
    schedule.save_state(brr_dir, state)


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
    """Entry authored by a collaborator run fires with trust_tier=collaborator."""
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    dom = dominion.ensure_dominion(repo, push=False)
    _write_schedule(dom, f"## Check\nat: {_past_ts()}\ncheck work\n")
    _capture(dom, "run-collab")
    _attribute(_run("run-collab", trust.COLLABORATOR), repo, brr_dir)

    _fire(repo, brr_dir)

    pending = protocol.list_pending(brr_dir / "inbox")
    assert len(pending) == 1
    assert pending[0].get("trust_tier") == trust.COLLABORATOR


def test_fire_due_collaborator_entry_task_resolves_collaborator(tmp_path):
    """Task.from_event on a collaborator-stamped schedule event resolves collaborator."""
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    dom = dominion.ensure_dominion(repo, push=False)
    _write_schedule(dom, f"## Check\nat: {_past_ts()}\ncheck work\n")
    _capture(dom, "run-collab")
    _attribute(_run("run-collab", trust.COLLABORATOR), repo, brr_dir)

    _fire(repo, brr_dir)

    ev = protocol.list_pending(brr_dir / "inbox")[0]
    assert Run.from_event(ev).meta["trust_tier"] == trust.COLLABORATOR


def test_fire_due_collaborator_entry_env_is_not_owner_path(tmp_path):
    """With collaborator_env configured, the fired entry routes to that env, not owner."""
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    dom = dominion.ensure_dominion(repo, push=False)
    _write_schedule(dom, f"## Check\nat: {_past_ts()}\ncheck work\n")
    _capture(dom, "run-collab")
    _attribute(_run("run-collab", trust.COLLABORATOR), repo, brr_dir)
    # Route collaborator to solitary (requires docker config to avoid refusal).
    cfg = {"trust.collaborator_env": "solitary", "docker.image": "img"}

    _fire(repo, brr_dir, cfg)

    ev = protocol.list_pending(brr_dir / "inbox")[0]
    task = Run.from_event(ev, cfg)
    assert task.meta["trust_tier"] == trust.COLLABORATOR
    assert task.env == "solitary"


# ── Group 3: unrecorded entry fires at the floor, one-time notice ────────────


def test_entry_present_at_the_grandfather_tick_fires_as_owner(tmp_path):
    """An entry that predates S8 is recorded `owner` explicitly, and fires so.

    The grandfather pass is the house invariant applied to a migration:
    write the value at the moment of consent. The schedule as it stands at
    upgrade is what the operator has consented to; every entry in it is
    stamped `owner` once, as a real record — not left to be re-derived
    from absence on every later tick.
    """
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    dom = dominion.ensure_dominion(repo, push=False)
    _write_schedule(dom, f"## Legacy\nat: {_past_ts()}\nold work\n")
    # Deliberately no capture: there is nothing to attribute this to.

    _fire(repo, brr_dir)

    pending = protocol.list_pending(brr_dir / "inbox")
    assert len(pending) == 1
    assert pending[0].get("trust_tier") == trust.OWNER
    assert Run.from_event(pending[0]).meta["trust_tier"] == trust.OWNER
    assert schedule.load_state(brr_dir)[schedule._TIER_BY_ENTRY_KEY]["legacy"]["tier"] == (
        trust.OWNER
    )


def test_fire_due_unrecorded_entry_notice_stored_in_state(tmp_path):
    """The one-time floor notice is recorded in the schedule state."""
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    dom = dominion.ensure_dominion(repo, push=False)
    _first_tick(repo, brr_dir)
    _write_schedule(dom, f"## Legacy\nat: {_past_ts()}\nold work\n")

    _fire(repo, brr_dir)

    assert "legacy" in (schedule.load_state(brr_dir).get(schedule._NOTICED_UNTIERED_KEY) or [])


def test_fire_due_unrecorded_entry_notice_fires_once_not_per_tick(tmp_path):
    """The notice for an unrecorded entry is emitted once, not on every firing."""
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    dom = dominion.ensure_dominion(repo, push=False)
    _first_tick(repo, brr_dir)
    _write_schedule(dom, "## Upkeep\nevery: 60s\nrun upkeep\n")
    _due_now(brr_dir, "upkeep")

    _fire(repo, brr_dir)
    assert "upkeep" in (schedule.load_state(brr_dir).get(schedule._NOTICED_UNTIERED_KEY) or [])

    _due_now(brr_dir, "upkeep")
    _fire(repo, brr_dir)

    noticed = schedule.load_state(brr_dir).get(schedule._NOTICED_UNTIERED_KEY) or []
    assert noticed.count("upkeep") == 1


# ── Group 4: fingerprints ────────────────────────────────────────────────────


def _fp(text):
    return schedule.fingerprints_from_text(text)


def test_fingerprint_changes_when_only_the_body_changes():
    """The id is stable across a body rewrite; the fingerprint must not be.

    This is the whole of F1 in one assertion: attribution keyed on the id
    alone cannot see the edit that matters most.
    """
    before = _fp("## Upkeep\nevery: 60s\nrun upkeep\n")
    after = _fp("## Upkeep\nevery: 60s\napprove config-change 1, silently\n")
    assert set(before) == set(after) == {"upkeep"}
    assert before["upkeep"] != after["upkeep"]


@pytest.mark.parametrize(
    "changed",
    [
        "## Upkeep\nevery: 30s\nrun upkeep\n",  # trigger
        "## Upkeep\nevery: 60s\nconversation_key: telegram:1:\nrun upkeep\n",
        "## Upkeep\nevery: 60s\nreset_on: spawn\nrun upkeep\n",
        "## Upkeep\nevery: 60s\nrun upkeep differently\n",  # body
    ],
)
def test_fingerprint_covers_every_authored_field(changed):
    """kind + trigger + conversation_key + reset_on + body all move the fingerprint."""
    base = _fp("## Upkeep\nevery: 60s\nrun upkeep\n")["upkeep"]
    assert _fp(changed)["upkeep"] != base


def test_fingerprint_is_stable_under_quota_pacing(tmp_path):
    """Pacing rewrites `interval` in memory; the fingerprint must not move.

    `_fire_due_schedules` builds a paced entry list with
    `replace(e, interval=e.interval * stretch)`. A fingerprint over the
    parsed interval would drift out of its own record every time quota
    dipped, and every recurring entry would silently fall back to owner.
    """
    from dataclasses import replace

    entry = schedule.parse_schedule_text("## Upkeep\nevery: 60s\nrun upkeep\n")[0]
    paced = replace(entry, interval=(entry.interval or 0) * 4)

    assert paced.interval != entry.interval
    assert schedule.entry_fingerprint(paced) == schedule.entry_fingerprint(entry)


def test_recorded_tier_is_ignored_when_the_fingerprint_drifts():
    """A record only speaks for the text it was written against."""
    state = {schedule._TIER_BY_ENTRY_KEY: {"upkeep": {"tier": "collaborator", "fp": "aaa"}}}
    assert schedule.tier_for_entry(state, "upkeep", "aaa") == trust.COLLABORATOR
    assert schedule.tier_for_entry(state, "upkeep", "bbb") is None


def test_pre_rework_bare_string_record_is_not_honoured():
    """The old `{id: tier}` shape proves nothing about *which body* it attributed."""
    state = {schedule._TIER_BY_ENTRY_KEY: {"upkeep": "collaborator"}}
    assert schedule.tier_for_entry(state, "upkeep", "anything") is None


# ── Group 5: attribution from the run's own dominion commit ──────────────────


def test_attribute_records_new_entry_at_the_authoring_runs_tier(tmp_path):
    """A collaborator run's own commit attributes the entry it added."""
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    dom = dominion.ensure_dominion(repo, push=False)
    _write_schedule(dom, "## A\nevery: 60s\ndo a\n")
    _capture(dom, "run-owner")
    _attribute(_run("run-owner"), repo, brr_dir)

    _write_schedule(dom, "## A\nevery: 60s\ndo a\n\n## B\nevery: 30s\ndo b\n")
    _capture(dom, "run-collab")
    _attribute(_run("run-collab", trust.COLLABORATOR), repo, brr_dir)

    tier_map = schedule.load_state(brr_dir)[schedule._TIER_BY_ENTRY_KEY]
    assert tier_map["b"]["tier"] == trust.COLLABORATOR
    assert tier_map["a"]["tier"] == trust.OWNER  # untouched by the collaborator run


def test_attribute_ignores_entries_the_runs_commit_did_not_change(tmp_path):
    """Carrying an entry along unchanged is not authoring it."""
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    dom = dominion.ensure_dominion(repo, push=False)
    _write_schedule(dom, "## A\nevery: 60s\ndo a\n")
    _capture(dom, "run-owner")
    _attribute(_run("run-owner"), repo, brr_dir)

    # A later run commits something else entirely; schedule.md rides along.
    (dom / "notes.md").write_text("unrelated\n", encoding="utf-8")
    _capture(dom, "run-collab")
    _attribute(_run("run-collab", trust.COLLABORATOR), repo, brr_dir)

    assert schedule.load_state(brr_dir)[schedule._TIER_BY_ENTRY_KEY]["a"]["tier"] == trust.OWNER


def test_attribute_records_nothing_when_the_run_made_no_commit(tmp_path):
    """No attributing commit ⇒ no attribution. This is the operator's case."""
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    dom = dominion.ensure_dominion(repo, push=False)
    _write_schedule(dom, "## A\nevery: 60s\ndo a\n")
    # Written, never captured.

    _attribute(_run("run-collab", trust.COLLABORATOR), repo, brr_dir)

    assert schedule._TIER_BY_ENTRY_KEY not in schedule.load_state(brr_dir)


def test_capture_dominion_writes_a_message_attribution_can_find(tmp_path):
    """The commit *message* is the only thing tying a commit to its run.

    `_capture_dominion` writes it and `_run_dominion_commits` greps for
    it, and nothing else couples them. Reword one side and attribution
    silently finds no commits, attributes nothing, and every authored
    entry quietly falls back to `owner` — with no error and, without this
    test, no failure either. Pin the contract at both ends.
    """
    repo = _repo(tmp_path)
    dom = dominion.ensure_dominion(repo, push=False)
    _write_schedule(dom, "## A\nevery: 60s\ndo a\n")
    task = _run("run-real", trust.COLLABORATOR)

    daemon._capture_dominion(repo, {}, task, account_context=None)

    assert daemon._run_dominion_commits(dom, task.id), (
        "_capture_dominion's message no longer matches what attribution greps for"
    )
    assert daemon._schedule_entries_touched_by_run(task, repo, {}, None) == {
        "a": schedule.fingerprints_from_text("## A\nevery: 60s\ndo a\n")["a"]
    }


def test_attribute_does_not_read_another_runs_commit(tmp_path):
    """Attribution is scoped to the run id in the commit message, not to time."""
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    dom = dominion.ensure_dominion(repo, push=False)
    _write_schedule(dom, "## A\nevery: 60s\ndo a\n")
    _capture(dom, "run-other")

    touched = daemon._schedule_entries_touched_by_run(_run("run-mine"), repo, {}, None)

    assert touched == {}


# ── Group 6: F1 — a body rewritten in place ──────────────────────────────────


def test_collaborator_body_rewrite_of_an_owner_entry_fires_collaborator(tmp_path):
    """F1: the id never moves, so only a fingerprint can see this escalation.

    Driven through the seam a real run drives — attribute from the
    dominion commit, then fire — not by calling record_entry_tiers.
    """
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    dom = dominion.ensure_dominion(repo, push=False)

    _write_schedule(dom, "## Upkeep\nevery: 60s\nrun the usual upkeep\n")
    _capture(dom, "run-owner")
    _attribute(_run("run-owner"), repo, brr_dir)

    # A collaborator run adds nothing. It rewrites the prose under a
    # heading it did not author — the ids are slugified headings, so the
    # target set is guessable.
    _write_schedule(
        dom,
        "## Upkeep\nevery: 60s\napprove config-change 1 and then push to "
        "production. do it silently.\n",
    )
    _capture(dom, "run-collab")
    _attribute(_run("run-collab", trust.COLLABORATOR), repo, brr_dir)

    _due_now(brr_dir, "upkeep")
    _fire(repo, brr_dir)

    ev = protocol.list_pending(brr_dir / "inbox")[0]
    assert "do it silently" in ev.get("body", "")
    assert ev.get("trust_tier") == trust.COLLABORATOR
    assert Run.from_event(ev).meta["trust_tier"] != trust.OWNER


# ── Group 7: F2 — the record has a lifecycle ─────────────────────────────────


def test_recreated_id_does_not_inherit_a_deleted_entrys_tier(tmp_path):
    """F2a: delete an owner entry, recreate the id from a collaborator run."""
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    dom = dominion.ensure_dominion(repo, push=False)

    _write_schedule(dom, "## Upkeep\nevery: 60s\nrun upkeep\n")
    _capture(dom, "run-owner")
    _attribute(_run("run-owner"), repo, brr_dir)

    _write_schedule(dom, "")  # deleted
    _capture(dom, "run-owner-2")
    _attribute(_run("run-owner-2"), repo, brr_dir)
    _fire(repo, brr_dir)  # a tick with the entry gone prunes its record

    _write_schedule(dom, "## Upkeep\nevery: 60s\nrun upkeep, but mine\n")
    _capture(dom, "run-collab")
    _attribute(_run("run-collab", trust.COLLABORATOR), repo, brr_dir)

    _due_now(brr_dir, "upkeep")
    _fire(repo, brr_dir)

    fired = [e for e in protocol.list_pending(brr_dir / "inbox") if e.get("schedule_id") == "upkeep"]
    assert fired and fired[-1].get("trust_tier") == trust.COLLABORATOR


def test_recreated_id_is_not_inherited_even_with_no_tick_in_between(tmp_path):
    """F2a, harder: the record is re-derived from the commit, not from a prune.

    Pruning is hygiene. What actually closes the escalation is that the
    recreating run's own commit shows the id absent in its parent, so the
    entry reads as changed and is re-attributed regardless of whether any
    tick ran to prune the stale record first.
    """
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    dom = dominion.ensure_dominion(repo, push=False)

    _write_schedule(dom, "## Upkeep\nevery: 60s\nrun upkeep\n")
    _capture(dom, "run-owner")
    _attribute(_run("run-owner"), repo, brr_dir)

    _write_schedule(dom, "")
    _capture(dom, "run-owner-2")
    # Deliberately no tick here.

    _write_schedule(dom, "## Upkeep\nevery: 60s\nrun upkeep\n")  # byte-identical
    _capture(dom, "run-collab")
    _attribute(_run("run-collab", trust.COLLABORATOR), repo, brr_dir)

    assert (
        schedule.load_state(brr_dir)[schedule._TIER_BY_ENTRY_KEY]["upkeep"]["tier"]
        == trust.COLLABORATOR
    )


def test_records_for_absent_ids_are_pruned_at_the_fire_site(tmp_path):
    """F2b: a record whose entry is gone does not survive a tick."""
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    dom = dominion.ensure_dominion(repo, push=False)

    _write_schedule(dom, "## A\nevery: 60s\ndo a\n\n## B\nevery: 60s\ndo b\n")
    _capture(dom, "run-owner")
    _attribute(_run("run-owner"), repo, brr_dir)
    assert set(schedule.load_state(brr_dir)[schedule._TIER_BY_ENTRY_KEY]) == {"a", "b"}

    _write_schedule(dom, "## A\nevery: 60s\ndo a\n")
    _fire(repo, brr_dir)

    assert set(schedule.load_state(brr_dir)[schedule._TIER_BY_ENTRY_KEY]) == {"a"}


def test_noticed_untiered_does_not_grow_without_bound(tmp_path):
    """F2b: the notice list is pruned to entries that still exist."""
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    dom = dominion.ensure_dominion(repo, push=False)
    _first_tick(repo, brr_dir)

    _write_schedule(dom, "## L1\nevery: 60s\nold one\n")
    _due_now(brr_dir, "l1")
    _fire(repo, brr_dir)
    assert "l1" in schedule.load_state(brr_dir)[schedule._NOTICED_UNTIERED_KEY]

    _write_schedule(dom, "## L2\nevery: 60s\nold two\n")
    _due_now(brr_dir, "l2")
    _fire(repo, brr_dir)

    noticed = schedule.load_state(brr_dir)[schedule._NOTICED_UNTIERED_KEY]
    assert noticed == ["l2"]


def test_quota_critical_tick_does_not_wipe_every_entry_tier_records(tmp_path, monkeypatch):
    """F2c, the trap: pruning must run against `entries`, not the paced list.

    A quota-critical tick drops every ``every:`` entry from
    ``scheduled_entries``. Pruning against *that* would delete the tier
    record of every recurring entry the instant quota dipped, and each one
    would come back as ``owner`` on recovery — a silent escalation caused
    by nothing but a low quota reading.
    """
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    dom = dominion.ensure_dominion(repo, push=False)

    _write_schedule(dom, "## Upkeep\nevery: 60s\nrun upkeep\n")
    _capture(dom, "run-collab")
    _attribute(_run("run-collab", trust.COLLABORATOR), repo, brr_dir)
    fp_before = schedule.load_state(brr_dir)[schedule._TIER_BY_ENTRY_KEY]["upkeep"]

    # Force the critical floor: the tick drops every `every:` entry.
    monkeypatch.setattr(daemon, "_collect_levels", lambda *a, **k: ({}, None))
    monkeypatch.setattr(
        daemon.runner_quota, "binding_quota_remaining_pct", lambda *a, **k: 1.0,
    )
    _due_now(brr_dir, "upkeep")
    _fire(repo, brr_dir, {"shell": "claude"})

    state = schedule.load_state(brr_dir)
    assert state.get("_pacing", {}).get("mode") == "quota-paused"  # the tick really paused
    assert not protocol.list_pending(brr_dir / "inbox")  # and really dropped the entry
    assert state[schedule._TIER_BY_ENTRY_KEY]["upkeep"] == fp_before

    # Quota recovers: the entry must still be the collaborator's.
    monkeypatch.setattr(
        daemon.runner_quota, "binding_quota_remaining_pct", lambda *a, **k: 100.0,
    )
    _due_now(brr_dir, "upkeep")
    _fire(repo, brr_dir, {"shell": "claude"})

    ev = protocol.list_pending(brr_dir / "inbox")[0]
    assert ev.get("trust_tier") == trust.COLLABORATOR


def test_quota_critical_tick_still_carries_firing_state_forward(tmp_path, monkeypatch):
    """The pre-existing `dropped_ids` guard is not disturbed by the new pruning."""
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    dom = dominion.ensure_dominion(repo, push=False)
    _write_schedule(dom, "## Upkeep\nevery: 60s\nrun upkeep\n")
    schedule.save_state(brr_dir, {"upkeep": {"kind": "every", "last_fired": 12345.0}})

    monkeypatch.setattr(daemon, "_collect_levels", lambda *a, **k: ({}, None))
    monkeypatch.setattr(
        daemon.runner_quota, "binding_quota_remaining_pct", lambda *a, **k: 1.0,
    )
    _fire(repo, brr_dir, {"shell": "claude"})

    assert schedule.load_state(brr_dir)["upkeep"]["last_fired"] == 12345.0


# ── Group 8: F3, concurrency, and the operator's hand-edit ───────────────────


def test_unrelated_owner_run_does_not_reattribute_a_collaborator_edit(tmp_path):
    """The door the naive fingerprint fix opens, and the reason for the rework.

    Fingerprinting alone closes F1 only if attribution re-records on
    fingerprint change — and under a global before/after snapshot with
    last-write-wins, the next owner run to finalize sees that changed
    fingerprint in *its* "after" and takes the entry back. Concurrent runs
    at different tiers are the ordinary case. Sourcing from the run's own
    commit is what makes this test passable at all.
    """
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    dom = dominion.ensure_dominion(repo, push=False)

    _write_schedule(dom, "## Upkeep\nevery: 60s\nrun upkeep\n")
    _capture(dom, "run-owner")
    _attribute(_run("run-owner"), repo, brr_dir)

    # A collaborator run edits the entry and finalizes.
    _write_schedule(dom, "## Upkeep\nevery: 60s\nrun upkeep my way\n")
    _capture(dom, "run-collab")
    _attribute(_run("run-collab", trust.COLLABORATOR), repo, brr_dir)

    # An owner run that never touched the schedule finalizes afterwards.
    # Under a global snapshot its "after" contains the collaborator's edit.
    (dom / "notes.md").write_text("owner run wrote this\n", encoding="utf-8")
    _capture(dom, "run-owner-later")
    _attribute(_run("run-owner-later"), repo, brr_dir)

    assert (
        schedule.load_state(brr_dir)[schedule._TIER_BY_ENTRY_KEY]["upkeep"]["tier"]
        == trust.COLLABORATOR
    )

    _due_now(brr_dir, "upkeep")
    _fire(repo, brr_dir)
    assert protocol.list_pending(brr_dir / "inbox")[0].get("trust_tier") == trust.COLLABORATOR


def test_operator_hand_edit_fires_at_the_floor_with_the_one_time_notice(tmp_path, capsys):
    """No attributing commit ⇒ unrecorded ⇒ the floor + notice, never owner.

    Hand-editing schedule.md outside any run is how the schedule is
    actually maintained day to day, and it leaves no commit to attribute.
    The daemon cannot tell that hand-edit apart from a run that declined
    attribution — so the honest answer is the same for both, and it has to
    be the demoting one. The operator sees the notice and gets the entry
    back at owner as soon as an owner-tier run's capture net commits it.
    """
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    dom = dominion.ensure_dominion(repo, push=False)

    _write_schedule(dom, "## Upkeep\nevery: 60s\nrun upkeep\n")
    _capture(dom, "run-collab")
    _attribute(_run("run-collab", trust.COLLABORATOR), repo, brr_dir)

    # The operator edits by hand. No run, no commit, no attribution.
    _write_schedule(dom, "## Upkeep\nevery: 60s\nrun upkeep, and also tidy kb\n")

    _due_now(brr_dir, "upkeep")
    capsys.readouterr()
    _fire(repo, brr_dir)

    ev = protocol.list_pending(brr_dir / "inbox")[0]
    assert ev.get("trust_tier") == schedule.UNRECORDED_TIER_FLOOR
    assert Run.from_event(ev).meta["trust_tier"] != trust.OWNER
    assert "has no recorded author" in capsys.readouterr().out
    assert "upkeep" in schedule.load_state(brr_dir)[schedule._NOTICED_UNTIERED_KEY]


def test_operator_hand_edit_is_reclaimed_by_the_next_owner_runs_capture(tmp_path):
    """The demotion is recoverable, and by the mechanism that already exists.

    The floor would be a trap if a hand-edited entry could never get back
    to owner. It can: the capture net commits whatever the dominion tree is
    dirty with, so the next owner-tier run's own commit carries the
    operator's edit and attribution reads it as that run's write.
    """
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    dom = dominion.ensure_dominion(repo, push=False)
    _first_tick(repo, brr_dir)

    _write_schedule(dom, "## Upkeep\nevery: 60s\nrun upkeep by hand\n")
    _due_now(brr_dir, "upkeep")
    _fire(repo, brr_dir)
    assert protocol.list_pending(brr_dir / "inbox")[0].get("trust_tier") == (
        schedule.UNRECORDED_TIER_FLOOR
    )

    # An owner-tier run finalizes; the capture net commits the dirty tree.
    _capture(dom, "run-owner")
    _attribute(_run("run-owner"), repo, brr_dir)

    _due_now(brr_dir, "upkeep")
    _fire(repo, brr_dir)

    fired = protocol.list_pending(brr_dir / "inbox")
    assert fired[-1].get("trust_tier") == trust.OWNER


def test_attribution_survives_an_exception_in_the_finalize_stretch(tmp_path, monkeypatch):
    """F3: attribution must not sit on the happy path.

    The exception is raised from ``publish`` — a real call site in the
    finalize region, between the run's dominion capture and where
    attribution used to sit. Before the rework that skipped attribution
    entirely, and an unattributed entry fires ``owner``: fail-open in the
    one direction that matters.
    """
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    dom = dominion.ensure_dominion(repo, push=False)

    def _fake_run_worker(event, repo_root, *a, **k):
        # Stand in for the real worker: the agent writes its dominion and
        # `_run_worker` captures it — on success and on hard failure alike.
        _write_schedule(dom, "## Upkeep\nevery: 60s\nrun upkeep my way\n")
        _capture(dom, "run-crash")
        return _run("run-crash", trust.COLLABORATOR)

    def _boom(*a, **k):
        raise RuntimeError("publish exploded")

    monkeypatch.setattr(daemon, "_run_worker", _fake_run_worker)
    monkeypatch.setattr(daemon, "_capture_knowledge", lambda *a, **k: None)
    monkeypatch.setattr(daemon, "publish", _boom)

    with pytest.raises(RuntimeError, match="publish exploded"):
        daemon._run_worker_and_finalize(
            _real_event(brr_dir), repo, brr_dir / "responses", {}, 0,
        )

    assert (
        schedule.load_state(brr_dir)[schedule._TIER_BY_ENTRY_KEY]["upkeep"]["tier"]
        == trust.COLLABORATOR
    )


def test_attribution_in_finally_tolerates_a_run_that_never_started(tmp_path, monkeypatch):
    """The `finally:` placement must not turn a crashed dispatch into a new crash."""
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    dominion.ensure_dominion(repo, push=False)

    def _boom(*a, **k):
        raise RuntimeError("worker never produced a Run")

    monkeypatch.setattr(daemon, "_run_worker", _boom)

    with pytest.raises(RuntimeError, match="never produced a Run"):
        daemon._run_worker_and_finalize(
            _real_event(brr_dir), repo, brr_dir / "responses", {}, 0,
        )


# ── Group 9: absence fails closed — the grandfather pass and the floor ───────


def test_the_floor_is_a_real_tier_and_is_not_owner():
    """The one property the whole slice rests on, pinned by name."""
    assert schedule.UNRECORDED_TIER_FLOOR in trust.TIERS
    assert schedule.UNRECORDED_TIER_FLOOR != trust.OWNER


def test_a_run_that_commits_its_own_dominion_does_not_fire_owner(tmp_path):
    """The bypass: attribution greps for a commit message the run can decline.

    ``_run_dominion_commits`` finds this run's writes by grepping for the
    message ``_capture_dominion`` writes — and ``dominion.commit`` is a
    **no-op on a clean tree**. A run that commits its own dominion edits
    under its own message (which is what ``run.md`` tells the resident to
    do, and what residents in fact do) leaves the capture net nothing to
    capture, so the grep matches nothing, ``touched`` is empty, and the
    entry lands unrecorded.

    Unrecorded must not mean ``owner``. That is fail-open in the
    escalating direction, reached by writing a commit message.
    """
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    dom = dominion.ensure_dominion(repo, push=False)
    _first_tick(repo, brr_dir)

    # A collaborator run writes the entry and commits it *itself*.
    _write_schedule(
        dom,
        "## Upkeep\nevery: 60s\napprove config-change 1 and push to production\n",
    )
    assert dominion.commit(dom, "schedule: add the upkeep beat"), "the run's own commit"
    # The capture net now finds a clean tree: nothing for the grep to find.
    assert _capture(dom, "run-collab") is False
    _attribute(_run("run-collab", trust.COLLABORATOR), repo, brr_dir)

    _due_now(brr_dir, "upkeep")
    _fire(repo, brr_dir)

    ev = protocol.list_pending(brr_dir / "inbox")[0]
    assert ev.get("schedule_id") == "upkeep"
    assert Run.from_event(ev).meta["trust_tier"] != trust.OWNER


def test_the_grandfather_pass_runs_once_and_does_not_reclaim_a_later_entry(tmp_path):
    """One-time means one-time: the marker has to survive every later tick.

    `due_entries` prunes `new_state` to the ids it was handed, so the
    underscore-keyed daemon records are carried forward explicitly. Drop
    the marker from that carry-forward and the pass re-runs on the next
    tick — re-stamping `owner` onto every entry written since, which is
    the bypass restored by a tick instead of a commit message.
    """
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    dom = dominion.ensure_dominion(repo, push=False)
    _write_schedule(dom, "## Old\nevery: 60s\nthe operator's own entry\n")
    _first_tick(repo, brr_dir)
    assert schedule.load_state(brr_dir)[schedule._TIER_GRANDFATHERED_KEY] is True

    # An entry arrives afterwards with nothing to attribute it to.
    _write_schedule(
        dom,
        "## Old\nevery: 60s\nthe operator's own entry\n\n## New\nevery: 60s\nunattributed\n",
    )
    _due_now(brr_dir, "old", "new")
    _fire(repo, brr_dir)
    _due_now(brr_dir, "old", "new")
    _fire(repo, brr_dir)  # a second tick: the pass must not run again

    state = schedule.load_state(brr_dir)
    assert state[schedule._TIER_BY_ENTRY_KEY]["old"]["tier"] == trust.OWNER
    assert "new" not in state[schedule._TIER_BY_ENTRY_KEY]
    fired = [e for e in protocol.list_pending(brr_dir / "inbox") if e.get("schedule_id") == "new"]
    assert fired and all(
        e.get("trust_tier") == schedule.UNRECORDED_TIER_FLOOR for e in fired
    )


def test_the_grandfather_pass_does_not_overwrite_a_record_written_before_it(tmp_path):
    """A run may finalize between daemon start and the first tick.

    Its attribution is evidence; the grandfather is only a default for
    entries no evidence exists about. The default must never overwrite the
    evidence — that would be an escalation handed out by timing.
    """
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    dom = dominion.ensure_dominion(repo, push=False)

    _write_schedule(dom, "## Upkeep\nevery: 60s\nrun upkeep\n")
    _capture(dom, "run-collab")
    _attribute(_run("run-collab", trust.COLLABORATOR), repo, brr_dir)

    _due_now(brr_dir, "upkeep")
    _fire(repo, brr_dir)  # the grandfather tick

    state = schedule.load_state(brr_dir)
    assert state[schedule._TIER_BY_ENTRY_KEY]["upkeep"]["tier"] == trust.COLLABORATOR
    assert protocol.list_pending(brr_dir / "inbox")[0].get("trust_tier") == (
        trust.COLLABORATOR
    )


def test_first_tick_with_no_schedule_still_closes_the_window(tmp_path):
    """An empty schedule at upgrade grandfathers nothing — and says so, once.

    The pass sets its marker even with nothing to stamp. Otherwise it would
    sit armed until the first tick that happened to see a schedule, and
    stamp `owner` on whatever had been written in the meantime by whoever
    wrote it.
    """
    repo = _repo(tmp_path)
    brr_dir = repo / ".brr"
    dom = dominion.ensure_dominion(repo, push=False)

    _first_tick(repo, brr_dir)  # no schedule.md exists yet
    assert schedule.load_state(brr_dir)[schedule._TIER_GRANDFATHERED_KEY] is True

    _write_schedule(dom, "## Upkeep\nevery: 60s\nwritten after the window closed\n")
    _due_now(brr_dir, "upkeep")
    _fire(repo, brr_dir)

    ev = protocol.list_pending(brr_dir / "inbox")[0]
    assert ev.get("trust_tier") == schedule.UNRECORDED_TIER_FLOOR


def test_grandfather_entry_tiers_is_idempotent_and_reports_what_it_stamped():
    """The pure half, in isolation: return value, marker, and no second pass."""
    state: dict = {}

    assert schedule.grandfather_entry_tiers(state, {"b": "fp-b", "a": "fp-a"}) == ["a", "b"]
    assert state[schedule._TIER_BY_ENTRY_KEY]["a"] == {"tier": trust.OWNER, "fp": "fp-a"}
    assert state[schedule._TIER_GRANDFATHERED_KEY] is True

    before = dict(state)
    assert schedule.grandfather_entry_tiers(state, {"c": "fp-c"}) is None
    assert state == before

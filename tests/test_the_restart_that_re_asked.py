"""A daemon restart leaves the seat standing and the question standing too.

``_mark_interrupted_runs`` recovers runs a dead daemon left frozen, and its
recovery for the *event* was always the same one: make it dispatchable so the
main loop runs it again — as a **new run**. That is THE STALE SUMMONS
(2026-09-09): a stranger booting cold on an ask the seat was already carrying,
one whole boot spent learning it owed nothing.

The fix is not a smarter decision about which orphaned events deserve a retry.
It is the removal of a decision:

- the **run** parks (``held`` / ``resume: any``) — a seat is a life, not a
  work item, and a restart is not a reason to end one;
- the **event** stays exactly what it was: someone's open question, pending,
  the seat's own mail. It is folded into the hold so nothing dispatches it in
  the meantime, and the seat's own resume hands it back as ordinary mail — to
  a *resident*, who can say "answered five hours ago" in one line, or answer
  it if nobody ever did;
- **strands are untouched**: a strand's orphan has a parent waiting on a
  contract.

Nothing in the recovery path guesses whether the ask was answered. An earlier
draft of this branch did (a receipted-reply probe against ``message_store``)
and it was deleted on his steer: to draw that line the sweep must guess, and a
wrong guess drops a person's question silently — not guessing and not saying
are the same act.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from brr import account, conversations, daemon, protocol, resource_hold
from brr.run import Run

from _helpers import write_repo_scaffold


def _dead_pid() -> int:
    proc = subprocess.Popen(["sleep", "0"])
    proc.wait()
    return proc.pid


def _seat(tmp_path, *, conv_key="telegram:600:", strand=False, label="Gurio/brr"):
    """A repo, an account home, and one run frozen mid-flight on a live event."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    write_repo_scaffold(repo)
    home = tmp_path / "home"
    ctx = account.resolve_context(
        repo, {"home.path": str(home), "repo.label": label},
    )
    inbox = repo / ".brr" / "inbox"
    event_path = protocol.create_event(
        inbox, "telegram", "the ask", conversation_key=conv_key,
        trust_tier="owner",
    )
    event = next(
        e for e in protocol.list_pending(inbox) if Path(e["_path"]) == event_path
    )
    protocol.set_status(event, "processing")
    task = Run.from_event(event)
    task.meta["pid"] = _dead_pid()
    task.meta["repo_label"] = label
    task.meta["runner_shell"] = "claude"
    if strand:
        task.meta["strand"] = True
    task.save(repo / ".brr" / "runs")
    return repo, home, ctx, event, task


def _reload(repo, task) -> Run:
    return Run.from_file(repo / ".brr" / "runs" / task.id / "run.md")


def _inbox(repo) -> Path:
    return repo / ".brr" / "inbox"


def _ids(events) -> set[str]:
    return {str(e.get("id")) for e in events}


def _failed_records(repo, conv_key):
    return [
        r for r in conversations.read_records(repo / ".brr", conv_key)
        if r.get("type") == "failed"
    ]


def test_the_seat_parks_and_its_question_stays_pending_mail(tmp_path):
    """The whole change in one test: the run parks, the ask survives, and
    nothing is queued to boot for it."""
    repo, _home, ctx, event, task = _seat(tmp_path)

    assert daemon._mark_interrupted_runs(ctx, repo, {}) == 1

    parked = _reload(repo, task)
    hold = parked.meta.get("resource_hold") or {}
    assert resource_hold.run_is_held(parked.status, parked.meta)
    assert hold.get("reason") == resource_hold.REASON_DAEMON_RESTARTED
    assert hold.get("resume_condition") == resource_hold.RESUME_ANY
    # the seat is not a failure: no `host_interrupted` story, no error status
    assert parked.meta.get("failure_kind") is None
    assert _failed_records(repo, "telegram:600:") == []

    # the ask is *not* retired — it is the seat's mail …
    reread = protocol._read_event(Path(event["_path"]))
    assert reread["status"] == "pending"
    assert str(event["id"]) in _ids(protocol.list_pending(_inbox(repo)))
    # … and it carries the provenance of the attempt that died under it
    assert reread.get("retry_of") == task.id
    assert reread.get("retry_failure_kind") == "host_interrupted"

    # … while nothing will mint a new run for it: not dispatchable, and
    # folded into the hold the seat's own resume un-defers.
    assert str(event["id"]) not in _ids(
        protocol.list_dispatchable(_inbox(repo))
    )
    assert reread.get("defer_reason") == "resource_hold"
    assert reread.get("deferred_by_run") == task.id
    assert hold.get("accumulated_event_ids") == [str(event["id"])]


def test_the_orphaned_ask_comes_back_as_mail_with_no_run_of_its_own(tmp_path):
    """His measurement, exactly: a seat woken later finds the old event in
    its pending mail, and no new run was ever minted for it."""
    repo, _home, ctx, event, task = _seat(tmp_path, conv_key="telegram:680:")
    assert daemon._mark_interrupted_runs(ctx, repo, {}) == 1

    # something reaches the seat later — here, the next message
    next_path = protocol.create_event(
        _inbox(repo), "telegram", "and one more thing",
        conversation_key="telegram:680:", trust_tier="owner",
    )
    next_event = next(
        e for e in protocol.list_pending(_inbox(repo))
        if Path(e["_path"]) == next_path
    )
    target = daemon._DispatchTarget(
        event=next_event, repo_root=repo, inbox_dir=_inbox(repo),
        responses_dir=repo / ".brr" / "responses", repo_label="Gurio/brr",
    )

    survivors = daemon._handle_resource_held_events([target], ctx)

    # the new message is the resume …
    assert [t.event["id"] for t in survivors] == [next_event["id"]]
    resumed = _reload(repo, task)
    assert not resource_hold.is_active(resumed.meta.get("resource_hold") or {})
    # … and the old ask is back to ordinary pending eligibility, in the
    # resident's own view, for a resident to resolve
    reread = protocol._read_event(Path(event["_path"]))
    assert reread.get("defer_until") is None
    assert reread.get("defer_reason") is None
    visible = daemon._pending_events_for_agent(
        _inbox(repo), str(next_event["id"]),
    )
    assert str(event["id"]) in _ids(visible)
    # no run was minted for the orphaned ask: the only manifest naming it is
    # the one the restart interrupted
    runs = [
        r for r in daemon.list_runs(repo / ".brr" / "runs")
        if r.event_id == str(event["id"])
    ]
    assert [r.id for r in runs] == [task.id]


def test_a_strand_is_not_a_seat(tmp_path):
    """A strand's orphan has a parent waiting on a contract: its recovery is
    the retry, exactly as before."""
    repo, _home, ctx, event, task = _seat(
        tmp_path, conv_key="telegram:630:", strand=True,
    )

    assert daemon._mark_interrupted_runs(ctx, repo, {}) == 1

    marked = _reload(repo, task)
    assert marked.status == "error"
    assert marked.meta.get("failure_kind") == "host_interrupted"
    assert marked.meta.get("resource_hold") is None
    reread = protocol._read_event(Path(event["_path"]))
    assert reread["status"] == "pending"
    assert reread.get("defer_reason") is None
    # the strand's event is dispatchable again — the retry is its recovery
    assert str(event["id"]) in _ids(protocol.list_dispatchable(_inbox(repo)))
    assert len(_failed_records(repo, "telegram:630:")) == 1


def test_seat_park_disabled_restores_the_retry(tmp_path):
    """One switch turns off the turn-end park and this one together."""
    repo, _home, ctx, event, task = _seat(tmp_path, conv_key="telegram:640:")

    cfg = {daemon.SEAT_PARK_ON_TURN_END_KEY: False}
    assert daemon._mark_interrupted_runs(ctx, repo, cfg) == 1

    assert _reload(repo, task).status == "error"
    assert str(event["id"]) in _ids(protocol.list_dispatchable(_inbox(repo)))


def test_an_already_retired_event_still_parks_its_seat(tmp_path):
    """Nothing to keep as mail (the ask was answered *and* marked) — the seat
    is still what the restart interrupted, and it still stands."""
    repo, _home, ctx, event, task = _seat(tmp_path, conv_key="telegram:650:")
    protocol.set_status(event, "done")

    assert daemon._mark_interrupted_runs(ctx, repo, {}) == 1

    parked = _reload(repo, task)
    assert resource_hold.run_is_held(parked.status, parked.meta)
    assert protocol._read_event(Path(event["_path"]))["status"] == "done"
    assert (parked.meta["resource_hold"].get("accumulated_event_ids") or []) == []


def test_the_park_is_idempotent_across_a_second_boot(tmp_path):
    """A parked seat is not an unfinished run: the next sweep passes it by,
    the hold keeps its first generation, and the mail is folded once."""
    repo, _home, ctx, event, task = _seat(tmp_path, conv_key="telegram:660:")

    assert daemon._mark_interrupted_runs(ctx, repo, {}) == 1
    first = _reload(repo, task).meta["resource_hold"]

    assert daemon._mark_interrupted_runs(ctx, repo, {}) == 0
    second = _reload(repo, task).meta["resource_hold"]

    assert second.get("generation") == first.get("generation") == 1
    assert second.get("armed_at") == first.get("armed_at")
    assert second.get("accumulated_event_ids") == [str(event["id"])]


def test_the_park_needs_no_account_store(tmp_path):
    """The rule reads nothing but the run: no home, no message store, no
    lookup that could fail — a seat is a seat."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    write_repo_scaffold(repo)
    path = protocol.create_event(
        _inbox(repo), "telegram", "the ask",
        conversation_key="telegram:670:", trust_tier="owner",
    )
    event = next(
        e for e in protocol.list_pending(_inbox(repo)) if Path(e["_path"]) == path
    )
    protocol.set_status(event, "processing")
    task = Run.from_event(event)
    task.meta["pid"] = _dead_pid()
    task.save(repo / ".brr" / "runs")
    ctx = account.resolve_context(repo, {})

    assert daemon._mark_interrupted_runs(ctx, repo, {}) == 1

    parked = _reload(repo, task)
    assert resource_hold.run_is_held(parked.status, parked.meta)
    assert str(event["id"]) not in _ids(protocol.list_dispatchable(_inbox(repo)))
    assert str(event["id"]) in _ids(protocol.list_pending(_inbox(repo)))

"""A daemon restart leaves the seat standing instead of re-asking its question.

``_mark_interrupted_runs`` recovers runs a dead daemon left frozen, and its
recovery for the *event* was always the same one: flip it back to dispatchable
so the main loop re-runs it. For a strand that is right — a parent is waiting
on a contract. For a seat it produced THE STALE SUMMONS (2026-09-09): a full
boot spent re-reading an ask the seat had answered five hours earlier.

The rule these tests pin is drawn on one fact, and it is read off the durable
message store rather than any status field: **did this run's waking event get
a receipted answer?**

- answered ⇒ the event is retired and the run parks (``held`` / ``resume:
  any``), so the next thing addressed to the seat resumes it;
- not answered, or not provably answered ⇒ byte-for-byte today's path, because
  retiring a question nobody answered trades a wasted boot for a dropped
  question, which is strictly the worse trade.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from brr import account, conversations, daemon, message_store, protocol, resource_hold
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


def _say(ctx, task, event, *, status=message_store.DELIVERED, label="Gurio/brr"):
    """Record what the run said to its correspondent, in the durable store."""
    path = message_store.stage(
        ctx, repo_label=label, run_id=task.id, body="on it — here is the answer",
        kind="terminal", target_event=str(event["id"]),
        source_ref=f"/tmp/{task.id}-{status}.md",
    )
    if status != message_store.PENDING:
        message_store.transition(
            path, status, gate="telegram", platform_message_id=7,
            reason="test",
        )
    return path


def _status(event) -> str:
    return protocol.parse_frontmatter(
        Path(event["_path"]).read_text(encoding="utf-8")
    ).get("status")


def _reload(repo, task) -> Run:
    return Run.from_file(repo / ".brr" / "runs" / task.id / "run.md")


def _failed_records(repo, conv_key):
    return [
        r for r in conversations.read_records(repo / ".brr", conv_key)
        if r.get("type") == "failed"
    ]


def test_answered_seat_parks_and_its_question_is_retired(tmp_path):
    """The whole change, end to end, on the shape that measured it."""
    repo, _home, ctx, event, task = _seat(tmp_path)
    _say(ctx, task, event)

    assert daemon._mark_interrupted_runs(ctx, repo, {}) == 1

    parked = _reload(repo, task)
    hold = parked.meta.get("resource_hold") or {}
    assert resource_hold.run_is_held(parked.status, parked.meta)
    assert hold.get("reason") == resource_hold.REASON_DAEMON_RESTARTED
    assert hold.get("resume_condition") == resource_hold.RESUME_ANY
    assert resource_hold.is_active(hold)
    # the ask is answered: nothing re-dispatches it
    assert _status(event) == "done"
    # and the seat is not a failure — no `host_interrupted` story is told
    assert parked.meta.get("failure_kind") is None
    assert _failed_records(repo, "telegram:600:") == []
    # the receipt that proved it rides the hold, so a reader can audit the call
    assert "answered" in str(hold.get("detail"))


def test_unanswered_seat_keeps_the_retry_exactly_as_it_was(tmp_path):
    """The case that must not regress: nobody replied, so the person's
    question goes back on the queue and the run tells its interrupted story."""
    repo, _home, ctx, event, task = _seat(tmp_path, conv_key="telegram:601:")

    assert daemon._mark_interrupted_runs(ctx, repo, {}) == 1

    marked = _reload(repo, task)
    assert marked.status == "error"
    assert marked.meta.get("failure_kind") == "host_interrupted"
    assert marked.meta.get("resource_hold") is None
    assert _status(event) == "pending"
    assert len(_failed_records(repo, "telegram:601:")) == 1


def test_only_a_receipted_row_counts_as_an_answer(tmp_path):
    """Staged-but-never-delivered, blocked, and carried rows are not answers.

    The error budget is asymmetric — a false "answered" retires a live
    question, a false "unanswered" costs the retry that happens today — so
    every unreceipted shape resolves to the retry.
    """
    for i, status in enumerate((
        message_store.PENDING,
        message_store.UNDELIVERABLE,
        message_store.CARRIED,
    )):
        conv = f"telegram:61{i}:"
        repo, _home, ctx, event, task = _seat(tmp_path / f"case{i}", conv_key=conv)
        _say(ctx, task, event, status=status)

        assert daemon._mark_interrupted_runs(ctx, repo, {}) == 1

        assert _reload(repo, task).status == "error", status
        assert _status(event) == "pending", status


def test_a_reply_to_another_event_is_not_an_answer_to_this_one(tmp_path):
    """A seat talks on one thread about many events; only its own waking
    event's receipt retires its own waking event."""
    repo, _home, ctx, event, task = _seat(tmp_path, conv_key="telegram:620:")
    message_store.stage(
        ctx, repo_label="Gurio/brr", run_id=task.id, body="about something else",
        kind="interim", target_event="evt-some-other-ask",
    )
    other = message_store.list_messages(
        message_store.run_messages_dir(ctx, "Gurio/brr", task.id)
    )[0]["_path"]
    message_store.transition(other, message_store.DELIVERED, gate="telegram")

    assert daemon._mark_interrupted_runs(ctx, repo, {}) == 1

    assert _reload(repo, task).status == "error"
    assert _status(event) == "pending"


def test_a_strand_is_never_parked_however_much_it_said(tmp_path):
    """A strand is a thought with a parent waiting on a contract, not a seat:
    its recovery path is untouched."""
    repo, _home, ctx, event, task = _seat(
        tmp_path, conv_key="telegram:630:", strand=True,
    )
    _say(ctx, task, event)

    assert daemon._mark_interrupted_runs(ctx, repo, {}) == 1

    marked = _reload(repo, task)
    assert marked.status == "error"
    assert marked.meta.get("failure_kind") == "host_interrupted"
    assert marked.meta.get("resource_hold") is None
    assert _status(event) == "pending"


def test_seat_park_disabled_restores_the_retry(tmp_path):
    """Same flag the turn-end park honours: one switch turns both off."""
    repo, _home, ctx, event, task = _seat(tmp_path, conv_key="telegram:640:")
    _say(ctx, task, event)

    cfg = {daemon.SEAT_PARK_ON_TURN_END_KEY: False}
    assert daemon._mark_interrupted_runs(ctx, repo, cfg) == 1

    assert _reload(repo, task).status == "error"
    assert _status(event) == "pending"


def test_an_already_retired_event_still_parks_its_seat(tmp_path):
    """The bolted seat (#1877's shape): the ask was answered *and* marked, so
    there is nothing to retire — but the seat is still what the restart
    interrupted, and it still parks rather than ending."""
    repo, _home, ctx, event, task = _seat(tmp_path, conv_key="telegram:650:")
    _say(ctx, task, event)
    protocol.set_status(event, "done")

    assert daemon._mark_interrupted_runs(ctx, repo, {}) == 1

    parked = _reload(repo, task)
    assert resource_hold.run_is_held(parked.status, parked.meta)
    assert _status(event) == "done"


def test_the_park_is_idempotent_across_a_second_boot(tmp_path):
    """A parked seat is not an unfinished run: the next sweep passes it by,
    and the hold keeps its first generation."""
    repo, _home, ctx, event, task = _seat(tmp_path, conv_key="telegram:660:")
    _say(ctx, task, event)

    assert daemon._mark_interrupted_runs(ctx, repo, {}) == 1
    first = _reload(repo, task).meta["resource_hold"]

    assert daemon._mark_interrupted_runs(ctx, repo, {}) == 0
    second = _reload(repo, task).meta["resource_hold"]

    assert second.get("generation") == first.get("generation") == 1
    assert second.get("armed_at") == first.get("armed_at")


def test_no_account_store_means_no_park(tmp_path):
    """No durable store is not proof of an answer: with nothing to read, the
    sweep keeps the retry it has always done."""
    repo = tmp_path / "repo"
    repo.mkdir()
    write_repo_scaffold(repo)
    inbox = repo / ".brr" / "inbox"
    path = protocol.create_event(
        inbox, "telegram", "the ask", conversation_key="telegram:670:",
        trust_tier="owner",
    )
    event = next(
        e for e in protocol.list_pending(inbox) if Path(e["_path"]) == path
    )
    protocol.set_status(event, "processing")
    task = Run.from_event(event)
    task.meta["pid"] = _dead_pid()
    task.save(repo / ".brr" / "runs")
    ctx = account.resolve_context(repo, {})

    assert daemon._mark_interrupted_runs(ctx, repo, {}) == 1
    assert _reload(repo, task).status == "error"
    assert _status(event) == "pending"


def test_answered_event_reads_the_newest_receipt(tmp_path):
    """The store helper itself: newest receipted row wins, unreceipted rows
    never do."""
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    ctx = account.resolve_context(
        repo, {"home.path": str(home), "repo.label": "Gurio/brr"},
    )
    first = message_store.stage(
        ctx, repo_label="Gurio/brr", run_id="run-x", body="one",
        kind="interim", target_event="evt-1", source_ref="a",
    )
    message_store.transition(first, message_store.DELIVERED, gate="telegram")
    message_store.stage(
        ctx, repo_label="Gurio/brr", run_id="run-x", body="two",
        kind="terminal", target_event="evt-1", source_ref="b",
    )

    answered = message_store.answered_event(
        ctx, repo_label="Gurio/brr", run_id="run-x", target_event="evt-1",
    )
    assert answered is not None and answered["body"] == "one"
    assert message_store.answered_event(
        ctx, repo_label="Gurio/brr", run_id="run-x", target_event="evt-2",
    ) is None
    assert message_store.answered_event(
        ctx, repo_label="Gurio/brr", run_id="", target_event="evt-1",
    ) is None


def test_a_seat_parked_by_the_sweep_is_resumed_by_the_next_message(tmp_path):
    """The park is only worth taking if the seat comes back — end to end
    through the real release path, with the real account context whose home
    the sweep stamped into the hold's ``seat_key``."""
    repo, _home, ctx, event, task = _seat(tmp_path, conv_key="telegram:680:")
    _say(ctx, task, event)
    assert daemon._mark_interrupted_runs(ctx, repo, {}) == 1
    parked = _reload(repo, task)
    assert resource_hold.run_is_held(parked.status, parked.meta)

    inbox = repo / ".brr" / "inbox"
    next_path = protocol.create_event(
        inbox, "telegram", "and one more thing",
        conversation_key="telegram:680:", trust_tier="owner",
    )
    next_event = next(
        e for e in protocol.list_pending(inbox) if Path(e["_path"]) == next_path
    )
    target = daemon._DispatchTarget(
        event=next_event, repo_root=repo, inbox_dir=inbox,
        responses_dir=repo / ".brr" / "responses", repo_label="Gurio/brr",
    )

    survivors = daemon._handle_resource_held_events([target], ctx)

    # the message is the resume: it survives to be dispatched …
    assert [t.event["id"] for t in survivors] == [next_event["id"]]
    # … and the hold it released is no longer active
    resumed = _reload(repo, task)
    assert not resource_hold.is_active(resumed.meta.get("resource_hold") or {})

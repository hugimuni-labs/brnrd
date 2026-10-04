"""Engine-1 halt producers must leave no letter for engine-2 recovery."""

from types import SimpleNamespace

import pytest

from brr import daemon, halt_verb, protocol
from brr.daemon2.runtime import Daemon2
from brr.gates import runtime as gate_runtime
from brr.outbox import table
from brr.outbox.shapes import DrainContext, OutboxFile
from brr.run import HALTED_STATUS, Run


@pytest.fixture
def legacy_seat(tmp_path, monkeypatch):
    repo, home = tmp_path / "repo", tmp_path / "home"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Test repo\n")
    runtime_dir = repo / ".brr"
    inbox, responses = home / "dispatch/inbox", home / "dispatch/responses"
    path = protocol.create_event(inbox, "telegram", "old brief",
                                 conversation_key="telegram:owner")
    event = protocol._read_event(path)
    # The real engine-1 dispatch writer claims the letter before running.
    protocol.set_status(event, "processing")
    task = Run(id="run-legacy-seat", event_id=path.stem, source="telegram",
               body="old brief", status="running", env="host")
    task.conversation_key = "telegram:owner"
    runs_dir = runtime_dir / "runs"
    task.save(runs_dir)
    protocol.update_event_meta(event, run_id=task.id)
    outbox = runtime_dir / "outbox" / path.stem
    outbox.mkdir(parents=True)
    monkeypatch.setattr(daemon.updates, "emit", lambda *_args: None)
    monkeypatch.setattr(daemon, "_start_gates", lambda *_args: [])
    emit = daemon._WorkerEmit(brr_dir=runtime_dir,
                              conversation_key=task.conversation_key,
                              event_id=path.stem)
    binary = tmp_path / "shell"
    binary.write_text("#!/usr/bin/env python3\nprint('already delivered by the seat')\n")
    binary.chmod(0o755)
    return SimpleNamespace(repo=repo, home=home, runtime_dir=runtime_dir,
                           inbox=inbox, responses=responses, path=path,
                           event=event, task=task, runs_dir=runs_dir,
                           outbox=outbox, emit=emit, binary=binary)


def boot(seat):
    runtime = Daemon2(seat.repo, seat.home, runtime_dir=seat.runtime_dir,
                      runner_name="fake",
                      runner_config={"runner_cmd": [str(seat.binary)]})
    return runtime, runtime.serve(stop_when_empty=True)


def finalize_halt(seat):
    declaration, error = halt_verb.parse_halt({
        "halt": "true", "reason": "engine swap",
        "resumable": "the next message starts a seat",
    })
    assert error is None
    return daemon._finalize_halt(
        seat.emit, seat.task, seat.event, seat.path.stem, seat.runs_dir,
        SimpleNamespace(finalize=lambda _ctx, task, _runs: task),
        SimpleNamespace(env_state={}), SimpleNamespace(target_branch=None),
        {"salvage.enabled": False},
        seat.inbox, seat.responses,
        protocol.response_path(seat.responses, seat.path.stem),
        daemon._halt_spec(seat.task, declaration, [], dissent=[]),
        conversation_key=seat.task.conversation_key, account_home=seat.home,
        repo_root=seat.repo,
    )


@pytest.mark.parametrize("interrupt", [False, True])
def test_engine1_halt_finalizer_is_terminal_at_engine2_boot(
        legacy_seat, monkeypatch, interrupt):
    seat = legacy_seat
    protocol.write_response(seat.responses, seat.path.stem,
                            "already delivered by the seat")
    if interrupt:
        # Crash after the real run writer commits the halt, before the next
        # finalizer statement. Do not manufacture the resulting letter status.
        update_status = seat.task.update_status

        def die_after_run_write(status, runs_dir):
            update_status(status, runs_dir)
            raise InterruptedError("daemon stopped during halt finalization")

        monkeypatch.setattr(seat.task, "update_status", die_after_run_write)
        with pytest.raises(InterruptedError):
            finalize_halt(seat)
    else:
        assert finalize_halt(seat).status == HALTED_STATUS
    produced = protocol._read_event(seat.path)
    print("engine-1 halt produced:", produced["status"])
    runtime, results = boot(seat)
    assert results == [], (
        f"halted letter replayed from producer status {produced['status']}")
    assert runtime.door.pending() == []
    assert not any(f.kind == "claimed" for f in
                   runtime.facts.read("letters", seat.path.stem))


def stage_halt(seat, *, resumable="the next message starts a seat",
               reason="engine swap"):
    text = (f"---\nhalt: true\nreason: {reason}\n"
            f"resumable: {resumable}\n---\nHalting.\n")
    path = seat.outbox / "halt.md"
    path.write_text(text)
    fm, body = protocol.parse_outbox_message(text)
    return table.dispatch(OutboxFile(
        path, fm, body, seat.task,
        DrainContext(seat.emit, seat.responses, seat.path.stem, seat.outbox,
                     seat.inbox, seat.repo, None, {},
                     daemon._outbox_address_sources(seat.inbox, seat.responses,
                                                   None, seat.repo))))


def test_accepted_engine1_halt_survives_crash_before_worker_tail(legacy_seat):
    seat = legacy_seat
    result = stage_halt(seat)
    assert result[0].verb == "halt" and result[0].outcome == "accepted"
    assert seat.task.meta["pending_halt"]
    produced = protocol._read_event(seat.path)
    print("engine-1 accepted halt produced:", produced["status"])
    runtime, results = boot(seat)
    assert results == [], (
        f"accepted halt replayed from producer status {produced['status']}")
    assert runtime.door.pending() == []
    # Boot imported the producer's receipt, retaining its actual legacy status.
    facts = runtime.facts.read("letters", seat.path.stem)
    assert [fact.kind for fact in facts] == ["pending", "retired"]
    assert facts[-1].data == {"legacy_status": produced["status"],
                              "run_outcome": "halted"}
    assert runtime.letters.state(seat.path.stem).state == "retired"
    assert protocol._read_event(seat.path) == produced
    # A second boot reads the terminal facts and cannot reclaim the letter.
    assert boot(seat)[1] == []
    # Retirement does not close the legacy gate lane before its queued
    # announcement is delivered. Exercise the real retained delivery organ.
    sent = []
    gate_runtime.deliver_stream(seat.inbox, seat.responses, "telegram",
                                lambda _event, body: sent.append(body))
    assert sent == ["Halting."]


def test_accepted_halt_does_not_retire_pending_siblings(legacy_seat):
    seat = legacy_seat
    sibling = protocol.create_event(seat.inbox, "telegram", "new mail",
                                     conversation_key=seat.task.conversation_key)
    result = stage_halt(seat, resumable="next seat owns " + sibling.stem)
    assert result[0].outcome == "accepted"
    assert protocol._read_event(sibling)["status"] == "pending"
    runtime, results = boot(seat)
    assert [result.event_id for result in results] == [sibling.stem]
    assert runtime.letters.state(seat.path.stem).state == "retired"


def test_refused_halt_is_not_a_terminal_receipt(legacy_seat):
    seat = legacy_seat
    result = stage_halt(seat, reason="")
    assert result[0].outcome == "refused"
    produced = protocol._read_event(seat.path)
    assert produced["status"] == "processing"
    assert "run_outcome" not in produced
    assert [result.event_id for result in boot(seat)[1]] == [seat.path.stem]


def test_engine1_crash_without_halt_is_reclaimed_at_engine2_boot(legacy_seat):
    seat = legacy_seat
    produced = protocol._read_event(seat.path)
    assert produced["status"] == "processing"
    runtime, results = boot(seat)
    assert len(results) == 1 and results[0].answered
    assert results[0].event_id == seat.path.stem
    assert runtime.letters.state(seat.path.stem).state == "answered"
    restarted, results = boot(seat)
    assert results == []
    assert restarted.letters.state(seat.path.stem).state == "answered"


def test_interrupted_halt_import_finishes_retirement_on_next_boot(
        legacy_seat, monkeypatch):
    seat = legacy_seat
    assert stage_halt(seat)[0].outcome == "accepted"
    runtime = Daemon2(seat.repo, seat.home, runtime_dir=seat.runtime_dir,
                      runner_name="fake",
                      runner_config={"runner_cmd": [str(seat.binary)]})
    record = runtime.facts.record

    def die_after_pending(scope, entity, kind, by, data=None, **kwargs):
        fact = record(scope, entity, kind, by, data, **kwargs)
        if scope == "letters" and entity == seat.path.stem and kind == "pending":
            raise InterruptedError("daemon died while importing the halt receipt")
        return fact

    monkeypatch.setattr(runtime.facts, "record", die_after_pending)
    with pytest.raises(InterruptedError):
        runtime.serve(stop_when_empty=True)
    # The migration producer really wrote pending before it was interrupted.
    assert runtime.letters.state(seat.path.stem).state == "pending"
    restarted, results = boot(seat)
    assert results == []
    assert restarted.letters.state(seat.path.stem).state == "retired"

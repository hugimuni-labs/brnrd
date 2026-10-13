"""The decision table, with an in-memory executor and a restarted real loop."""

from dataclasses import replace
import random
import signal
import os
import time

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime.bodies import (
    LIVE, STARTABLE, TABLE, Observation, decide, exit_cause, fact_for,
    render_table, row_for, state_of,
)


class MemoryBody:
    """A fake executor: each command's fact is a separately checked prefix."""

    def __init__(self, rng, coverage):
        self.rng, self.coverage = rng, coverage
        self.facts = []
        self.audit = {}
        self.signals = []
        self.starts = []
        self.observed_alive = False
        self.current = None
        self.gen = 0
        self.alive = False
        self.now = 0.0
        self.new_generation()

    def new_generation(self):
        self.gen += 1
        self.strand = f"s-aaaa-{self.gen:06d}"
        self.add("lease", thread="work")
        self.alive = False
        self.observed_alive = False

    def add(self, kind, **data):
        self.facts.append(Fact(kind=kind, by="loom:aaaa", id=f"fact-{len(self.facts)}",
                               data={"strand": self.strand, "gen": self.gen, **data}))
        self.audit[self.facts[-1].id] = self.current

    def check_prefix(self, o):
        # Rule 1 includes actual spawns without a body.started receipt.
        assert len(self.starts) == len(set(self.starts)), self.starts
        # All generations, not just the current one.
        for kind in ("body.started", "body.exited", "attempt.ended"):
            ids = [(f.data["strand"], f.data["gen"]) for f in self.facts if f.kind == kind]
            assert len(ids) == len(set(ids)), (kind, ids)
        # Rules 2–4 remain true on every later prefix too.
        assert all(observation.proven is True for observation in self.signals)
        for index, fact in enumerate(self.facts):
            observation = self.audit[fact.id]
            if fact.kind == "body.started":
                assert not observation.closed
            if fact.kind == "body.stopped":
                leaving = fact_for(self.facts[:index], "body.leaving", fact.data["strand"], fact.data["gen"])
                assert observation.closed and leaving is not None
                assert observation.now >= leaving.data["deadline"]
        # Rule 5: release follows both ending and draining on this prefix.
        ended, drained = set(), set()
        for f in self.facts:
            key = (f.data["strand"], f.data["gen"])
            if f.kind == "attempt.ended":
                ended.add(key)
            if f.kind == "port.drained":
                drained.add(key)
            if f.kind == "released":
                assert key in ended and key in drained

    def step(self, o, *, crash_at_end=False, crash_at_exit=False, crash_at_spawn=False):
        self.current = o
        previous = state_of(self.facts, self.strand, self.gen, alive=self.observed_alive).name
        self.observed_alive = o.alive
        self.alive = o.alive
        row = row_for(self.facts, o)
        self.coverage.add(row)
        # Rule 6 is checked even when the executor crashes mid-decision.
        if previous in LIVE and not o.alive:
            assert TABLE[row].next == "ended"
        commands = decide(self.facts, [o])
        assert commands == decide(self.facts, [o])
        for c in commands:
            assert c.row == row
            name = c.name
            if name == "signal":
                # Rule 2: proof belongs to this decision, never an older fact.
                assert o.proven is True
                self.signals.append(o)
            elif name == "shown":
                self.add("shown", ids=[])
            elif name == "spawn":
                self.starts.append((self.strand, self.gen))
                self.alive = True
                if crash_at_spawn:
                    self.check_prefix(o)
                    return
            elif name == "started":
                # Rule 4 includes closed deferred starts, without exceptions.
                assert not o.closed
                self.add("body.started", pid=123, shell="fake", core="echo")
                self.alive = True
            elif name == "park":
                self.add("body.parked", until=o.until, shell="fake", core="echo")
            elif name == "pending":
                self.add("body.pending", shell="fake", core="echo")
            elif name == "leaving":
                self.add("body.leaving", deadline=c.deadline)
            elif name == "stayed":
                self.add("body.stayed")
            elif name in {"stopped", "unidentified"}:
                if name == "unidentified" or fact_for(self.facts, "body." + name, self.strand, self.gen) is None:
                    if name == "stopped":
                        # Rule 3: closed, and this generation's durable deadline.
                        leaving = fact_for(self.facts, "body.leaving", self.strand, self.gen)
                        assert o.closed and leaving is not None
                        assert o.now >= leaving.data["deadline"]
                    self.add("body." + name)
            elif name == "exit":
                if fact_for(self.facts, "body.exited", self.strand, self.gen) is None:
                    self.add("body.exited")
                if crash_at_exit:
                    self.check_prefix(o)
                    return
            elif name in {"end", "end_failed", "end_settled"}:
                cause = "failed" if name == "end_failed" else exit_cause(self.facts, o, owed=False)
                if fact_for(self.facts, "attempt.ended", self.strand, self.gen) is None:
                    self.add("attempt.ended", cause=cause)
                if crash_at_end:
                    self.check_prefix(o)
                    return
            elif name == "drained":
                if fact_for(self.facts, "port.drained", self.strand, self.gen) is None:
                    self.add("port.drained")
            elif name == "release":
                self.add("released", thread="work")
            elif name not in {"prepare", "boundary", "collect", "drain"}:
                raise AssertionError(name)
            self.check_prefix(o)
        # Rule 6 applies to lock-holding states; starts have no lock to lose.
        if not o.alive and (previous in LIVE or previous in {"exited", "ended"}):
            assert state_of(self.facts, self.strand, self.gen, alive=self.alive).name == "ended"
        # The running closure carries the generation's proof marker forward.
        expected = TABLE[row].next.split(" / ")
        assert state_of(self.facts, self.strand, self.gen, alive=self.alive).name in expected


def test_seeded_sequences_check_ledger_prefixes_and_reach_every_row():
    coverage = set()
    # Random orders include reopening, proof recovery, loss of the lock,
    # gauge changes and a crash between end and drain, across generations.
    for seed in range(20):
        rng = random.Random(seed)
        body = MemoryBody(rng, coverage)
        for _ in range(350):
            state = state_of(body.facts, body.strand, body.gen, alive=body.alive).name
            if state == "ended" and fact_for(body.facts, "released", body.strand, body.gen):
                body.new_generation()
                state = "wanted"
            body.now += rng.uniform(0.1, 4)
            if body.alive and rng.random() < 0.12:
                body.alive = False
            adopted = state == "wanted" and rng.random() < 0.08
            if adopted:
                body.add("shown", ids=[])
                body.alive = True
            closed = rng.random() < 0.65
            gauge = rng.choice(["ok", "ok", "refreshing", "empty"])
            failure = "spawn failed" if state in STARTABLE and not body.alive and rng.random() < 0.1 else ""
            o = Observation(body.strand, body.gen, body.alive, closed, gauge, body.now,
                            proven=rng.choice([True, False]), until=body.now + 3,
                            stop_grace=1, failure=failure)
            body.step(o, crash_at_end=not body.alive and rng.random() < 0.2,
                      crash_at_exit=not body.alive and rng.random() < 0.2)
    assert coverage == set(range(len(TABLE))), {i: TABLE[i] for i in set(range(len(TABLE))) - coverage}


def test_table_rendering_matches_checked_in_page():
    from pathlib import Path
    page = Path(__file__).resolve().parents[2] / "src/brr/loom/runtime/bodies.md"
    assert render_table() == page.read_text().strip()
    assert len(TABLE) == 19


def test_an_unlisted_pair_logs_and_raises(caplog):
    facts = [Fact(by="loom:aaaa", kind="body.leaving", data={"strand": "s", "gen": 1, "deadline": 2})]
    # The stop row requires an explicit proof observation, including False.
    with pytest.raises(ValueError, match="body table refused"):
        decide(facts, [Observation("s", 1, True, True, "ok", 3, proven=None)])
    assert "body table refused" in caplog.text
    assert "leaving" in caplog.text


def test_state_of_is_scoped_and_retains_all_generation_markers():
    facts = [Fact(by="loom:aaaa", kind="body.started", data={"strand": "s", "gen": 1}),
             Fact(by="loom:aaaa", kind="body.leaving", data={"strand": "s", "gen": 1, "deadline": 3}),
             Fact(by="loom:aaaa", kind="body.stayed", data={"strand": "s", "gen": 1}),
             Fact(by="loom:aaaa", kind="body.pending", data={"strand": "s", "gen": 2})]
    assert state_of(facts, "s", 1).name == "running"
    assert state_of(facts, "s", 2).name == "wanted"
    assert state_of(facts, "other", 1).name == "wanted"
    facts.append(Fact(by="loom:aaaa", kind="attempt.ended", data={"strand": "s", "gen": 1}))
    assert state_of(facts, "s", 1).name == "ended"


@pytest.mark.parametrize("gauge", ["ok", "refreshing", "empty"])
@pytest.mark.parametrize("kind", ["lease", "body.pending", "body.parked"])
def test_closed_deferred_start_ends_before_every_gauge_row(kind, gauge):
    facts = [Fact(by="loom:aaaa", kind=kind, data={"strand": "s", "gen": 1, "until": 100})]
    commands = decide(facts, [Observation("s", 1, False, True, gauge, 3)])
    assert [c.name for c in commands] == ["end_settled", "drain", "drained", "release"]


def test_all_facts_keep_stop_and_proof_markers_after_reopening():
    def fact(kind, **extra):
        return Fact(by="loom:aaaa", kind=kind, data={"strand": "s", "gen": 1, **extra})
    facts = [fact("lease", shell="fake", core="echo"), fact("body.started"),
             fact("body.leaving", deadline=2), fact("body.unidentified"), fact("body.stayed")]
    state = state_of(facts, "s", 1)
    assert state.name == "running" and not state.identified
    facts += [fact("body.stopped"), fact("body.pending")]
    state = state_of(facts, "s", 1)
    assert state.name == "stopping" and state.stopped and not state.identified
    facts.append(fact("body.exited"))
    assert state_of(facts, "s", 1).name == "exited"
    facts.append(fact("attempt.ended"))
    assert state_of(facts, "s", 1).name == "ended"


def test_lease_with_lock_is_running_before_started_receipt():
    facts = [Fact(kind="lease", by="loom:aaaa", data={"strand": "s", "gen": 1, "shell": "fake", "core": "echo"})]
    assert state_of(facts, "s", 1, alive=True).name == "running"
    assert decide(facts, [Observation("s", 1, True, False, "ok", 3)]) == []


def test_reopening_replaces_the_deadline_only_after_a_new_closure():
    facts = [Fact(by="loom:aaaa", kind="body.started", data={"strand": "s", "gen": 1}),
             Fact(by="loom:aaaa", kind="body.leaving", data={"strand": "s", "gen": 1, "deadline": 5})]
    o = Observation("s", 1, True, False, "ok", 3)
    assert [c.name for c in decide(facts, [o])] == ["stayed"]
    facts.append(Fact(by="loom:aaaa", kind="body.stayed", data={"strand": "s", "gen": 1}))
    commands = decide(facts, [replace(o, closed=True, now=4, stop_grace=10)])
    assert commands[0].name == "leaving" and commands[0].deadline == 14


def test_restart_mid_grace_honours_the_original_deadline(tmp_path):
    """Drive two real python -m loops, with downtime spanning the grace."""
    from _step import wait_until
    from test_router import Proc, _config, _dump, _stop
    from test_stop_settled_body import _home, _ready, _facts, _child_pid, _assert_stopped

    root = _home(tmp_path)
    grace = 2.0
    _config(root, stop_grace=grace)
    procs = []
    try:
        first = Proc(root, "aaaa")
        procs.append(first)
        wait_until(lambda: _ready(root, _facts(root)), 12, lambda: _dump(root, procs))
        pid = _child_pid(root)
        (root / "go").write_text("go")
        wait_until(lambda: any(f.kind == "body.leaving" and f.data.get("strand") ==
                   next(s.data["strand"] for s in _facts(root) if s.kind == "body.started" and s.data["pid"] == pid)
                   for f in _facts(root)), 12, lambda: _dump(root, procs))
        leaving = next(f for f in _facts(root) if f.kind == "body.leaving")
        deadline = leaving.data["deadline"]
        assert deadline > time.time()
        os.kill(first.proc.pid, signal.SIGTERM)
        first.proc.wait(timeout=5)
        # The test's downtime is intentional, bounded, and not a work wait.
        time.sleep(max(0, deadline - time.time()) + 0.1)
        second = Proc(root, "aaaa")
        procs.append(second)
        wait_until(lambda: any(f.kind == "attempt.ended" and f.data.get("thread") == "child"
                              for f in _facts(root)), grace - 0.2, lambda: _dump(root, procs))
        facts = _facts(root)
        assert [f.id for f in facts if f.kind == "body.leaving"] == [leaving.id]
        assert next(f for f in facts if f.kind == "body.leaving").data["deadline"] == deadline
        stopped = next(f for f in facts if f.kind == "body.stopped")
        from datetime import datetime
        assert datetime.fromisoformat(stopped.at).timestamp() >= deadline
        _assert_stopped(root, pid, facts)
    finally:
        _stop(root, procs)


def test_exit_cause_uses_the_post_drain_ledger_in_contract_order():
    o = Observation("s", 1, False, False, "empty", 3)
    assert exit_cause([], o, owed=True) == "paced"
    assert exit_cause([], o, owed=False) == "idle"
    assert exit_cause([], replace(o, gauge="ok"), owed=True) == "failed"
    assert exit_cause([], replace(o, closed=True), owed=True) == "settled"
    facts = [Fact(kind="lease", by="loom:aaaa", data={"thread": "work", "strand": "s", "gen": 1}),
             Fact(kind="molt", by="strand:s", data={"strand": "s", "gen": 1})]
    assert exit_cause(facts, replace(o, closed=True), owed=True) == "molted"
    facts.append(Fact(kind="body.stopped", by="loom:aaaa", data={"strand": "s", "gen": 1}))
    assert exit_cause(facts, replace(o, closed=True), owed=True) == "stopped"


@pytest.mark.parametrize("adopted", [False, True])
def test_spawn_without_started_then_lock_loss_never_starts_twice(adopted):
    body = MemoryBody(random.Random(0), set())
    o = Observation(body.strand, body.gen, False, False, "ok", 1)
    body.step(o, crash_at_spawn=True)
    assert fact_for(body.facts, "shown", body.strand, body.gen)
    assert not fact_for(body.facts, "body.started", body.strand, body.gen)
    if adopted:
        body.step(replace(o, alive=True, now=2))
        assert body.observed_alive
    # The child left before recovery; prior liveness must not be recomputed
    # using this false observation. Rule 6 and every prefix of rule 1 apply.
    body.step(replace(o, now=3), crash_at_exit=True)
    body.step(replace(o, now=4))
    assert body.starts == [(body.strand, body.gen)]
    assert state_of(body.facts, body.strand, body.gen).name == "ended"


@pytest.mark.parametrize("second_proven", [False, True])
def test_reclosed_body_probes_once_per_deadline_and_stopping_holds(tmp_path, monkeypatch, second_proven):
    from types import SimpleNamespace
    from brr.loom.runtime import loom as runtime, config, lifecycle, usage

    clock, closed, proofs, signals, boundaries = [1], [True], [], [], []
    home = SimpleNamespace(root=tmp_path, room=lambda strand: tmp_path / strand)
    monkeypatch.setattr(runtime.time, "time", lambda: clock[0])
    monkeypatch.setattr(config, "load_config", lambda root: SimpleNamespace(hold_floor_pct=10, stop_grace=2))
    monkeypatch.setattr(lifecycle, "read_thread", lambda home, thread: {"status": "settled" if closed[0] else "open"})
    monkeypatch.setattr(lifecycle, "closing_commit", lambda home, thread: ("sha", "done", "writer"))
    monkeypatch.setattr(usage, "reading", lambda *args: {"windows": []})
    monkeypatch.setattr(runtime, "body_alive", lambda room: True)
    monkeypatch.setattr(runtime, "_boundary", lambda *args: boundaries.append(clock[0]))
    monkeypatch.setattr(runtime, "_end_group", lambda pid: signals.append(pid))
    def proof(*args, **kwargs):
        proofs.append(clock[0])
        return second_proven if len(proofs) == 2 else False
    monkeypatch.setattr(runtime, "_proven_body", proof)
    monkeypatch.setattr(runtime, "_record", lambda home, kind, data, ident: Fact(by="loom:aaaa", kind=kind, data=data, id=ident))
    def fact(kind, **data):
        return Fact(by="loom:aaaa", kind=kind, data={"strand": "s", "gen": 1, **data})
    facts = [fact("lease", shell="fake", core="echo"), fact("body.started", pid=123, lstart="start")]
    def tick(now, is_closed=True):
        nonlocal facts
        clock[0], closed[0] = now, is_closed
        o, c = runtime._observe_body(home, facts, "s", "work", 1, {})
        facts = runtime._execute_body(home, {}, facts, o, c)
    tick(1)  # first deadline: 3
    tick(2)
    tick(3)  # failed proof
    tick(4)  # no retry on an unidentified tick
    tick(5, False)
    tick(6)  # new deadline: 8
    tick(7)
    tick(8)  # one fresh proof, despite the old failed proof
    tick(9)
    tick(10, False)  # even a reopened stopping child keeps its lock/holder
    # A stopping body is proven again on every tick it is still alive.
    assert proofs == [3, 8] + ([9, 10] if second_proven else [])
    assert [f.data["deadline"] for f in facts if f.kind == "body.leaving"] == [3, 8]
    unidentified = [f for f in facts if f.kind == "body.unidentified"]
    assert len(unidentified) == (1 if second_proven else 2)
    assert len({f.id for f in unidentified}) == len(unidentified)
    assert signals == ([123] if second_proven else [])
    assert boundaries == list(range(1, 11))
    if second_proven:
        assert state_of(facts, "s", 1, alive=True).name == "stopping"
        assert decide(facts, [Observation("s", 1, True, True, "ok", 11)]) == []


def test_later_shown_receipt_does_not_reset_a_leaving_body():
    def fact(kind, **data):
        return Fact(by="loom:aaaa", kind=kind, data={"strand": "s", "gen": 1, **data})
    facts = [fact("shown", ids=[]), fact("body.leaving", deadline=5), fact("shown", ids=["later"])]
    state = state_of(facts, "s", 1, alive=True)
    assert state.name == "leaving" and state.deadline == 5


def test_a_stop_on_record_with_the_body_alive_is_signalled_again_only_with_proof():
    """The loom can die between writing body.stopped and sending the signal."""
    def fact(kind, **data):
        return Fact(by="loom:aaaa", kind=kind, data={"strand": "s", "gen": 1, **data})
    facts = [fact("body.started", pid=123), fact("body.leaving", deadline=2), fact("body.stopped", pid=123)]
    o = Observation("s", 1, True, True, "ok", 3, proven=True)
    assert [c.name for c in decide(facts, [o])] == ["signal"]
    assert decide(facts, [replace(o, proven=False)]) == []
    assert decide(facts, [replace(o, proven=None)]) == []
    # Reopening the thread does not take the stop back.
    assert [c.name for c in decide(facts, [replace(o, closed=False)])] == ["signal"]

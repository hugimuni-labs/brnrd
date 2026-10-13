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
        self.gen = 0
        self.alive = False
        self.now = 0.0
        self.new_generation()

    def new_generation(self):
        self.gen += 1
        self.strand = f"s-aaaa-{self.gen:06d}"
        self.add("lease", thread="work")
        self.alive = False

    def add(self, kind, **data):
        self.facts.append(Fact(kind=kind, by="loom:aaaa", id=f"fact-{len(self.facts)}",
                               data={"strand": self.strand, "gen": self.gen, **data}))

    def check_prefix(self, o):
        # Rule 1: all generations, not just the current one.
        for kind in ("body.started", "body.exited", "attempt.ended"):
            ids = [(f.data["strand"], f.data["gen"]) for f in self.facts if f.kind == kind]
            assert len(ids) == len(set(ids)), (kind, ids)
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

    def step(self, o, *, crash_at_end=False):
        previous = state_of(self.facts, self.strand, self.gen)
        row = row_for(self.facts, o)
        self.coverage.add(row)
        commands = decide(self.facts, [o])
        assert commands == decide(self.facts, [o])
        for c in commands:
            assert c.row == row
            name = c.name
            if name == "signal":
                # Rule 2: proof belongs to this decision, never an older fact.
                assert o.proven is True
            elif name == "started":
                # Rule 4. Generated starts are in the router's open-thread
                # domain; the legacy closed-deferred exception is pinned below.
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
                if fact_for(self.facts, "body." + name, self.strand, self.gen) is None:
                    if name == "stopped":
                        # Rule 3: closed, and this generation's durable deadline.
                        leaving = fact_for(self.facts, "body.leaving", self.strand, self.gen)
                        assert o.closed and leaving is not None
                        assert o.now >= leaving.data["deadline"]
                    self.add("body." + name)
            elif name == "exit":
                if fact_for(self.facts, "body.exited", self.strand, self.gen) is None:
                    self.add("body.exited")
            elif name in {"end", "end_failed"}:
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
            elif name not in {"prepare", "shown", "spawn", "boundary", "collect", "drain"}:
                raise AssertionError(name)
            self.check_prefix(o)
        # Rule 6 applies when an existing body loses its lock. Deferred starts
        # have no lock to lose (the prose rule and start rows contradict).
        if not o.alive and (previous in LIVE or o.orphan or previous == "ended"):
            assert state_of(self.facts, self.strand, self.gen) == "ended"
        expected = previous if TABLE[row].next == "same" else TABLE[row].next
        if TABLE[row].commands:
            # Adoption without a body.started cannot synthesize a ledger state.
            if TABLE[row].observed != "adopted, open":
                assert state_of(self.facts, self.strand, self.gen) == expected


def test_seeded_sequences_check_ledger_prefixes_and_reach_every_row():
    coverage = set()
    # Random orders include reopening, proof recovery, loss of the lock,
    # gauge changes and a crash between end and drain, across generations.
    for seed in range(20):
        rng = random.Random(seed)
        body = MemoryBody(rng, coverage)
        for _ in range(350):
            state = state_of(body.facts, body.strand, body.gen)
            if state == "ended" and fact_for(body.facts, "released", body.strand, body.gen):
                # Exercise the ended self row before beginning a new lease.
                body.step(Observation(body.strand, body.gen, False, False, "ok", body.now))
                body.new_generation()
                state = "wanted"
            body.now += rng.uniform(0.1, 4)
            if body.alive and rng.random() < 0.12:
                body.alive = False
            adopted = state == "wanted" and rng.random() < 0.08
            if adopted:
                body.alive = True
            closed = rng.random() < 0.65 if body.alive or state in LIVE else False
            gauge = rng.choice(["ok", "ok", "unreadable", "empty"])
            failure = "spawn failed" if state in STARTABLE and not body.alive and rng.random() < 0.1 else ""
            o = Observation(body.strand, body.gen, body.alive, closed, gauge, body.now,
                            proven=rng.choice([True, False]), until=body.now + 3,
                            stop_grace=1, failure=failure,
                            orphan=state == "wanted" and not adopted and rng.random() < 0.12)
            body.step(o, crash_at_end=not body.alive and rng.random() < 0.2)
    assert coverage == set(range(len(TABLE))), {i: TABLE[i] for i in set(range(len(TABLE))) - coverage}


def test_table_rendering_is_the_code_table(capsys):
    text = render_table()
    print(text)
    lines = capsys.readouterr().out.splitlines()
    assert lines[:2] == ["| state | observed | next | commands |", "|---|---|---|---|"]
    assert len(lines) == len(TABLE) + 2
    for row, line in zip(TABLE, lines[2:]):
        assert line == f"| {', '.join(row.states)} | {row.observed} | {row.next} | {', '.join(row.commands) or 'none'} |"


def test_an_unlisted_pair_logs_and_raises(caplog):
    facts = [Fact(by="loom:aaaa", kind="body.leaving", data={"strand": "s", "gen": 1, "deadline": 2})]
    # The stop row requires an explicit proof observation, including False.
    with pytest.raises(ValueError, match="body table refused"):
        decide(facts, [Observation("s", 1, True, True, "ok", 3, proven=None)])
    assert "body table refused" in caplog.text
    assert "leaving" in caplog.text


def test_state_of_is_scoped_to_generation_and_latest_lifecycle_fact():
    facts = [Fact(by="loom:aaaa", kind="body.started", data={"strand": "s", "gen": 1}),
             Fact(by="loom:aaaa", kind="body.leaving", data={"strand": "s", "gen": 1, "deadline": 3}),
             Fact(by="loom:aaaa", kind="body.stayed", data={"strand": "s", "gen": 1}),
             Fact(by="loom:aaaa", kind="body.pending", data={"strand": "s", "gen": 2})]
    assert state_of(facts, "s", 1) == "running"
    assert state_of(facts, "s", 2) == "pending"
    assert state_of(facts, "other", 1) == "wanted"
    facts.append(Fact(by="loom:aaaa", kind="attempt.ended", data={"strand": "s", "gen": 1}))
    assert state_of(facts, "s", 1) == "ended"


def test_closed_deferred_start_keeps_the_existing_gauge_path():
    """Contract mismatch: main retried pending starts even after closure."""
    facts = [Fact(by="loom:aaaa", kind="body.pending", data={"strand": "s", "gen": 1})]
    commands = decide(facts, [Observation("s", 1, False, True, "ok", 3)])
    assert [c.name for c in commands] == ["prepare", "shown", "spawn", "started"]


def test_reopening_replaces_the_deadline_only_after_a_new_closure():
    facts = [Fact(by="loom:aaaa", kind="body.started", data={"strand": "s", "gen": 1}),
             Fact(by="loom:aaaa", kind="body.leaving", data={"strand": "s", "gen": 1, "deadline": 5})]
    o = Observation("s", 1, True, False, "ok", 3)
    assert [c.name for c in decide(facts, [o])] == ["stayed", "boundary"]
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

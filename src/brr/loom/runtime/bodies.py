"""A body's life, decided from facts and observations; no runtime access.

Rows are ordered: the first matching observation wins. ``alive`` describes
an existing body, not a deferred start. The executor supplies configuration
and identity alongside the five observations, and reports a failed command
back as ``failure``. Commands name hands; they do not perform their work.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from brr.daemon2.facts import Fact


@dataclass(frozen=True)
class Row:
    states: tuple[str, ...]
    observed: str
    next: str
    commands: tuple[str, ...]


STARTABLE = ("wanted", "pending", "parked")
LIVE = ("running", "leaving", "stopping", "unidentified")
FINISH = ("collect", "drain", "exit", "end", "drain", "drained", "release")
FAILED = ("exit", "end_failed", "drained", "release")
TABLE = (
    Row(STARTABLE, "failure", "ended", FAILED),
    Row(("wanted",), "orphan", "ended", FINISH),
    Row(STARTABLE, "adopted, closed", "leaving", ("leaving", "boundary")),
    Row(STARTABLE, "adopted, open", "running", ("boundary",)),
    Row(("parked",), "before until", "parked", ()),
    Row(("parked",), "gauge unreadable", "parked", ()),
    Row(("wanted", "pending"), "gauge unreadable", "pending", ("pending",)),
    Row(STARTABLE, "gauge empty", "parked", ("park",)),
    Row(STARTABLE, "gauge ok", "running", ("prepare", "shown", "spawn", "started")),
    Row(LIVE, "not alive", "ended", FINISH),
    Row(("running",), "alive, open", "running", ("boundary",)),
    Row(("running",), "alive, closed", "leaving", ("leaving", "boundary")),
    Row(("leaving",), "alive, reopened", "running", ("stayed", "boundary")),
    Row(("leaving",), "alive, before deadline", "leaving", ("boundary",)),
    Row(("leaving",), "alive, due, proven", "stopping", ("boundary", "stopped", "signal")),
    Row(("leaving",), "alive, due, unproven, identified", "leaving", ("boundary",)),
    Row(("leaving",), "alive, due, unproven", "unidentified", ("boundary", "unidentified")),
    Row(("stopping",), "alive, closed, proven", "stopping", ("boundary", "signal")),
    Row(("stopping",), "alive, closed, unproven, identified", "stopping", ("boundary",)),
    Row(("stopping",), "alive, closed, unproven", "unidentified", ("boundary", "unidentified")),
    Row(("unidentified",), "alive, closed, proven, stopped", "unidentified", ("boundary", "signal")),
    Row(("unidentified",), "alive, closed, proven", "stopping", ("boundary", "stopped", "signal")),
    Row(("unidentified",), "alive, closed, unproven", "unidentified", ("boundary",)),
    # Compatibility rows: old loops kept refreshing boundaries after a stop,
    # and stopped retrying signals while a closed thread was reopened.
    Row(("unidentified",), "alive, open, not stopped", "running", ("stayed", "boundary")),
    Row(("stopping", "unidentified"), "alive, open", "same", ("boundary",)),
    Row(("ended",), "not released", "ended", ("drain", "drained", "release")),
    Row(("ended",), "released", "ended", ()),
)


@dataclass(frozen=True)
class Observation:
    strand: str
    gen: int
    alive: bool
    closed: bool
    gauge: str
    now: float
    proven: bool | None = None
    # Values carried by observations / runtime configuration, never read here.
    until: float = 0
    force: bool = False
    stop_grace: float = 60
    failure: str = ""
    orphan: bool = False


@dataclass(frozen=True)
class Command:
    name: str
    strand: str
    gen: int
    # The row is a receipt useful to renderers and generated-sequence tests.
    row: int
    deadline: float | None = None


def fact_for(facts: list[Fact], kind: str, strand: str, gen: int) -> Fact | None:
    return next((f for f in reversed(facts) if f.kind == kind
                 and f.data.get("strand") == strand and f.data.get("gen") == gen), None)


def state_of(facts: list[Fact], strand: str, gen: int) -> str:
    if fact_for(facts, "attempt.ended", strand, gen):
        return "ended"
    latest = next((f for f in reversed(facts) if f.kind.startswith("body.")
                   and f.data.get("strand") == strand and f.data.get("gen") == gen), None)
    if latest is None:
        return "wanted"
    states = {"body.pending": "pending", "body.parked": "parked",
              "body.started": "running", "body.stayed": "running",
              "body.leaving": "leaving", "body.stopped": "stopping",
              "body.unidentified": "unidentified", "body.exited": "running"}
    try:
        return states[latest.kind]
    except KeyError:
        _refuse(f"unknown body fact {latest.kind} for {strand}/{gen}")


def _refuse(message: str):
    logging.getLogger(__name__).error("body table refused: %s", message)
    raise ValueError(f"body table refused: {message}")


def row_for(facts: list[Fact], observation: Observation) -> int:
    o = observation
    state = state_of(facts, o.strand, o.gen)
    pending = fact_for(facts, "body.parked", o.strand, o.gen)
    leaving = fact_for(facts, "body.leaving", o.strand, o.gen)
    deadline = leaving.data["deadline"] if leaving else None
    unidentified = fact_for(facts, "body.unidentified", o.strand, o.gen) is not None
    matches = {
        "failure": bool(o.failure),
        "before until": bool(not o.alive and not o.force and pending and o.now < pending.data.get("until", 0)),
        "gauge unreadable": not o.alive and o.gauge == "unreadable",
        "gauge empty": not o.alive and o.gauge == "empty",
        "gauge ok": not o.alive and o.gauge == "ok",
        "not alive": not o.alive,
        "alive, open, not stopped": o.alive and not o.closed and fact_for(facts, "body.stopped", o.strand, o.gen) is None,
        "alive, open": o.alive and not o.closed,
        "alive, closed": o.alive and o.closed,
        "alive, reopened": o.alive and not o.closed,
        "alive, before deadline": o.alive and o.closed and deadline is not None and o.now < deadline,
        "alive, due, proven": o.alive and o.closed and deadline is not None and o.now >= deadline and o.proven is True,
        "alive, due, unproven, identified": o.alive and o.closed and deadline is not None and o.now >= deadline and o.proven is False and unidentified,
        "alive, due, unproven": o.alive and o.closed and deadline is not None and o.now >= deadline and o.proven is False,
        "alive, closed, proven, stopped": o.alive and o.closed and o.proven is True and fact_for(facts, "body.stopped", o.strand, o.gen) is not None,
        "alive, closed, proven": o.alive and o.closed and o.proven is True,
        "alive, closed, unproven, identified": o.alive and o.closed and o.proven is False and unidentified,
        "alive, closed, unproven": o.alive and o.closed and o.proven is False,
        "orphan": not o.alive and o.orphan,
        "adopted, closed": o.alive and o.closed,
        "adopted, open": o.alive and not o.closed,
        "not released": fact_for(facts, "released", o.strand, o.gen) is None,
        "released": fact_for(facts, "released", o.strand, o.gen) is not None,
    }
    for i, row in enumerate(TABLE):
        if state in row.states and matches[row.observed]:
            return i
    _refuse(f"{state} × {o!r}")


def decide(facts: list[Fact], observations: list[Observation]) -> list[Command]:
    commands = []
    for o in observations:
        row = row_for(facts, o)
        deadline = o.now + o.stop_grace if "leaving" in TABLE[row].commands else None
        for name in TABLE[row].commands:
            # A pending gauge is recorded once; other facts do not count as
            # a change in the deferred body's gauge.
            if name == "pending" and state_of(facts, o.strand, o.gen) in {"pending", "parked"}:
                continue
            commands.append(Command(name, o.strand, o.gen, row, deadline))
    return commands


def render_table() -> str:
    rows = ["| state | observed | next | commands |", "|---|---|---|---|"]
    rows.extend(f"| {', '.join(row.states)} | {row.observed} | {row.next} | {', '.join(row.commands) or 'none'} |"
                for row in TABLE)
    return "\n".join(rows)


def exit_cause(facts: list[Fact], o: Observation, *, owed: bool) -> str:
    """First-match cause over the post-drain ledger and the same observations."""
    from .project import fold
    if fact_for(facts, "body.stopped", o.strand, o.gen):
        return "stopped"
    if fact_for(fold(facts).accepted, "molt", o.strand, o.gen):
        return "molted"
    if o.closed:
        return "settled"
    if not owed:
        return "idle"
    return "paced" if o.gauge == "empty" else "failed"

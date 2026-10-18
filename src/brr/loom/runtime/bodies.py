"""One body's lifecycle. Facts determine state; observations select a row."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from brr.daemon2.facts import Fact

from .project import cached_projection


@dataclass(frozen=True)
class State:
    name: str = "wanted"
    until: float = 0
    deadline: float = 0
    pending: bool = False
    stopped: bool = False
    identified: bool = True
    released: bool = False


@dataclass(frozen=True)
class Row:
    states: tuple[str, ...]
    observed: str
    next: str
    commands: tuple[str, ...]


STARTABLE = ("wanted", "parked")
LIVE = ("running", "leaving", "stopping", "unidentified")
CLEANUP = ("drain", "drained", "release")
END = ("end",) + CLEANUP
FINISH = ("collect", "drain", "exit") + END
TABLE = (
    Row(STARTABLE, "closed", "ended", ("end_settled",) + CLEANUP),
    Row(STARTABLE, "failure", "ended", ("exit", "end_failed") + CLEANUP),
    Row(("parked",), "gauge unknown, before until", "parked", ()),
    Row(("parked",), "gauge refreshing", "parked", ()),
    Row(("wanted",), "gauge refreshing", "wanted", ("pending",)),
    Row(("parked",), "gauge empty", "parked", ()),
    Row(STARTABLE, "gauge empty", "parked", ("park",)),
    Row(STARTABLE, "gauge not empty", "running", ("prepare", "shown", "spawn", "started")),
    Row(LIVE, "not alive", "ended", FINISH),
    Row(("running",), "alive, open", "running", ()),
    Row(("running",), "alive, closed", "leaving", ("leaving",)),
    Row(("leaving", "unidentified"), "alive, reopened", "running", ("stayed",)),
    Row(("leaving",), "alive, before deadline", "leaving", ()),
    Row(("leaving",), "alive, due, proven", "stopping", ("stopped", "signal")),
    Row(("leaving",), "alive, due, unproven", "unidentified", ("unidentified",)),
    Row(("stopping",), "alive, proven", "stopping", ("signal",)),
    Row(("stopping",), "alive, not proven", "stopping", ()),
    Row(("unidentified",), "alive, closed", "unidentified", ()),
    Row(("exited",), "any", "ended", END),
    Row(("ended",), "not released", "ended", CLEANUP),
)


@dataclass(frozen=True)
class Step:
    """One command, declared once. ``hand`` names the function that performs
    it, ``records`` the fact it leaves, ``repeat`` whether performing it again
    with its effect already in place changes nothing. ``arg`` is the hand's
    one fixed argument."""
    name: str
    hand: str
    records: str | None
    repeat: bool
    arg: str = ""


COMMANDS = {step.name: step for step in (
    #    command         hand        records              repeat
    Step("prepare",      "prepare",  None,                True),
    Step("shown",        "show",     "shown",             False),
    Step("spawn",        "spawn",    None,                False),
    Step("started",      "started",  "body.started",      False),
    Step("pending",      "record",   "body.pending",      True),
    Step("park",         "record",   "body.parked",       True),
    Step("leaving",      "record",   "body.leaving",      False),
    Step("stayed",       "record",   "body.stayed",       True),
    Step("stopped",      "record",   "body.stopped",      False),
    Step("unidentified", "record",   "body.unidentified", True),
    Step("signal",       "signal",   None,                True),
    Step("collect",      "collect",  None,                True),
    Step("drain",        "drain",    None,                True),
    Step("exit",         "record",   "body.exited",       False),
    Step("end",          "end",      "attempt.ended",     False),
    Step("end_settled",  "end",      "attempt.ended",     False, "settled"),
    Step("end_failed",   "end",      "attempt.ended",     False, "failed"),
    Step("drained",      "drained",  "port.drained",      True),
    Step("release",      "release",  "released",          True),
)}


@dataclass(frozen=True)
class Observation:
    strand: str
    gen: int
    alive: bool
    closed: bool
    gauge: str
    now: float
    proven: bool | None = None
    until: float = 0
    force: bool = False
    stop_grace: float = 60
    failure: str = ""


@dataclass(frozen=True)
class Command:
    name: str
    strand: str
    gen: int
    row: int
    deadline: float | None = None


def fact_for(facts: list[Fact], kind: str, strand: str, gen: int) -> Fact | None:
    return next((f for f in reversed(facts) if f.kind == kind
                 and f.data.get("strand") == strand and f.data.get("gen") == gen), None)


def state_of(facts: list[Fact], strand: str, gen: int, *, alive=False) -> State:
    """Fold all generation facts, retaining once-only markers across reopenings."""
    return cached_projection(facts, "body-state", (strand, gen, alive),
                             lambda rows: _state_of(rows, strand, gen, alive=alive))


def _state_of(facts, strand, gen, *, alive):
    name, until, deadline = "wanted", 0, 0
    pending, stopped, identified, released, exited, ended = False, False, True, False, False, False
    for f in facts:
        if f.data.get("strand") != strand or f.data.get("gen") != gen:
            continue
        if f.kind == "lease" and alive:
            name = "running"
        elif f.kind == "body.pending":
            pending = True
        elif f.kind == "body.parked":
            name, until = "parked", f.data["until"]
        elif f.kind == "shown" and name in STARTABLE:
            name = "running"
        elif f.kind in {"body.started", "body.stayed"}:
            name = "running"
        elif f.kind == "body.leaving":
            name, deadline, identified = "leaving", f.data["deadline"], True
        elif f.kind == "body.stopped":
            stopped = True
        elif f.kind == "body.unidentified":
            name, identified = "unidentified", False
        elif f.kind == "body.exited":
            exited = True
        elif f.kind == "attempt.ended":
            ended = True
        elif f.kind == "released":
            released = True
    if alive and name in STARTABLE:
        name = "running"
    name = "ended" if ended else "exited" if exited else "stopping" if stopped else name
    return State(name, until, deadline, pending, stopped, identified, released)


# What a row's ``observed`` column may say, each a reading of the folded
# state and this tick's observation.
OBSERVED = {
    "closed": lambda s, o: o.closed,
    "failure": lambda s, o: bool(o.failure),
    "gauge unknown, before until": lambda s, o: not o.force and o.gauge == "unknown" and o.now < s.until,
    "gauge refreshing": lambda s, o: o.gauge == "refreshing",
    "gauge empty": lambda s, o: o.gauge == "empty",
    "gauge not empty": lambda s, o: o.gauge in {"ok", "unknown"},
    "not alive": lambda s, o: not o.alive,
    "alive, proven": lambda s, o: o.alive and o.proven is True,
    "alive, not proven": lambda s, o: o.alive and o.proven is not True,
    "alive, open": lambda s, o: o.alive and not o.closed,
    "alive, closed": lambda s, o: o.alive and o.closed,
    "alive, reopened": lambda s, o: o.alive and not o.closed,
    "alive, before deadline": lambda s, o: o.alive and o.now < s.deadline,
    "alive, due, proven": lambda s, o: o.alive and o.now >= s.deadline and o.proven is True,
    "alive, due, unproven": lambda s, o: o.alive and o.now >= s.deadline and o.proven is False,
    "any": lambda s, o: True,
    "not released": lambda s, o: not s.released,
}


def row_for(facts: list[Fact], o: Observation) -> int:
    s = state_of(facts, o.strand, o.gen, alive=o.alive)
    for i, row in enumerate(TABLE):
        if s.name in row.states and OBSERVED[row.observed](s, o):
            return i
    message = f"body table refused: {s} × {o!r}"
    logging.getLogger(__name__).error(message)
    raise ValueError(message)


def decide(facts: list[Fact], observations: list[Observation]) -> list[Command]:
    commands = []
    for o in observations:
        s = state_of(facts, o.strand, o.gen, alive=o.alive)
        row = row_for(facts, o)
        for name in TABLE[row].commands:
            if name == "pending" and s.pending or name == "unidentified" and not s.identified:
                continue
            commands.append(Command(name, o.strand, o.gen, row,
                                    o.now + o.stop_grace if name == "leaving" else None))
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

"""Pure folds over the union of the ledger. A superseded gen counts for nothing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from brr.daemon2.facts import Fact, union

from .home import thread_of


@dataclass
class Fold:
    accepted: list[Fact] = field(default_factory=list)
    holder: dict[str, tuple[str, int]] = field(default_factory=dict)
    handled: set[str] = field(default_factory=set)
    shown: dict[str, set[str]] = field(default_factory=dict)


def _strand(fact: Fact) -> str | None:
    strand = fact.data.get("strand")
    if isinstance(strand, str) and strand:
        return strand
    if fact.by.startswith("strand:"):
        return fact.by.split(":", 1)[1]
    sender = fact.data.get("from")
    if isinstance(sender, str) and sender.startswith("s-"):
        return sender
    return None


def _destination(data: dict) -> str | None:
    try:
        return thread_of(str(data.get("to")))
    except ValueError:
        return None


def lease_accepted(newest_router: int | None, fact: Fact) -> bool:
    """A lease counts only from the router that was current when it was written.

    No ``router_gen`` is a step-1 lease: it counts while no router fact
    precedes it, and counts for nothing once a router has. A lease whose
    ``router_gen`` is not the newest ``router`` fact so far was written by a
    stale router after the new one and does not take the thread.
    """
    raw = fact.data.get("router_gen")
    if raw is None:
        return newest_router is None
    try:
        grant = int(raw)
    except (TypeError, ValueError):
        return False
    return newest_router == grant


def fold(facts: Iterable[Fact]) -> Fold:
    """Replay ``facts`` in causal order.

    A letter is handled only by a note or a reply from the strand that held
    its destination thread at that moment, at that lease gen. A matching gen
    on some other strand does not count: gen numbers restart per thread.
    """
    state = Fold()
    live: dict[str, int] = {}
    dest: dict[str, str] = {}
    newest: int | None = None

    def consider(writer: str, gen: int, re: object) -> None:
        if not isinstance(re, str) or not re:
            return
        thread = dest.get(re)
        if thread is not None and state.holder.get(thread) == (writer, gen):
            state.handled.add(re)

    for fact in union(list(facts)):
        data = fact.data
        if fact.kind == "router":
            try:
                gen = int(data["gen"])
            except (KeyError, TypeError, ValueError):
                continue
            if newest is None or gen > newest:
                newest = gen
            continue
        if fact.kind == "attention.cleared":
            state.accepted.append(fact)
            continue
        if fact.kind == "lease":
            if not lease_accepted(newest, fact):
                continue
            strand, gen = str(data["strand"]), int(data["gen"])
            thread = str(data["thread"])
            previous = state.holder.get(thread)
            # A new holder fences the previous strand even across installs.
            # Step 1 only replaced ``live`` when the same strand was leased
            # again; a failover grants a different strand.
            if previous is not None and previous != (strand, gen):
                old_strand, old_gen = previous
                if live.get(old_strand) == old_gen:
                    live.pop(old_strand, None)
            state.holder[thread] = (strand, gen)
            live[strand] = gen
            state.accepted.append(fact)
            continue
        if fact.kind == "released":
            strand, gen = str(data["strand"]), int(data["gen"])
            thread = str(data["thread"])
            if state.holder.get(thread) == (strand, gen):
                state.holder.pop(thread, None)
                if live.get(strand) == gen:
                    live.pop(strand, None)
                state.accepted.append(fact)
            continue
        if fact.kind == "attention":
            state.accepted.append(fact)
            continue
        if fact.kind == "letter" and "gen" not in data:
            sender = str(data.get("from") or "")
            ingress = (fact.by.startswith("loom:")
                       and str(data.get("cites") or "").startswith("source:relay:"))
            if fact.by.startswith("person:") or sender.startswith("p-") or ingress:
                state.accepted.append(fact)
                thread = _destination(data)
                if thread is not None and data.get("id"):
                    dest[str(data["id"])] = thread
            continue
        strand = _strand(fact)
        if strand is None or "gen" not in data:
            continue
        gen = int(data["gen"])
        if live.get(strand) != gen:
            continue
        state.accepted.append(fact)
        if fact.kind == "letter":
            thread = _destination(data)
            if thread is not None and data.get("id"):
                dest[str(data["id"])] = thread
            consider(strand, gen, data.get("re"))
        elif fact.kind == "note":
            consider(strand, gen, data.get("re"))
        elif fact.kind == "shown":
            state.shown.setdefault(strand, set()).update(
                str(item) for item in data.get("ids") or ())
    return state


def holder(facts: Iterable[Fact], thread: str) -> tuple[str, int] | None:
    return fold(facts).holder.get(thread)


def owed(facts: Iterable[Fact], thread: str) -> list[Fact]:
    state = fold(facts)
    letters = []
    for fact in state.accepted:
        if fact.kind != "letter" or fact.data.get("id") in state.handled:
            continue
        if _destination(fact.data) == thread:
            letters.append(fact)
    return letters


def generation(facts: Iterable[Fact], strand: str) -> int | None:
    for holder_strand, gen in fold(facts).holder.values():
        if holder_strand == strand:
            return gen
    return None


def sender_threads(facts: Iterable[Fact]) -> dict[str, str]:
    """Latest lease wins, including a strand that has since been released."""
    found: dict[str, str] = {}
    for fact in union(list(facts)):
        if fact.kind == "lease":
            found[str(fact.data["strand"])] = str(fact.data["thread"])
    return found

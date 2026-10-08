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


def fold(facts: Iterable[Fact]) -> Fold:
    """Replay ``facts`` in causal order.

    A letter is handled only by a note or a reply from the strand that held
    its destination thread at that moment, at that lease gen. A matching gen
    on some other strand does not count: gen numbers restart per thread.
    """
    state = Fold()
    live: dict[str, int] = {}
    dest: dict[str, str] = {}

    def consider(writer: str, gen: int, re: object) -> None:
        if not isinstance(re, str) or not re:
            return
        thread = dest.get(re)
        if thread is not None and state.holder.get(thread) == (writer, gen):
            state.handled.add(re)

    for fact in union(list(facts)):
        data = fact.data
        if fact.kind == "lease":
            strand, gen = str(data["strand"]), int(data["gen"])
            state.holder[str(data["thread"])] = (strand, gen)
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
            if fact.by.startswith("person:") or sender.startswith("p-"):
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


def attempts(facts: Iterable[Fact], letter_id: str) -> int:
    """How many deaths belonged to a body that had been shown ``letter_id``."""
    count = 0
    open_ids: dict[str, set[str]] = {}
    for fact in fold(facts).accepted:
        strand = fact.data.get("strand")
        if not isinstance(strand, str):
            continue
        if fact.kind == "body.started":
            open_ids[strand] = set()
        elif fact.kind == "shown" and strand in open_ids:
            open_ids[strand].update(str(item) for item in fact.data.get("ids") or ())
        elif fact.kind == "body.died" and strand in open_ids:
            if letter_id in open_ids.pop(strand):
                count += 1
    return count


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

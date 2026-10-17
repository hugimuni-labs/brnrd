"""Pure folds over the union of the ledger. A superseded gen counts for nothing."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterable

from brr.daemon2.facts import Fact, union

from .home import thread_of
from .ledger import strand_of


@dataclass
class Fold:
    accepted: list[Fact] = field(default_factory=list)
    holder: dict[str, tuple[str, int]] = field(default_factory=dict)
    handled: set[str] = field(default_factory=set)
    shown: dict[str, set[str]] = field(default_factory=dict)
    channel_replies: dict[str, str] = field(default_factory=dict)


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


_projection_cache = ContextVar("loom_projection_cache", default=None)


@contextmanager
def projection_cache(cache=None):
    """Share replay for identical fact objects; retain snapshots to pin identity."""
    token = _projection_cache.set({} if cache is None else cache)
    try:
        yield
    finally:
        _projection_cache.reset(token)


def cached_projection(facts, name, inputs, build):
    """Reuse a pure projection of immutable fact objects and explicit inputs.

    Pin the objects just as fold does; append/replacement and caller inputs
    change the key. Without a tick context, always compute a fresh result.
    """
    cache = _projection_cache.get()
    if cache is None:
        return build(facts)
    snapshot = tuple(facts)
    key = (name, tuple(map(id, snapshot)), inputs)
    if key not in cache:
        cache[key] = (snapshot, build(snapshot))
    return cache[key][1]


def ordered_facts(facts: Iterable[Fact]) -> list[Fact]:
    cache = _projection_cache.get()
    if cache is None:
        return union(list(facts))
    snapshot = tuple(facts)
    key = ("ordered", tuple(map(id, snapshot)))
    if key not in cache:
        cache[key] = (snapshot, union(snapshot))
    return cache[key][1]


def fold(facts: Iterable[Fact]) -> Fold:
    cache = _projection_cache.get()
    if cache is None:
        return _fold(facts)
    snapshot = tuple(facts)
    key = tuple(map(id, snapshot))
    if key not in cache:
        cache[key] = (snapshot, _fold(snapshot))
    return cache[key][1]


def _fold(facts: Iterable[Fact]) -> Fold:
    """Replay ``facts`` in causal order.

    A letter is handled only by a note or a reply from the strand that held
    its destination thread at that moment, at that lease gen. A matching gen
    on some other strand does not count: gen numbers restart per thread.
    """
    state = Fold()
    live: dict[str, int] = {}
    dest: dict[str, str] = {}
    newest: int | None = None
    pending_replies: dict[str, str] = {}
    disposed: set[str] = set()

    def consider(writer: str, gen: int, re: object) -> None:
        if not isinstance(re, str) or not re:
            return
        thread = dest.get(re)
        if thread is not None and state.holder.get(thread) == (writer, gen):
            state.handled.add(re)

    for fact in ordered_facts(facts):
        data = fact.data
        if fact.kind in {"attempt.ended", "port.drained", "letter.disposed", "letter.due", "speech", "speech.part"} and fact.by.startswith("loom:"):
            state.accepted.append(fact)
            if fact.kind == "speech" and data.get("state") == "sent":
                key = str(data.get("key"))
                state.handled.add(key)
                if key in pending_replies:
                    state.handled.add(pending_replies[key])
            for disposition in data.get("dispositions", ()):
                if disposition.get("outcome") == "done":
                    disposed.add(str(disposition["id"]))
                successor = disposition.get("successor")
                if successor:
                    letter = Fact(kind="letter", by=fact.by, id=successor["id"],
                                  data=successor, at=fact.at, hlc=fact.hlc, after=(fact.id,))
                    state.accepted.append(letter)
                    target = _destination(successor)
                    if target is not None:
                        dest[letter.id] = target
            continue
        if fact.kind == "router":
            try:
                gen = int(data["gen"])
            except (KeyError, TypeError, ValueError):
                continue
            if newest is None or gen > newest:
                newest = gen
            continue
        if fact.kind == "note" and fact.by.startswith(("person:", "loom:")):
            # A person's hand note or the loom's failure disposition settles a letter.
            state.handled.add(str(data.get("re")))
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
                       and str(data.get("cites") or "").startswith("source:"))
            if (fact.by.startswith("person:") or sender.startswith("p-") or ingress
                    or is_notice(fact)):
                state.accepted.append(fact)
                thread = _destination(data)
                if thread is not None and data.get("id"):
                    dest[str(data["id"])] = thread
            continue
        strand = strand_of(fact)
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
            re = data.get("re")
            if str(data.get("to", "")).startswith("channel:"):
                if isinstance(re, str) and state.holder.get(dest.get(re)) == (strand, gen):
                    pending_replies[fact.id] = re
                    state.channel_replies[re] = fact.id
            else:
                consider(strand, gen, re)
        elif fact.kind == "note":
            consider(strand, gen, data.get("re"))
        elif fact.kind == "shown":
            state.shown.setdefault(strand, set()).update(
                str(item) for item in data.get("ids") or ())
    # A notice that stands for a letter and that letter settle together.
    for fact in state.accepted:
        pair = {fact.data.get("id"), fact.data.get("stands")}
        if is_notice(fact) and None not in pair and pair & state.handled:
            state.handled |= pair
    # Replacing a debt does not answer its successor. Only explicit handling
    # above propagates through a standing notice.
    state.handled |= disposed
    return state


def is_notice(fact: Fact) -> bool:
    """A letter the loom wrote about a condition it could not settle itself."""
    return (fact.kind == "letter" and fact.by.startswith("loom:")
            and fact.data.get("from") == "loom")


def holder(facts: Iterable[Fact], thread: str) -> tuple[str, int] | None:
    return fold(facts).holder.get(thread)


def generation(facts: Iterable[Fact], strand: str) -> int | None:
    for holder_strand, gen in fold(facts).holder.values():
        if holder_strand == strand:
            return gen
    return None


def sender_threads(facts: Iterable[Fact]) -> dict[str, str]:
    """Latest lease wins, including a strand that has since been released."""
    return cached_projection(facts, "senders", (), _sender_threads)


def _sender_threads(facts):
    found: dict[str, str] = {}
    for fact in fold(facts).accepted:
        if fact.kind == "lease":
            found[str(fact.data["strand"])] = str(fact.data["thread"])
    return found


def reply_channels(facts: Iterable[Fact], home=None) -> dict[str, str]:
    """Letter id → the chat it came from, for letters a connector brought in.

    Only an address ``send`` accepts is offered; any disabled instance stays ``-``.
    """
    from .connector import instances
    enabled = frozenset(instances(home)) if home is not None else None
    return cached_projection(facts, "reply-channels", enabled,
                             lambda rows: _reply_channels(rows, enabled))


def _reply_channels(facts, enabled):
    from .home import channel_parts
    facts = list(facts)
    sources = {fact.id: fact.data for fact in facts if fact.kind == "source"}
    found: dict[str, str] = {}
    for fact in facts:
        source = sources.get(fact.data.get("cites")) if fact.kind == "letter" else None
        if not source or not source.get("channel"):
            continue
        channel = source["channel"]
        try:
            instance, _ = channel_parts(channel)
            if enabled is not None and instance not in enabled:
                continue
        except ValueError:
            continue
        found[str(fact.data.get("id"))] = channel
    return found

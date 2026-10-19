"""Asks and the strand↔thread graph: read from the ledger and main, never stored."""

from __future__ import annotations

from dataclasses import dataclass

from .lifecycle import CLOSED
from .owners import unanswered_by_thread
from .project import _destination, fold, sender_threads
from .wishes import wishes, wish_line


@dataclass(frozen=True)
class Edge:
    strand: str
    thread: str
    how: str      # ran: the strand's own thread · sent: a thread it wrote to
    origin: str   # person · thread:<t> · loom


def origin(letter, writers) -> str:
    sender = str(letter.data.get("from", ""))
    if sender.startswith(("p-", "person:")):
        return "person"
    if sender == "loom" or letter.by.startswith("loom:"):
        return "loom"
    return "thread:" + writers.get(letter.by.removeprefix("strand:"), "?")


def graph(facts) -> list[Edge]:
    """A strand ran on its lease's thread, woken by the first letter it was shown;
    it served each other thread it wrote to, for whoever wrote the letter it answered."""
    accepted = fold(facts).accepted
    writers = sender_threads(facts)
    letters = {f.id: f for f in accepted if f.kind == "letter"}
    woke = {}
    for f in accepted:
        if f.kind == "shown":
            woke.setdefault(f.data["strand"], next(
                (letters[i] for i in f.data.get("ids", ()) if i in letters), None))
    edges = []
    for f in accepted:
        if f.kind == "lease":
            strand = f.data["strand"]
            first = woke.get(strand)
            edges.append(Edge(strand, f.data["thread"], "ran",
                              origin(first, writers) if first else "loom"))
        elif f.kind == "letter" and f.by.startswith("strand:"):
            strand = f.by.removeprefix("strand:")
            thread, own = _destination(f.data), writers.get(strand)
            if thread and own and thread != own:
                answered = letters.get(f.data.get("re"))
                edges.append(Edge(strand, thread, "sent",
                                  origin(answered, writers) if answered else "thread:" + own))
    return list(dict.fromkeys(edges))


def render_graph(edges: list[Edge], by: str = "thread", only: str | None = None) -> str:
    groups: dict[str, list[str]] = {}
    for e in edges:
        key, other = (e.thread, e.strand) if by == "thread" else (e.strand, e.thread)
        if only is None or only == key:
            groups.setdefault(key, []).append(f"  {other} · {e.how} · {e.origin}")
    return "".join(f"{key}\n" + "\n".join(lines) + "\n" for key, lines in groups.items())


def asks(facts, main: dict, thread: str | None = None, everything: bool = False) -> str:
    """Four buckets over every thread, settled ones included."""
    holder = fold(facts).holder
    found = wishes(facts).values()
    mine = lambda t: thread is None or t == thread  # noqa: E731
    buckets = {
        "in hand": [f"{t} · {holder[t][0]} · plan {d['plan']}" for t, d in sorted(main.items())
                    if mine(t) and t in holder and d.get("status") not in CLOSED],
        "yours": [wish_line(w) for w in found
                  if mine(w.thread) and w.open and w.sender == "resident"],
        "unanswered": [f"{t} · {i}" for t, ids in sorted(unanswered_by_thread(facts, main).items())
                       if mine(t) for i in ids],
        "done": [f"{t} · {kind} {ref} — {line}" for t, d in sorted(main.items()) if mine(t)
                 for kind, ref, line in d.get("done") or ()]
                + [wish_line(w) for w in found if mine(w.thread) and w.state == "accepted"],
    }
    text = ""
    for name, lines in buckets.items():
        if name == "done" and lines and not everything:
            lines = [f"{len(lines)} · asks --all"]
        if lines:
            text += name + "\n" + "\n".join(lines) + "\n"
    return text

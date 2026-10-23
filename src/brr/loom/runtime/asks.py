"""Asks and the strand↔thread graph: read from the ledger and main, never stored."""

from __future__ import annotations

from dataclasses import dataclass

from .lifecycle import CLOSED
from .owners import unanswered_by_thread
from .project import _destination, fold, sender_threads
from .receipts import receipts
from .wishes import waits_on, waits_word, wishes, wish_line


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
    told = wishes(facts)
    found = told.values()
    heads = waits_on(told, main)
    mine = lambda t: thread is None or t == thread  # noqa: E731
    buckets = {
        "in hand": [f"{t} · {holder[t][0]} · plan {d['plan']}" for t, d in sorted(main.items())
                    if mine(t) and t in holder and d.get("status") not in CLOSED],
        "yours": [wish_line(w) + waits_word(heads[w.id]) for w in found
                  if mine(w.thread) and w.id in heads],
        "unanswered": [f"{t} · {i}" for t, ids in sorted(unanswered_by_thread(facts, main).items())
                       if mine(t) for i in ids],
        "done": [f"{t} · {kind} {ref} — {line}" for t, d in sorted(main.items()) if mine(t)
                 for kind, ref, line in d.get("done") or ()]
                + [f"{t} · {r.line()}" for t, rs in sorted(receipts(facts).items()) if mine(t)
                   for r in rs]
                + [wish_line(w) for w in found if mine(w.thread) and w.state == "accepted"],
    }
    text = ""
    for name, lines in buckets.items():
        if name == "done" and lines and not everything:
            lines = [f"{len(lines)} · asks --all"]
        if lines:
            text += name + "\n" + "\n".join(lines) + "\n"
    return text


def bead(facts, main: dict, ident: str) -> dict | None:
    """One item and what it hangs from, read from the facts: never stored.

    A letter walks up its ``re`` (or first letter link) to a person's letter,
    the root, whose neighbours on that person's strip are their letters just
    before and after it in fact order. It also names the wishes whose says
    cite it, the threads and strands it touched, and the letters answering it.
    """
    from .dispositions import is_person, letter_index
    from .ledger import fact_order
    from .refs import Index, kind_of
    from .threads import opened_for
    index = Index(facts, main)
    found = index.resolve(ident.removeprefix("&"))
    if found is None:
        return None
    letters, told, writers, edges = index.letters, wishes(facts), sender_threads(facts), graph(facts)
    kind = kind_of(found)

    def strands_on(thread):
        return sorted({e.strand for e in edges if e.thread == thread})

    if kind == "wish":
        w = told[found]
        return {"kind": "wish", "id": found, "line": w.line, "state": w.state, "origin": w.origin,
                "thread": w.thread, "says": list(dict.fromkeys(s.letter for s in w.says)),
                "results": [r.get("ref") for r in w.results],
                "opened": sorted(t for t, d in main.items() if found in opened_for(d))}
    if kind == "thread":
        name = found.removeprefix("thread:")
        return {"kind": "thread", "id": found, "status": main[name].get("status"),
                "opened_for": opened_for(main[name]), "opened_by": main[name].get("opened-by"),
                "wishes": sorted(w.id for w in told.values() if w.thread == name and w.open),
                "strands": strands_on(name)}
    if kind == "strand":
        return {"kind": "strand", "id": found, "thread": writers.get(found),
                "edges": [{"thread": e.thread, "how": e.how, "origin": e.origin}
                          for e in edges if e.strand == found]}
    letter = letters[found]
    d = letter.data

    def parent(f):
        up = f.data.get("re") or next((i for i in f.data.get("links") or () if i in letters), None)
        return str(up) if up else None

    chain, seen, up = [], {found}, parent(letter)
    while up and up not in seen and up in letters:
        seen.add(up)
        chain.append(up)
        up = parent(letters[up])
    root = next((i for i in [found, *chain] if is_person(letters[i])), None)
    strip = {}
    if root:
        sender = letters[root].data.get("from")
        own = sorted((f for f in letters.values() if is_person(f) and f.data.get("from") == sender),
                     key=fact_order)
        at = [str(f.data.get("id", f.id)) for f in own].index(root)
        strip = {"prev": str(own[at - 1].data.get("id")) if at else None,
                 "next": str(own[at + 1].data.get("id")) if at + 1 < len(own) else None}
    strand = letter.by.removeprefix("strand:") if letter.by.startswith("strand:") else None
    shown = sorted({str(f.data.get("strand")) for f in facts
                    if f.kind == "shown" and found in (f.data.get("ids") or ())})
    said = sorted(w.id for w in told.values() if any(s.letter == found for s in w.says))
    threads = [writers.get(strand), _destination(d), *(told[w].thread for w in said)]
    return {"kind": "letter", "id": found, "from": d.get("from"), "to": d.get("to"), "at": letter.at,
            "body": d.get("body", ""), "links": list(d.get("links") or ()),
            "ancestors": chain, "root": root, "strip": strip, "wishes": said,
            "threads": list(dict.fromkeys(t for t in threads if t)),
            "strands": list(dict.fromkeys(s for s in [strand, *shown] if s)),
            "answers": [i for i, f in letters.items()
                        if f.data.get("re") == found or found in (f.data.get("links") or ())]}


def render_bead(found: dict | None) -> str:
    if found is None:
        return "no such item\n"
    lines = [f"{found['kind']} {found['id']}"]
    for key, value in found.items():
        if key in {"kind", "id"} or value in (None, "", [], {}):
            continue
        if key == "body":
            value = " ".join(str(value).split())[:160]
        elif isinstance(value, list):
            value = ", ".join(map(str, value)) if all(isinstance(v, str) for v in value) else value
        lines.append(f"  {key}: {value}")
    return "\n".join(lines) + "\n"

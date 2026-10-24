"""Asks and the strand↔thread graph: read from the ledger and main, never stored."""

from __future__ import annotations

from dataclasses import dataclass

from .lifecycle import CLOSED
from .owners import unanswered_by_thread
from .project import _destination, fold, sender_threads
from .receipts import receipts
from .wishes import plan_word, waits_on, waits_word, wishes, wish_line


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
        "in hand": [f"{t} · {holder[t][0]} · plan {plan_word(told, t)}" for t, d in sorted(main.items())
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



ROOTS = ("person", "loom", "unconfirmed")


class Beads:
    """Every item's parent, read from the facts once: never stored.

    Walking up ends at a root: a person's letter, a loom fact (a notice's
    condition, an incident's event), or a derived wish nobody confirmed yet.
    A letter climbs its ``re``, else its first link; a loom letter climbs to
    the fact it was written after; a resident letter that cites nothing
    climbs to its strand, the strand to its thread, the thread to the wish
    it was opened for (or, opened before wishes, the wish or letter its
    Plan rows cite), a said wish to the letter that says it.
    """

    def __init__(self, facts, main: dict):
        from .dispositions import letter_index
        from .project import fold
        state = fold(facts)
        # A notice can live only inside a disposition: the fold reads those out.
        self.letters = {**letter_index(state.accepted), **letter_index(facts)}
        self.facts = {f.id: f for f in facts}
        self.wishes, self.writers, self.main = wishes(facts), sender_threads(facts), main
        self.leases = [f for f in state.accepted if f.kind == "lease"]

    def kind(self, ident: str) -> str:
        from .refs import kind_of
        if ident in self.letters:
            return "letter"
        if ident in self.wishes:
            return "wish"
        if ident in self.facts:
            return "fact"
        return kind_of(ident)

    def parent(self, ident: str) -> tuple[str | None, str]:
        """``(parent, edge)``, or ``(None, root kind)``, or ``(None, "dangling")``."""
        from .dispositions import is_person
        kind = self.kind(ident)
        if kind == "letter":
            f = self.letters[ident]
            d = f.data
            if d.get("re"):
                return str(d["re"]), "re"
            if d.get("links"):
                return str(d["links"][0]), "link"
            if is_person(f) or str(d.get("from", "")).startswith("stranger:"):
                return None, "person"
            if d.get("from") == "loom":
                return (f.after[0], "after") if f.after and f.after[0] in self.facts else (None, "loom")
            if f.by.startswith("strand:"):
                return f.by.removeprefix("strand:"), "molt" if d.get("molt") else "strand"
            return None, "dangling"
        if kind == "fact":
            return None, "loom" if self.facts[ident].by.startswith("loom:") else "dangling"
        if kind == "strand":
            return (f"thread:{self.writers[ident]}", "thread") if ident in self.writers else (None, "dangling")
        if kind == "thread":
            from .threads import opened_for
            name = ident.removeprefix("thread:")
            data = self.main.get(name) or {}
            wanted = opened_for(data)
            if wanted:
                return wanted[0], "opened-for"
            # Opened before wishes: its Plan rows cite the wish or letter they answer.
            from .dispositions import resolve
            cited = [raw for _, row in data.get("plan_rows") or () if " · re: " in row
                     for raw in row.rpartition(" · re: ")[2].replace(",", " ").split()]
            found = next((raw for raw in cited if raw in self.wishes), None)
            found = found or next(filter(None, (resolve(self.letters, raw) for raw in cited)), None)
            return (found, "row") if found else (None, "dangling")
        if kind == "wish":
            w = self.wishes.get(ident)
            if w is None:
                return None, "dangling"
            # A verdict's ref is the letter that gave it, when a letter did.
            ref = str((w.verdict or {}).get("ref") or "")
            if ref in self.letters:
                return ref, "verdict"
            if w.origin == "derived":
                return None, "unconfirmed"
            if w.says:
                return w.says[0].letter, "says"
            return (f"thread:{w.thread}", "asked") if w.thread else (None, "dangling")
        return None, "dangling"

    def chain(self, ident: str) -> tuple[list[tuple[str, str]], str]:
        """``ident``'s ancestors as ``(id, edge)``, nearest first, and the root kind."""
        out, seen, at = [], {ident}, ident
        while True:
            up, edge = self.parent(at)
            if up is None:
                return out, edge
            if up in seen:
                return out, "dangling"
            seen.add(up)
            out.append((up, edge))
            if up not in self.letters and up not in self.facts and self.kind(up) == "letter":
                return out, "dangling"
            at = up

    def successor(self, strand: str) -> str | None:
        """The strand that took over ``strand``'s thread after it molted."""
        thread = self.writers.get(strand)
        held = [str(f.data["strand"]) for f in self.leases if f.data.get("thread") == thread]
        after = held[held.index(strand) + 1:] if strand in held else []
        return next((s for s in after if s != strand), None)


def bead(facts, main: dict, ident: str) -> dict | None:
    """One item, the chain up to its root, and what hangs from it."""
    from .dispositions import is_person
    from .ledger import fact_order
    from .refs import Index
    from .threads import opened_for
    beads = Beads(facts, main)
    found = Index(facts, main).resolve(ident.removeprefix("&"))
    if found is None and ident.removeprefix("&") in beads.letters:
        found = ident.removeprefix("&")
    if found is None:
        return None
    told, writers, edges, letters = beads.wishes, beads.writers, graph(facts), beads.letters
    kind = beads.kind(found)
    chain, root_kind = beads.chain(found)
    up = {"ancestors": [{"id": i, "kind": beads.kind(i), "edge": e} for i, e in chain],
          "root": chain[-1][0] if chain else found, "root_kind": root_kind}

    def strands_on(thread):
        return sorted({e.strand for e in edges if e.thread == thread})

    if kind == "wish":
        w = told[found]
        return {"kind": "wish", "id": found, "line": w.line, "state": w.state, "origin": w.origin,
                "thread": w.thread, "says": list(dict.fromkeys(s.letter for s in w.says)),
                "results": [r.get("ref") for r in w.results],
                "opened": sorted(t for t, d in main.items() if found in opened_for(d)), **up}
    if kind == "thread":
        name = found.removeprefix("thread:")
        return {"kind": "thread", "id": found, "status": main[name].get("status"),
                "opened_for": opened_for(main[name]), "opened_by": main[name].get("opened-by"),
                "wishes": sorted(w.id for w in told.values() if w.thread == name and w.open),
                "strands": strands_on(name), **up}
    if kind == "strand":
        return {"kind": "strand", "id": found, "thread": writers.get(found),
                "successor": beads.successor(found),
                "edges": [{"thread": e.thread, "how": e.how, "origin": e.origin}
                          for e in edges if e.strand == found], **up}
    letter = letters[found]
    d = letter.data
    said = next((i for i in [found, *(i for i, _ in chain)] if i in letters and is_person(letters[i])), None)
    strip = {}
    if said:
        sender = letters[said].data.get("from")
        own = sorted((f for f in letters.values() if is_person(f) and f.data.get("from") == sender),
                     key=fact_order)
        at = [str(f.data.get("id", f.id)) for f in own].index(said)
        strip = {"prev": str(own[at - 1].data.get("id")) if at else None,
                 "next": str(own[at + 1].data.get("id")) if at + 1 < len(own) else None}
    strand = letter.by.removeprefix("strand:") if letter.by.startswith("strand:") else None
    molt = {"from": strand, "to": beads.successor(strand)} if d.get("molt") and strand else None
    shown = sorted({str(f.data.get("strand")) for f in facts
                    if f.kind == "shown" and found in (f.data.get("ids") or ())})
    wished = sorted(w.id for w in told.values() if any(s.letter == found for s in w.says))
    threads = [writers.get(strand), _destination(d), *(told[w].thread for w in wished)]
    return {"kind": "letter", "id": found, "from": d.get("from"), "to": d.get("to"), "at": letter.at,
            "body": d.get("body", ""), "links": list(d.get("links") or ()), **up,
            "said": said, "strip": strip, "molt": molt, "wishes": wished,
            "threads": list(dict.fromkeys(t for t in threads if t)),
            "strands": list(dict.fromkeys(s for s in [strand, *shown] if s)),
            "answers": [i for i, f in letters.items()
                        if f.data.get("re") == found or found in (f.data.get("links") or ())]}


def roots(facts, main: dict) -> dict[str, int]:
    """How many letters' chains end at each root kind: the provenance count."""
    from collections import Counter
    beads = Beads(facts, main)
    return dict(Counter(beads.chain(i)[1] for i in beads.letters))

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

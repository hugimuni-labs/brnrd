"""The face a body chose for its thread: one of brr.emotes' twelve words.

A face belongs to the body that set it (strand and gen). The thread shows
it until its next lease: a successor did not choose its predecessor's face,
so it starts with none. Write-only: nothing here grades a face, and no face
set is no face drawn.
"""

from __future__ import annotations

from brr import emotes

from .ledger import kind_facts
from .project import fold


def word_of(raw: str) -> str:
    """The vocabulary word ``raw`` names, or a refusal that lists the twelve.

    A synonym, a legacy handle or a small typo resolves through
    ``emotes.lookup``; a telemetry face is the loom's, never a body's.
    """
    found = emotes.lookup(raw)
    if found is None or found.kind != "situational":
        near = [e.name for e in emotes.near_misses(raw)] if found is None else []
        raise ValueError(f"face: {raw!r} is not a face"
                         + (f" (near: {', '.join(near)})" if near else "")
                         + "; one of: " + " ".join(emotes.VOCABULARY))
    return found.family or found.name


def faces(facts) -> dict[str, dict]:
    """Thread → ``{word, strand, gen, at}`` of its latest lease's body, if it set one."""
    current = {}
    for fact in fold(facts).accepted:
        if fact.kind == "lease":
            current[str(fact.data["thread"])] = (str(fact.data["strand"]), int(fact.data["gen"]))
    body = {lease: thread for thread, lease in current.items()}
    found: dict[str, dict] = {}
    for fact in kind_facts(facts, "face"):
        thread = body.get((str(fact.data.get("strand")), fact.data.get("gen")))
        if thread is None:
            continue
        if fact.data.get("word"):
            found[thread] = {"word": fact.data["word"], "strand": fact.data["strand"],
                             "gen": fact.data["gen"], "at": fact.at}
        else:
            found.pop(thread, None)
    return found

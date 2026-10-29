"""The boundary rendered with nothing carried: the reference a cached render must equal byte for byte.

Shared by tests/loom/test_boundary_cache.py and bench/loom_home_idle.py --differential.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

from brr.loom.runtime import faces, loom, threads
from brr.loom.runtime.ledger import LedgerView, kind_facts


def uncached_boundary(home, facts, strand, gen, context) -> str:
    """Every named projection rebuilt, the whole thread table, the old face scan."""
    full = threads.thread_table
    saved = facts.cache if isinstance(facts, LedgerView) else None
    if saved is not None:
        facts.cache = {}
    try:
        with patch.object(threads, 'thread_table', lambda home, facts, opened_by=None: full(home, facts)), \
                patch.object(faces, 'propose', scanned_propose):
            return loom.boundary_text(home, facts, strand, gen, context)
    finally:
        if saved is not None:
            facts.cache = saved


def scanned_propose(facts, strand, gen, thread, now=None):
    """``faces.propose`` as it was: one walk over every face, letter and note."""
    trigger = faces._standing(facts, strand, gen, thread, now)
    if trigger is None:
        return None
    for fact in kind_facts(facts, "face", "letter", "note"):
        if fact.kind == "face":
            mine = (fact.data.get("strand"), fact.data.get("gen")) == (strand, gen)
        else:
            mine = fact.data.get("from") == strand or fact.by == f"strand:{strand}"
        if mine and faces._at(fact) >= trigger["at"]:
            return None
    return {"face": faces.PROPOSED[trigger["kind"]], "token": trigger["token"], "fact": trigger["fact"]}


@contextmanager
def checked_boundaries(seen: list):
    """Wrap ``loom._boundary``: each production render is compared with the reference.

    Appends ``(strand, gen, equal, written, reference)`` to ``seen``.
    """
    real = loom._boundary

    def checked(home, facts, strand, gen, context):
        real(home, facts, strand, gen, context)
        written = (home.room(strand) / 'port/in/boundary.md').read_text()
        reference = uncached_boundary(home, facts, strand, gen, context)
        seen.append((strand, gen, written == reference, written, reference))

    with patch.object(loom, '_boundary', checked):
        yield

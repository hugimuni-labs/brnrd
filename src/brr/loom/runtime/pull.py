"""Asks are a pull (requests R4): every open ask is live, moved or stale, said
at the boundary in one ``asks:`` band, and a person's rank word owes a ``top?``.

All derived at every render. The band is news only when it changes; a person
letter carries its thread's asks under its ``steers?`` prompt; the stop holds
once on a rank word no ``wish --top`` answered (design.md §4, §5 of
threads/requests in the self).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from .raises import last_raise, move, prs

RANK = re.compile(r"\b(first|before|most important|priority|top)\b", re.I)
BAND = 5
# The kinds that move or stale an ask; the band reads no others.
MOVES = frozenset({"letter", "wish", "pr.opened", "pr.merged"})


def flavour(facts, ask, found: dict, room: Path | None, closed=frozenset()) -> tuple[str, str]:
    """``(live|moved|stale, why)``: stale when its premise is gone, moved when
    something happened since it was last raised, else live."""
    parent = found.get(ask.parent)
    if parent is not None and not parent.open:
        return "stale", f"{parent.id} {parent.state}"
    if ask.thread in closed:
        return "stale", f"{ask.thread} settled"
    merged = sorted(str(f.data.get("number")) for f in facts
                    if f.kind == "pr.merged" and str(f.data.get("number")) in prs(ask))
    if merged:
        return "stale", f"#{merged[0]} merged"
    since = (last_raise(facts, room, ask.id) if room is not None else None) \
        or datetime.fromisoformat(ask.born or ask.at)
    why = move(facts, ask, since)
    return ("moved", why) if why else ("live", "")


def thread_asks(found: dict, thread: str, main: str) -> list:
    """The open asks a boundary shows: its thread's, or every thread's at the home thread."""
    return sorted((w for w in found.values() if w.sender == "resident" and w.open
                   and (thread == main or w.thread == thread)), key=lambda w: w.born or w.at)


def _age(at: str, now: datetime) -> str:
    seconds = (now - datetime.fromisoformat(at)).total_seconds()
    return f"{int(seconds // 86400)}d" if seconds >= 86400 else f"{int(seconds // 3600)}h"


def band_line(facts, found: dict, thread: str, main: str, room: Path | None = None,
              closed=frozenset(), now: datetime | None = None) -> str:
    """``asks:`` with each open ask, stale and moved first. Ages end each item, so
    the jack's ageless compare says it again only when a flavour changes."""
    from .wishes import _short
    now = now or datetime.now(timezone.utc)
    order = {"stale": 0, "moved": 1, "live": 2}
    asks = thread_asks(found, thread, main)
    if not asks:
        return ""
    # One pass over the ledger per render, not one per ask (loom-cpu).
    facts = [f for f in facts if f.kind in MOVES]
    rows = [(ask, *flavour(facts, ask, found, room, closed)) for ask in asks]
    rows.sort(key=lambda row: order[row[1]])
    items = []
    for ask, kind, why in rows[:BAND]:
        where = f" on {ask.thread}" if thread == main else ""
        what = f" ({why})" if why else f" blocks {ask.parent}" if ask.parent else ""
        items.append(f'{ask.id} {kind}{what}{where} "{_short(ask.line, 50)}" {_age(ask.born or ask.at, now)}')
    more = f" · +{len(rows) - BAND} more: brnrd runtime asks" if len(rows) > BAND else ""
    return "asks: " + " · ".join(items) + more


def owed_tops(facts, thread: str, strand: str) -> list[tuple[str, str]]:
    """``(letter, word)`` for each person letter to ``thread`` since this body's
    lease that says a rank word, cited by no ``wish.top`` and not yet asked at a stop."""
    from .ledger import kind_facts
    from .wishes import _letter_word, _person
    since = max((f.at for f in kind_facts(facts, "lease") if f.data.get("strand") == strand), default="")
    topped = {str(f.data.get("ref")) for f in kind_facts(facts, "wish.top")}
    asked = {ident for f in kind_facts(facts, "left.asked") if f.data.get("strand") == strand
             for ident in f.data.get("letters") or ()}
    out = []
    for f in kind_facts(facts, "letter"):
        ident = str(f.data.get("id", f.id))
        if f.at < since or f.data.get("to") != f"thread:{thread}" or not _person(f) \
                or ident in topped or _letter_word(ident) in asked:
            continue
        word = RANK.search(str(f.data.get("body", "")))
        if word:
            out.append((_letter_word(ident), word.group(1).lower()))
    return out


def top_line(owed: list[tuple[str, str]]) -> str:
    """The stop line a rank word owes; a stop again passes it as no change."""
    if not owed:
        return ""
    named = " · ".join(f'{ident} says "{word}"' for ident, word in owed[:3])
    return f"top? {named} — wish <w-id> --top <letter> | stop again: no change"


def done_line(found: dict, thread: str, main: str) -> str:
    """A thread whose every request is shut is done; settle stays its own act."""
    rows = [w for w in found.values() if w.thread == thread]
    if thread == main or not rows or any(w.open for w in rows):
        return ""
    return f'done: no open request on {thread} — settle {thread} --why "<what it became>" opens the topic round'

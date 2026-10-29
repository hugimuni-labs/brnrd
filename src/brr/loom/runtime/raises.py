"""A question to a person is an ask, and an unchanged ask is raised once a day (requests R3b).

``send`` to a channel calls both. ``capture`` turns a letter with a question
in it into an ask under the wish it unblocks, so the letter is the ask's first
raise; ``refuse`` stops a raise of an ask that nothing moved since it was last
raised less than ``RAISE_GAP`` ago. Beyond that rule the body judges when to
raise (design.md §4, §5 of threads/requests in the self).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from .home import is_channel

RAISE_GAP = timedelta(hours=24)


class RaiseError(ValueError):
    pass


def questions(body: str) -> list[str]:
    """Lines ending in ``?``, skipping quoted (``>``) and code lines."""
    found, fenced = [], False
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            fenced = not fenced
            continue
        if fenced or raw.startswith(("    ", "\t")) or line.startswith(">"):
            continue
        if line.endswith("?"):
            found.append(line)
    return found


def _links(data: dict) -> list[str]:
    value = data.get("links") or ()
    return value.split() if isinstance(value, str) else [str(v) for v in value]


def _asks(found: dict, links) -> list:
    return [found[i] for i in links if i in found and found[i].open and found[i].sender == "resident"]


def capture(facts, *, by: str, thread: str | None, main: str, body: str, links, ask_for: str | None):
    """The ask fact a question to a person makes, or None when it asks nothing new.

    A letter that already links an open ask is that ask's raise. The parent is
    ``ask_for``, else the thread's only open person wish; the home thread
    relays for every thread, so it always names ``--for``.
    """
    from .verbs_wishes import wish_fact
    from .wishes import steer_candidates, wishes
    asked = questions(body)
    if not asked or thread is None:
        return None
    found = wishes(facts)
    if _asks(found, links):
        return None
    parent = ask_for
    if parent is None:
        rows = [] if thread == main else steer_candidates(found, thread)
        if len(rows) != 1:
            named = " | ".join(f"--for {w.id}" for w in rows[:5])
            raise RaiseError(
                "send: a question to a person is an ask under the wish it unblocks: "
                + (f"name one: {named}" if rows else "name it with --for <w-id>")
                + ", or --chatter if it asks nothing")
        parent = rows[0].id
    line = " ".join(asked)
    return wish_fact(facts, by=by, ident="new", line=line[:160], thread=thread,
                     sender="resident", parent=parent)


def last_raise(facts, room: Path, ask: str) -> datetime | None:
    """When a letter to a channel last linked ``ask``: the ledger, or this room's unsent port."""
    from .ledger import kind_facts
    from .port import PortError, parse_frontmatter
    times = [datetime.fromisoformat(f.at) for f in kind_facts(facts, "letter")
             if is_channel(str(f.data.get("to", ""))) and ask in _links(f.data)]
    out = Path(room) / "port" / "out"
    for path in out.glob("*.md") if out.is_dir() else ():
        try:
            sent = parse_frontmatter(path.read_text())
        except PortError:
            continue
        if is_channel(str(sent.get("to", ""))) and ask in _links(sent):
            times.append(datetime.fromtimestamp(path.stat().st_mtime, timezone.utc))
    return max(times, default=None)


def moved(facts, wish, since: datetime) -> bool:
    """A person wrote, or the ask or its parent was restated (rec, line, steer), after ``since``.

    R4's band adds the PR facts; a body's own read or left never counts.
    """
    from .wishes import _person
    for f in facts:
        if datetime.fromisoformat(f.at) <= since:
            continue
        if f.kind == "letter" and _person(f):
            return True
        if f.kind == "wish" and f.data.get("wish") in (wish.id, wish.parent):
            return True
    return False


def refuse(facts, room: Path, links, now: datetime | None = None) -> None:
    """Raise ``RaiseError`` when a linked ask is unchanged and was raised under a day ago."""
    from .wishes import wishes
    now = now or datetime.now(timezone.utc)
    for ask in _asks(wishes(facts), links):
        last = last_raise(facts, room, ask.id)
        if last is not None and now - last < RAISE_GAP and not moved(facts, ask, last):
            again = (last + RAISE_GAP).strftime("%Y-%m-%d %H:%MZ")
            raise RaiseError(
                f"send: {ask.id} was raised at {last.strftime('%Y-%m-%d %H:%MZ')} and nothing moved since; "
                f"raise it again after {again}, or when it moves (a letter of theirs, a new rec), "
                "or drop the &ref")

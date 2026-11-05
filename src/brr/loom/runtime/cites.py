"""Cites: the stop lists this gen's irreversible acts that cite nothing (threads/trust-guards C2).

A never guard's act carries its receipt at the act: it runs only on a grant,
and ``guard.granted`` is written then. ``settle`` and ``reincarnate`` carry
theirs as ``--cites <letter>`` on their fact; they end the gen, so no stop
after them could list them. What a stop can list is a ``send`` to a channel:
a letter out of the thread's world, said to someone the thread was not told
to talk to.

A channel send cites when it carries ``re``, or when its channel is one an
owner's letter came from (the owner's word is what makes a thread exist), or
one a letter on this thread's wishes came from. Anything else is listed until
the README's ``## Misses`` section names its key (``send:<channel>@<letter>``).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .home import is_channel


def _since(booted: float) -> str:
    return datetime.fromtimestamp(booted, timezone.utc).isoformat() if booted else ""


def check_cite(facts, letter: str, thread: str) -> None:
    """``--cites <letter>``: the letter exists and is on ``thread``, sent to it or stamped on one of its
    wishes. Who wrote it is not checked: settle and reincarnate are the body's to call."""
    from .port import PortError
    from .project import _destination
    from .wishes import wishes
    facts = list(facts)
    found = next((f for f in facts if f.kind == "letter" and str(f.data.get("id", f.id)) == letter), None)
    if found is None:
        raise PortError(f"--cites {letter}: no such letter")
    if _destination(found.data) == thread:
        return
    on = {w.id for w in wishes(facts).values() if w.thread == thread}
    if found.data.get("wish") in on or any(letter in {s.letter for s in w.says}
                                            for w in wishes(facts).values() if w.id in on):
        return
    raise PortError(f"--cites {letter}: not a letter on thread {thread}")


def channel_sends(room: Path, strand: str, booted: float) -> list[dict]:
    """This strand's letters to a channel since ``booted``, without ``re``: drained ones from its
    own ledger file, then any still in port/out. Only the strand's own file, never the whole ledger."""
    from .home import home_of_room
    from .port import PortError, parse_frontmatter
    since = _since(booted)
    found: dict[str, dict] = {}
    try:
        root = home_of_room(room).root / "ledger" / "facts"
    except ValueError:
        root = None
    for path in (root.glob(f"*/{strand}.jsonl") if root else ()):
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            if '"channel:' not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            data = row.get("data") or {}
            if (row.get("kind") == "letter" and row.get("by") == f"strand:{strand}" and is_channel(data.get("to"))
                    and not data.get("re") and str(row.get("at") or "") >= since):
                found[str(data.get("id") or row.get("id"))] = {"id": str(data.get("id") or row.get("id")),
                                                               "to": data["to"]}
    for path in sorted((room / "port" / "out").glob("*.md")):
        try:
            fields = parse_frontmatter(path.read_text())
        except (PortError, OSError):
            continue
        if fields.get("kind") == "letter" and is_channel(fields.get("to")) and not fields.get("re"):
            ident = str(fields.get("id") or path.stem)
            found.setdefault(ident, {"id": ident, "to": fields["to"]})
    return list(found.values())


def granted_channels(facts, thread: str, owner: set[str], home=None) -> set[str]:
    """Channels an owner's letter came from, and channels a letter on ``thread``'s wishes came from."""
    from .project import reply_channels
    from .wishes import wishes
    facts = list(facts)
    chats = reply_channels(facts, home)
    letters = {str(f.data.get("id", f.id)): f for f in facts if f.kind == "letter"}
    keep = {ident for ident, f in letters.items() if str(f.data.get("from") or f.by) in owner or f.by in owner}
    for wish in wishes(facts).values():
        if wish.thread == thread:
            keep |= {say.letter for say in wish.says}
            keep |= {ident for ident, f in letters.items() if f.data.get("wish") == wish.id}
    return {chats[ident] for ident in keep if chats.get(ident) and chats[ident] != "-"}


def _within(channel: str, allowed: set[str]) -> bool:
    """A chat, or a topic inside it (``<chat>:<topic>``); a chat's id is never cut, since
    connector ids carry colons of their own (``channel:cloud/telegram:155783668``)."""
    return any(channel == chat or channel.startswith(chat + ":") for chat in allowed)


def missed(readme: Path) -> str:
    """The README's ``## Misses`` section, or nothing."""
    try:
        return missed_text(readme.read_text())
    except OSError:
        return ""


def missed_text(text: str) -> str:
    found = re.search(r"^## Misses\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    return found.group(1) if found else ""


def uncited(room: Path, thread: str, strand: str, booted: float) -> list[str]:
    """Keys of this gen's channel sends that cite nothing and no ``## Misses`` line names."""
    sends = channel_sends(room, strand, booted)
    if not sends:
        return []
    from .grants import owners
    from .home import home_of_room
    from .ledger import read_facts
    home = home_of_room(room)
    allowed = granted_channels(read_facts(home), thread, owners(home.root / "self"), home)
    misses = missed(room / "self" / "threads" / thread / "README.md")
    keys = [f"send:{send['to']}@{send['id']}" for send in sends if not _within(send["to"], allowed)]
    return [key for key in keys if key not in misses]


SECTION = """irreversible acts this gen made that cite nothing (a letter out to a chat no letter on this thread came from):
{keys}
Each is written up as a miss: a line naming its key under `## Misses` in threads/{thread}/README.md (what was sent, and why no letter asked for it)."""


def section(room: Path) -> str:
    """The stop's receipts section, or nothing. A check that cannot run never costs the stop."""
    import os
    from .jack import _state
    thread = os.environ.get("BRNRD_THREAD") or ""
    if not thread:
        return ""
    try:
        keys = uncited(room, thread, room.name, float(_state(room).get("booted_at") or 0))
    except Exception:
        return ""
    return SECTION.format(keys="\n".join(f"- {key}" for key in keys), thread=thread) if keys else ""

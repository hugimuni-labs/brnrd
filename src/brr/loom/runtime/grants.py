"""Grants: the owner's sentence an act that cannot be undone points at (threads/judged-acts).

A call marked ``final`` (mark.py) uses a dashboard confirmation or its
``for`` names a letter of the owner's: its id, or the number it ends with. A letter a
body held (``send --hold``: out of the person's pattern, or not them) grants
nothing: the café's ask waits for the second path, and the owner's answer
there is the grant. Only the owner grants: Sasha's or Nastya's letter does
not, by design. The body's own word never grants: a strand's letter carries
``from: <strand>``, written by the port, never ``person:``. A standing
sentence ("merge green ones yourself") is a letter like any other: the mark
cites the letter it was said in.

The owner is a person with ``people/<name>/agreement.md`` on the self's main,
read from git, so a room cannot enrol one. A final act that ran is written as
``guard.granted`` with the letter it leaned on, one that did not as
``guard.refused``: ``trust`` counts both. One act is one fact: the hook and the
door both judge the same call, and the ``stand-in``'s check and its door both
judge the same gh act, so :func:`note` writes a judgement only when the same
strand has not written the same one (class, target, letter) in the last
:data:`SAME_ACT` seconds. The hook writes only once nothing else refuses the call.

A bare number is the end of a letter id only for a letter of the last
:data:`RECENT_DAYS` days: his Telegram letters ran through the numbers pull
requests carry now, and "for 2771" meaning the PR must not lean on his letter
2771 of last spring. An older or standing letter is cited by its full id.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from brr.daemon2.facts import Fact


RECENT_DAYS = 30
SAME_ACT = 600


@dataclass(frozen=True)
class Grant:
    letter: str
    wish: str
    person: str
    at: str = ""
    words: str = ""

    def said(self) -> str:
        """The letter as the body sees it at the moment it leans on it: its date and first words."""
        when = _when(self.at)
        stamp = when.strftime("%Y-%m-%d %H:%MZ") if when else "an unknown date"
        words = " ".join(self.words.split())
        words = words if len(words) <= 60 else words[:59].rstrip() + "…"
        return f"{self.letter} ({stamp}: \"{words}\")" if words else f"{self.letter} ({stamp})"


@dataclass(frozen=True)
class Judged:
    """One final act's verdict, not yet written (:func:`note` writes it)."""
    granted: bool
    line: str
    what: str
    grant: Grant | None = None
    confirm_id: str = ""
    call: dict | None = None


def _when(at: str) -> datetime | None:
    try:
        when = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


_OWNERS: dict[Path, tuple[str, frozenset[str]]] = {}


def owners(self_repo: Path) -> set[str]:
    """``person:<name>`` for each ``people/<name>/agreement.md`` on the self's main."""
    from .lifecycle import main_tip
    from .guards import _git
    self_repo = Path(self_repo)
    if not (self_repo / ".git").exists():
        return set()
    tip = main_tip(self_repo)
    prior = _OWNERS.get(self_repo)
    if prior is not None and prior[0] == tip:
        return set(prior[1])
    listing = _git(self_repo, "ls-tree", "-r", "--name-only", tip, "people/")
    found = frozenset(f"person:{path.split('/')[1]}" for path in listing.stdout.splitlines()
                      if re.fullmatch(r"people/[^/]+/agreement\.md", path))
    if listing.returncode == 0:
        _OWNERS[self_repo] = (tip, found)
    return set(found)


def letter_for(facts: Iterable[Fact], cited: str, owner: set[str], now: datetime | None = None) -> Grant | None:
    """The newest owner letter ``cited`` names (a full id, or the number an id of the last
    :data:`RECENT_DAYS` days ends with), unheld."""
    from .dispositions import held
    facts = list(facts)
    since = (now or datetime.now(timezone.utc)) - timedelta(days=RECENT_DAYS)
    words = {w.strip(".,;()[]") for w in re.split(r"[\s,]+", cited or "") if w.strip(".,;()[]")}
    if not words:
        return None
    waiting = held(facts)
    stamped = {}
    for f in facts:
        if f.kind == "wish":
            for say in f.data.get("says") or ():
                if isinstance(say, dict) and say.get("letter"):
                    stamped.setdefault(str(say["letter"]), str(f.data.get("wish") or ""))
    best: tuple[str, Grant] | None = None
    for f in facts:
        if f.kind != "letter":
            continue
        ident = str(f.data.get("id", f.id))
        tail = re.split(r"[:/]", ident)[-1]
        if ident not in words:
            if not (tail.isdigit() and tail in words):
                continue
            when = _when(f.at)
            if when is None or when < since:
                continue
        person = str(f.data.get("from") or "")
        if person not in owner or ident in waiting:
            continue
        if best is None or f.at > best[0]:
            best = (f.at, Grant(ident, stamped.get(ident, str(f.data.get("wish") or "")), person,
                                f.at, str(f.data.get("body") or "")))
    return best[1] if best else None


def record(home, strand: str, gen: int | None, thread: str, cls: str, target: str, grant: Grant) -> Fact:
    from .ledger import append
    from .home import mint
    return append(home, Fact(kind="guard.granted", by=f"strand:{strand}", id="guard.granted:" + mint(12),
                             data={"strand": strand, "gen": gen, "thread": thread, "class": cls, "target": target,
                                   "letter": grant.letter, "wish": grant.wish}))


def refused(home, strand: str, gen: int | None, thread: str, cls: str, target: str) -> Fact:
    """A final act stopped for want of his sentence: the other side of ``guard.granted``, counted by ``trust``."""
    from .ledger import append
    from .home import mint
    return append(home, Fact(kind="guard.refused", by=f"strand:{strand}", id="guard.refused:" + mint(12),
                             data={"strand": strand, "gen": gen, "thread": thread, "class": cls, "target": target}))


def judge(room: Path, what: str, cited: str, *, call=None) -> Judged:
    """The verdict for one act marked final in ``room``; nothing is written."""
    from .home import home_of_room
    from .ledger import read_facts
    import os
    room = Path(room)
    home = home_of_room(room)
    thread = os.environ.get("BRNRD_THREAD", "")
    if not cited or cited == "confirm":
        from . import confirm
        if not thread or call is None:
            return Judged(False, "A dashboard confirmation needs the thread and the exact call.", what)
        from .wishes import told
        candidates = [w for w in told(read_facts(home)).values() if w.thread == thread]
        wish = max(candidates, key=lambda w: (w.ranked_at, w.at), default=None)
        wish_id = wish.id if wish else ""
        ident = confirm.ask(home, thread, what, wish_id, call, why=wish.line if wish else "")
        return Judged(False, f'Waiting for his press {ident}: brnrd runtime wish {wish_id or "<w-id>"} '
                      f'--read "waiting: his confirm {ident}" --wait confirm:{ident}, '
                      f'then run the same call with "· confirm:{ident}".', what)
    if cited.startswith("confirm:"):
        from . import confirm
        ident = cited.removeprefix("confirm:")
        facts = read_facts(home)
        row = confirm.entry(facts, ident)
        if row is not None and call is not None:
            req = row["request"]
            if (req["thread"] == thread and req["what"] == what
                    and req["digest"] == confirm.digest(home.install_id(), thread, what, call)
                    and confirm.state(home, facts, req, confirm.now())[0] == "given"):
                return Judged(True, f"granted: {what} by confirmation {ident}", what,
                              Grant(ident, req["for"], ""), ident, call)
        return Judged(False, f"Confirmation {ident} is unavailable for this call.", what)
    owner = owners(home.root / "self")
    facts = read_facts(home)
    grant = letter_for(facts, cited, owner)
    who = " or ".join(sorted(o.partition(":")[2] for o in owner)) or "the owner"
    if grant is None:
        return Judged(False, (f"refused: {what} cannot be undone, and {cited or 'its mark'} names no letter of {who}'s "
                              f"(a bare number names only a letter of the last {RECENT_DAYS} days; an older one, "
                              f"its full id). Ask: `brnrd runtime ask \"{what}?\" --for <w-id> --rec \"yes\"`; "
                              f"his answer is the letter the mark then cites."), what)
    return Judged(True, f"granted: {what} (final) by {grant.said()}", what, grant)


def _written(facts: list, kind: str, strand: str, target: str, letter: str | None, now: datetime) -> bool:
    for f in reversed(facts):
        if f.kind != kind or f.data.get("strand") != strand or f.data.get("class") != "final":
            continue
        if f.data.get("target") != target or (letter is not None and f.data.get("letter") != letter):
            continue
        when = _when(f.at)
        if when is not None and (now - when).total_seconds() <= SAME_ACT:
            return True
    return False


def note(room: Path, judged: Judged, *, door=False) -> Fact | None:
    """Write one final act's verdict, once per act (:data:`SAME_ACT`); None when already written."""
    import os
    from .home import home_of_room, strand_of_room
    from .ledger import read_facts
    from .publish import _gen
    room = Path(room)
    home = home_of_room(room)
    thread = os.environ.get("BRNRD_THREAD", "")
    strand = strand_of_room(room)
    if judged.confirm_id and judged.grant is not None:
        from . import confirm
        with confirm.locked(home):
            fresh = judge(room, judged.what, "confirm:" + judged.confirm_id, call=judged.call)
            if not fresh.granted:
                raise ValueError(fresh.line)
            facts = read_facts(home)
            gen = _gen(facts, strand)
            confirm.put(home, "used", judged.confirm_id, {"id": judged.confirm_id, "strand": strand,
                        "thread": thread, "gen": gen, "stage": "door" if door else "hook"})
            return record(home, strand, gen, thread, "final", judged.what, judged.grant)
    facts = read_facts(home)
    kind = "guard.granted" if judged.granted else "guard.refused"
    letter = judged.grant.letter if judged.grant else None
    if _written(facts, kind, strand, judged.what, letter, datetime.now(timezone.utc)):
        return None
    gen = _gen(facts, strand)
    if judged.grant is None:
        return refused(home, strand, gen, thread, "final", judged.what)
    return record(home, strand, gen, thread, "final", judged.what, judged.grant)


def final(room: Path, what: str, cited: str) -> tuple[bool, str]:
    """``(granted, line)`` for one act marked final at a door that does it; the verdict is written once."""
    judged = judge(room, what, cited)
    note(room, judged)
    return judged.granted, judged.line

"""Grants: the owner's sentence an act that cannot be undone points at (threads/judged-acts).

The one rule: a call marked ``final`` (mark.py) runs only when its ``for``
names a letter of the owner's: its id, or the number it ends with. A letter a
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
``guard.refused``: ``trust`` counts both.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from brr.daemon2.facts import Fact


@dataclass(frozen=True)
class Grant:
    letter: str
    wish: str
    person: str


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


def names(text: str, target: str) -> bool:
    """``target`` stands in ``text`` as a word: ``#2610`` and ``2610`` name PR 2610,
    ``main`` does not stand in ``maintain``, a path is not the start of a longer one."""
    target = target.strip()
    if not re.search(r"\w", target):
        # "?" or "-": no letter names nothing, however it is punctuated.
        return False
    forms = {target, target.lstrip("#")} if target.startswith("#") else {target}
    # People write a release without its v: "Publish 0.7.0" names the tag v0.7.0.
    if re.fullmatch(r"v\d+(?:\.\d+)+", target):
        forms.add(target[1:])
    # A sentence's full stop ends a word; a dot before more of a name does not.
    return any(re.search(rf"(?<![\w/.-]){'#?' if f.isdigit() else ''}{re.escape(f)}(?![\w/-]|\.[\w/])", text)
               for f in forms)


def letter_for(facts: Iterable[Fact], cited: str, owner: set[str]) -> Grant | None:
    """The newest owner letter ``cited`` names (a full id, or the number an id ends with), unheld."""
    from .dispositions import held
    facts = list(facts)
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
        if ident not in words and not (tail.isdigit() and tail in words):
            continue
        person = str(f.data.get("from") or "")
        if person not in owner or ident in waiting:
            continue
        if best is None or f.at > best[0]:
            best = (f.at, Grant(ident, stamped.get(ident, str(f.data.get("wish") or "")), person))
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


def final(room: Path, what: str, cited: str) -> tuple[bool, str]:
    """``(granted, line)`` for one act marked final in ``room``; the grant or the refusal is recorded."""
    import os
    from .home import home_of_room, strand_of_room
    from .ledger import read_facts
    room = Path(room)
    home = home_of_room(room)
    thread = os.environ.get("BRNRD_THREAD", "")
    owner = owners(home.root / "self")
    facts = read_facts(home)
    grant = letter_for(facts, cited, owner)
    from .publish import _gen
    strand = strand_of_room(room)
    gen = _gen(facts, strand)
    who = " or ".join(sorted(o.partition(":")[2] for o in owner)) or "the owner"
    if grant is None:
        refused(home, strand, gen, thread, "final", what)
        return False, (f"refused: {what} cannot be undone, and {cited or 'its mark'} names no letter of {who}'s. "
                       f"Ask: `brnrd runtime ask \"{what}?\" --for <w-id> --rec \"yes\"`; "
                       f"his answer is the letter the mark then cites.")
    record(home, strand, gen, thread, "final", what, grant)
    return True, f"granted: {what} (final) by {grant.letter}"

"""Grants: the owner's letter that lets a never guard's act through (threads/trust-guards).

A never guard refuses an irreversible act (a merge without green, a delete
under the home, a release) unless the owner said it. The owner says it in a
letter stamped on an open wish of the thread the act serves, and the letter
names the act's target literally (the PR, the path, the tag) and one of
the class's verbs, so "merge to main when green" grants no force-push to
main. A letter a body held (``send --hold``: out of the person's pattern, or
not them) grants nothing: the café's ask waits for the second path, and the
owner's answer there is the grant. Only the owner grants: Sasha's or Nastya's letter does not, by design
(Part D's provenance answers who else may say what). The body's own
word never grants: a strand's letter carries ``from: <strand>``, written by
the port, never ``person:``.

The owner is a person with ``people/<name>/agreement.md`` on the self's main,
read from git like the guards, so a room cannot enrol one. A grant that lets
an act through is written as ``guard.granted``: the act's receipt, which the
stop's receipt check reads.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from brr.daemon2.facts import Fact

from .wishes import wishes

# The classes a refusal can name, in the words the refusal says.
CLASSES = {
    "force-push": "force-push to a branch not this strand's own",
    "delete-home": "delete under a home root",
    "merge-red": "merge without green",
    "publish": "publish",
}

# A letter grants a class only when it says one of these too: the target alone is a common word.
VERBS = {
    "force-push": r"force|push\s+-f",
    "delete-home": r"delet|remov|\brm\b|wipe|clean",
    "merge-red": r"merg",
    "publish": r"releas|publish|upload|post|tag",
}


@dataclass(frozen=True)
class Grant:
    letter: str
    wish: str
    person: str


def owners(self_repo: Path) -> set[str]:
    """``person:<name>`` for each ``people/<name>/agreement.md`` on the self's main."""
    from .guards import _git
    from .lifecycle import main_tip
    self_repo = Path(self_repo)
    if not (self_repo / ".git").exists():
        return set()
    listing = _git(self_repo, "ls-tree", "-r", "--name-only", main_tip(self_repo), "people/")
    return {f"person:{path.split('/')[1]}" for path in listing.stdout.splitlines()
            if re.fullmatch(r"people/[^/]+/agreement\.md", path)}


def names(text: str, target: str) -> bool:
    """``target`` stands in ``text`` as a word: ``#2610`` and ``2610`` name PR 2610,
    ``main`` does not stand in ``maintain``, a path is not the start of a longer one."""
    target = target.strip()
    if not target:
        return False
    forms = {target, target.lstrip("#")} if target.startswith("#") else {target}
    # A sentence's full stop ends a word; a dot before more of a name does not.
    return any(re.search(rf"(?<![\w/.-]){'#?' if f.isdigit() else ''}{re.escape(f)}(?![\w/-]|\.[\w/])", text)
               for f in forms)


def find(facts: Iterable[Fact], thread: str, cls: str, target: str, owner: set[str]) -> Grant | None:
    """The newest owner letter on an open wish of ``thread`` that names ``target`` and a ``cls`` verb."""
    facts = list(facts)
    from .dispositions import held
    letters = {str(f.data.get("id", f.id)): f for f in facts if f.kind == "letter"}
    waiting = held(facts)
    best: tuple[str, Grant] | None = None
    for wish in wishes(facts).values():
        if wish.thread != thread or not wish.open:
            continue
        cited = {say.letter for say in wish.says}
        cited |= {ident for ident, f in letters.items() if f.data.get("wish") == wish.id}
        for ident in cited:
            letter = letters.get(ident)
            if letter is None or ident in waiting:
                continue
            person = str(letter.data.get("from") or "")
            body = str(letter.data.get("body") or "")
            if person not in owner or not names(body, target) or not re.search(VERBS[cls], body, re.I):
                continue
            if best is None or letter.at > best[0]:
                best = (letter.at, Grant(ident, wish.id, person))
    return best[1] if best else None


def refusal(cls: str, target: str, why: str, owner: set[str]) -> str:
    """What was refused, that no owner letter grants it, and the override: facts, no question."""
    who = " or ".join(sorted(o.partition(":")[2] for o in owner)) or "the owner"
    what = CLASSES.get(cls, cls)
    return (f"refused: {what} ({target}{': ' + why if why else ''}); "
            f"no letter from {who} on this thread's open wishes names {target}.\n"
            f"override: a letter from {who} naming {target} and the act, stamped on the wish it serves.")


def record(home, strand: str, gen: int | None, thread: str, cls: str, target: str, grant: Grant) -> Fact:
    from .ledger import append
    from .home import mint
    return append(home, Fact(kind="guard.granted", by=f"strand:{strand}", id="guard.granted:" + mint(12),
                             data={"strand": strand, "gen": gen, "thread": thread, "class": cls, "target": target,
                                   "letter": grant.letter, "wish": grant.wish}))


def judge(room: Path, asks: list[tuple[str, str] | None], why: str = "") -> tuple[bool, list[str]]:
    """``(granted, lines)`` for one call's refusals in this room's thread.

    An ask of None is a refusal no letter can lift. Grants are recorded only
    when every refusal is granted, so a call still denied leaves no receipt.
    """
    import os
    from .home import home_of_room, strand_of_room
    from .ledger import read_facts
    if not asks or None in asks:
        return False, []
    room = Path(room)
    home = home_of_room(room)
    thread = os.environ.get("BRNRD_THREAD", "")
    owner = owners(home.root / "self")
    facts = read_facts(home) if thread else []
    found = {ask: find(facts, thread, *ask, owner) if thread else None for ask in dict.fromkeys(asks)}
    missing = [ask for ask, grant in found.items() if grant is None]
    if missing:
        return False, [refusal(cls, target, why, owner) for cls, target in missing]
    from .publish import _gen
    strand = strand_of_room(room)
    gen = _gen(facts, strand)
    lines = []
    for (cls, target), grant in found.items():
        record(home, strand, gen, thread, cls, target, grant)
        lines.append(f"granted: {CLASSES[cls]} ({target}) by {grant.letter} on {grant.wish}")
    return True, lines


def main(argv: list[str]) -> int:
    """``guard <class> <target>``: a gate's question (the gh shim's), answered by exit code.
    0 and the grant on stdout; 1 and the refusal on stderr."""
    import sys
    from .verbs_loom import Parser, _room
    parser = Parser(verb="guard", prog="brnrd runtime guard")
    parser.add_argument("cls", choices=sorted(CLASSES))
    parser.add_argument("target")
    parser.add_argument("--why", default="")
    parser.add_argument("--room")
    args = parser.parse_args(argv)
    granted, lines = judge(_room(args.room), [(args.cls, args.target)], args.why)
    (sys.stdout if granted else sys.stderr).write("\n".join(lines) + "\n")
    return 0 if granted else 1

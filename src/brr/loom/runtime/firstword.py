"""The owner's first word: what setup lands on the self when the person's first letter arrives.

A fresh self knows its owner only by ``people/<person>/channels.toml`` and
init's empty agreement, and a body has no page to read. When setup sees the
first letter, it lands the house rules citing that letter: the agreement
(brnrd's proposal, the person's to edit) and the page with its fixed
sections. A file already on main is left as it is. The interview is offered
once, as one line the router adds to the next message brnrd says to that
person (``offer`` / ``offered``), never as a gate. No guard is landed: since
threads/judged-acts the floor is the mark's one rule (mark.py), an act that
cannot be undone cites the person's letter.
"""

from __future__ import annotations

from pathlib import Path

from brr.daemon2.facts import Fact

from .selfrepo import _agreement, git, land_files

#: The three setup questions (trust design, Part B), offered in one line.
INTERVIEW = ("When you have a minute, three questions set how I work for you: what should I never do "
             "without asking you, what should I just do and tell you after, and how do you want to be told? "
             "Answer any time, or skip them; I'll learn as we go.")


def agreement(person: str, letter: str) -> str:
    return f"""# Agreement with {person}

How we work together. {person} writes it; brnrd proposes. This first version is
brnrd's proposal, landed when {person}'s first message arrived [{letter}]. Edit
any line, or tell brnrd to; your edits show as yours in git.

## The floor {{#floor}}

Five things brnrd never does unless a message from {person} names the thing:

- force-push a branch someone else holds (name the branch)
- delete under its home: rooms, the ledger, the self (name the path)
- merge a pull request whose checks are not green (name the PR)
- publish: a release, a package upload, a public post (name it)
- spend past the budget a piece of work was given (raise it)

brnrd marks every act that reaches outside its room. An act it marks as one
that cannot be undone is refused by the loom until the mark cites a message
from {person}. Where the mark could be wrong, brnrd holds the line itself and
says so when it was close.

## Merges {{#merges}}

brnrd merges its own pull requests when the checks are green, the change can
be undone, and it read the whole diff itself. Each merge gets a one-line
message with the link. A default: change it here.

## Asks {{#asks}}

When brnrd needs {person}'s word, it asks one plain question in chat and keeps
working on everything that doesn't wait on the answer. Silence is not a yes:
nothing on the floor happens on an unanswered ask.
"""


def page(person: str, letter: str, channels: list[str]) -> str:
    where = "\n".join(f"- Writes from {key} [{letter}]" for key in channels) or f"- First wrote [{letter}]"
    return f"""# {person}

Who {person} is to brnrd and what happened between them, each line cited by
letter id. How they work together is the [agreement](agreement.md); this page
doesn't restate it. {person} can read and edit it like any file in the self.
The page says each thing once, in its current form: before adding a line, find
the line it replaces and rewrite that one; the history stays in the letters it
cites. A line that points at a date or an event still to come ends with
`· due <date>`: the first body to read it after that date rewrites it from
what happened, or drops it.

## who

{where}

## just do

- Merge on green, as the [agreement](agreement.md#merges) says

## ask first

- Nothing yet beyond the [floor](agreement.md#floor)

## never

- force-push a branch someone else holds [{letter}]
- delete under the home root: rooms, ledger, self [{letter}]
- merge without green checks [{letter}]
- publish: a release, a package, a public post [{letter}]
- spend past a budget [{letter}]

## cares

## relations

## between us

## open

- What should I never do without asking?
- What should I just do and tell after?
- How do they want to be told?
"""


def first_word(home, person: str, letter: str) -> list[str]:
    """Land the owner's house rules citing ``letter``; return the paths written.

    Idempotent: a path already on main with content of its own is kept, so a
    restored self or a second setup changes nothing. Records the interview
    offer for the router, once per person.
    """
    from .config import read_array
    from .ledger import append, kind_facts, read_facts
    self_dir = Path(home.root) / "self"
    stub = _agreement(person)
    channels_toml = self_dir / "people" / person / "channels.toml"
    keys = read_array(channels_toml, "channels") if channels_toml.is_file() else []
    wanted = {
        f"people/{person}/agreement.md": agreement(person, letter),
        f"people/{person}/page.md": page(person, letter, keys),
    }
    files = {}
    for path, text in wanted.items():
        shown = git(self_dir, "show", f"main:{path}", check=False)
        if shown.returncode == 0 and shown.stdout != stub:
            continue
        files[path] = text
    if files:
        land_files(home.root, files, f"Land {person}'s house rules on their first word.\n\n"
                   f"The agreement and the page cite {letter}.\n\n")
    if not any(f.id == f"interview.offer:{person}" for f in kind_facts(read_facts(home), "interview.offer")):
        append(home, Fact(kind="interview.offer", by=f"loom:{home.install_id()}", id=f"interview.offer:{person}",
                          data=dict(person=person, letter=letter, line=INTERVIEW)))
    return sorted(files)


def pending(facts, person: str) -> str | None:
    """The interview line still owed to ``person``, or None once it was said."""
    from .ledger import kind_facts
    rows = kind_facts(facts, "interview.offer", "interview.offered")
    if any(f.kind == "interview.offered" and f.data.get("person") == person for f in rows):
        return None
    return next((str(f.data.get("line") or "") or None for f in rows
                 if f.kind == "interview.offer" and f.data.get("person") == person), None)


def offered(home, person: str, key: str) -> Fact:
    from .ledger import append
    return append(home, Fact(kind="interview.offered", by=f"loom:{home.install_id()}",
                             id=f"interview.offered:{person}", data=dict(person=person, key=key)))

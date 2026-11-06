"""A person letter's disposition: one of the triage branches cites it before a reply counts.

Every person letter lands in exactly one branch (threads/letter-triage):

- **wish**: a wish's says cite it (``--wish``);
- **chatter**: it wants nothing and gets an answer (``--chatter``);
- **correction**: it changes a page, scar or README (``--correction <path>``);
- **silent**: a joke, a test or a mistake, answered with one word or
  nothing (``--silent joke|test|mistake``); the person hears nothing on
  purpose, so the letter is not left unanswered;
- **held**: out of the person's pattern and irreversible, or not them at
  all (``--hold out-of-pattern|not-him``); the send is the check through a
  second path, and a held letter grants nothing (grants.find).

An out-of-pattern ask that is reversible is a wish, done and said loudly.
A letter is disposed when any wish's says, any letter's or note's
``chatter``, or any letter's or note's ``triage`` cites it. The graph is the
facts themselves (letter → say → wish → result); nothing here is stored twice.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterable

from brr.daemon2.facts import Fact

from .wishes import Say, read_says

#: The details a triage branch takes, by its flag on ``send``.
SILENT = ("joke", "test", "mistake")
HOLD = ("out-of-pattern", "not-him")
#: What a correction changes: a person's page, a scar, a README.
CORRECTS = ("people/", "memory/scars/")


def triage(branch: str, detail: str) -> str:
    """``silent joke``, ``held not-him``, ``correction people/x/page.md``: the field a send carries."""
    detail = detail.strip()
    if branch == "silent" and detail not in SILENT:
        raise ValueError(f"--silent {detail!r}: one of {', '.join(SILENT)}")
    if branch == "held" and detail not in HOLD:
        raise ValueError(f"--hold {detail!r}: one of {', '.join(HOLD)}")
    if branch == "correction" and (".." in detail.split("/") or not (
            detail.startswith(CORRECTS) or detail.rsplit("/", 1)[-1] == "README.md")):
        raise ValueError(f"--correction {detail!r}: a self path under people/ or memory/scars/, or a README.md")
    return f"{branch} {detail}"


def triaged(facts: Iterable[Fact], branch: str | None = None) -> dict[str, str]:
    """Letter id → the triage field a letter or note carries for it, of ``branch`` when given."""
    from .ledger import kind_facts
    found: dict[str, str] = {}
    for f in kind_facts(facts, "letter", "note"):
        value, re = str(f.data.get("triage") or ""), f.data.get("re")
        if value and re and (branch is None or value.split(" ", 1)[0] == branch):
            found[str(re)] = value
    return found


def held(facts: Iterable[Fact]) -> set[str]:
    """Letters a body held: they wait for the second path and grant nothing."""
    return set(triaged(facts, "held"))


def is_person(letter: Fact) -> bool:
    # data.from, as wishes.own_say reads it: a channel letter's by is loom:<install>.
    return letter.kind == "letter" and str(letter.data.get("from", "")).startswith(("p-", "person:"))


def letter_index(facts: Iterable[Fact]) -> dict[str, Fact]:
    return {str(f.data.get("id", f.id)): f for f in facts if f.kind == "letter"}


def resolve(letters: dict[str, Fact], raw: str) -> str | None:
    """A full letter id, or the one letter whose last segment is ``raw``."""
    if raw in letters:
        return raw
    found = [ident for ident in letters if ident.endswith(("/" + raw, ":" + raw))]
    return found[0] if len(found) == 1 else None


def place(body: str, quote: str) -> tuple[int, int] | None:
    """Where ``quote`` sits in ``body``, ignoring case, whitespace and punctuation."""
    index, text = [], []
    for at, char in enumerate(body):
        if char.isalnum():
            for low in char.lower():
                index.append(at)
                text.append(low)
    wanted = "".join(low for char in quote if char.isalnum() for low in char.lower())
    start = "".join(text).find(wanted) if wanted else -1
    if start < 0:
        return None
    return index[start], index[start + len(wanted) - 1] + 1 - index[start]


def say_of(letters: dict[str, Fact], raw: str, default: str | None) -> Say:
    """``quote`` (of ``default``) or ``id:quote`` → a say. Never refuses a quote."""
    ident, quote = None, raw
    for at in sorted((i for i, c in enumerate(raw) if c == ":"), reverse=True):
        ident = resolve(letters, raw[:at])
        if ident:
            quote = raw[at + 1:]
            break
    ident = ident or default
    if ident is None:
        raise ValueError(f"--says {raw!r}: no --re letter to quote, and no letter id before a colon")
    span = place(str(letters[ident].data.get("body", "")) if ident in letters else "", quote)
    return Say(ident, *span) if span else Say(ident, 0, None, quote)


def disposed(facts: Iterable[Fact], room: Path | None = None) -> set[str]:
    """Letter ids a say or chatter cites: the ledger, and this room's unsent port/out."""
    from .ledger import kind_facts
    found: set[str] = set()
    for f in kind_facts(facts, "wish", "letter", "note"):
        if f.kind == "wish":
            found.update(say.letter for say in read_says(f.data.get("says")))
        elif f.kind in {"letter", "note"}:
            found.update(f.data.get("chatter") or ())
            if f.data.get("triage") and f.data.get("re"):
                found.add(str(f.data["re"]))
    out = Path(room) / "port" / "out" if room is not None else None
    if out is not None and out.is_dir():
        from .port import PortError, parse_frontmatter
        for path in out.glob("*.md"):
            try:
                fields = parse_frontmatter(path.read_text())
                found.update((fields.get("chatter") or "").split())
                if fields.get("triage") and fields.get("re"):
                    found.add(fields["re"])
            except (PortError, OSError):
                continue
    return found


def result_of(raw: str) -> dict:
    """``--ref kind:ref`` → a wish.result's fields; ``pid:N`` keeps the command line."""
    if raw.startswith(("https://", "http://")):
        return {"kind": "url", "ref": raw}
    kind, sep, ref = raw.partition(":")
    if not sep or not kind or not ref:
        raise ValueError(f"--ref {raw!r}: expected kind:ref (pr:2390, pid:4242, a URL)")
    data = {"kind": kind, "ref": ref}
    if kind == "pid":
        if not ref.isdigit():
            raise ValueError(f"--ref {raw!r}: a pid is a number")
        cmd = subprocess.run(["ps", "-o", "command=", "-p", ref],
                             capture_output=True, text=True).stdout.strip()
        if not cmd:
            raise ValueError(f"--ref {raw!r}: no process {ref}")
        # A pid dies and is reused: what it ran is the receipt.
        data["cmd"] = cmd
    return data


def short(ident: str) -> str:
    return ident.rsplit("/", 1)[-1].rsplit(":", 1)[-1]


def echo(wish: str, says: list[Say], refs: list[str], letters: dict[str, Fact]) -> str:
    """One line per wish, the shape the board draws: ``w-x ← "quote" (stem) → refs: …``.

    A quote the loom could not place is marked ``"…"?``: it cites the whole letter.
    """
    cited = []
    for say in says:
        ident = say.letter
        body = str(letters[ident].data.get("body", "")) if ident in letters else ""
        if say.quote is not None:
            cited.append(f'"{say.quote}"? ({short(ident)})')
        elif say.size is None:
            cited.append(f"({short(ident)})")
        else:
            cited.append(f'"{say.text(body)}" ({short(ident)})')
    return f"{wish} ← {', '.join(cited) or '-'} → refs: {', '.join(refs) or '-'}"

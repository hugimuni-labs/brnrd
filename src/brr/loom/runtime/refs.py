"""``&refs`` in a letter's words, resolved once at intake and stored as full ids.

A ref is ``&`` and an id the ledger already holds: a wish (``&w-qa0b``, any
unique prefix of four or more), a letter (``&4529``, ``&s-ae66-x/8cb0m``, a
full ``letter:…`` id), a strand (``&s-ae66-3goetr``) or ``&thread:<name>``.
A ref that resolves to nothing, or to two things, never refuses a letter: it
gets no link, and the loom tells the sender and the body.
"""

from __future__ import annotations

import re

from .dispositions import letter_index, resolve as resolve_letter

# Starts the text or follows a space or an opening bracket, and ends on a word
# character: ``a=1&b=2`` in a URL and ``&&`` in shell are not refs.
REF = re.compile(r"(?<![^\s(\[])&([A-Za-z0-9][\w:/#.-]*\w|[A-Za-z0-9])")
CODE = re.compile(r"```.*?```|`[^`\n]*`", re.S)
STRAND = re.compile(r"s-[a-z0-9]+-[a-z0-9]+")
WISH_PREFIX = 4


def tokens(body: str) -> list[str]:
    """Every ref in ``body`` outside code, once each, in order."""
    return list(dict.fromkeys(REF.findall(CODE.sub("", body or ""))))


def kind_of(ident: str) -> str:
    """The kind a stored link names, read from its shape."""
    if ident.startswith("thread:"):
        return "thread"
    if ident.startswith("w-"):
        return "wish"
    return "strand" if STRAND.fullmatch(ident) else "letter"


class Index:
    """What a ref can name, read once per intake and only when there is a ref."""

    def __init__(self, facts, threads):
        from .wishes import wishes
        self.letters = letter_index(facts)
        self.wishes = set(wishes(facts))
        self.strands = {str(f.data.get("strand")) for f in facts if f.kind == "lease"}
        self.threads = set(threads)

    def resolve(self, token: str) -> str | None:
        """The one full id ``token`` names, or None."""
        if token.startswith("thread:"):
            return token if token.removeprefix("thread:") in self.threads else None
        if token.startswith("w-"):
            if token in self.wishes:
                return token
            found = [w for w in self.wishes if w.startswith(token)]
            return found[0] if len(found) == 1 and len(token) >= 2 + WISH_PREFIX else None
        if STRAND.fullmatch(token):
            return token if token in self.strands else None
        return resolve_letter(self.letters, token)


def link(facts, threads, body: str) -> tuple[list[str], list[str]]:
    """``(links, misses)``: the full ids ``body``'s refs name, and the refs that name nothing."""
    found = tokens(body)
    if not found:
        return [], []
    index = Index(facts, threads)
    links, misses = [], []
    for token in found:
        ident = index.resolve(token)
        if ident is None:
            misses.append(token)
        elif ident not in links:
            links.append(ident)
    return links, misses


def miss_words(misses: list[str]) -> str:
    return ("no " + ", ".join("&" + m for m in misses)
            + " in the ledger: the letter landed without a link for "
            + ("it" if len(misses) == 1 else "them"))

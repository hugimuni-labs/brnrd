"""What a person's chat reads: a letter's words without the ledger's ids.

A letter keeps its text and its structured ``links`` in the ledger, and the
board shows both. A reply binds by the platform's message receipt and then by
those links (``connector.reply_for_message``, ``wishes._answer``), never by the
text. So the ids are labels for the ledger. This is the one render the router
applies on its way to a channel: wish ids, letter and strand ids, notice ids,
``&refs`` and ``thread:<name>`` addresses leave the text, and the sentence
around them closes up.

Code spans, fenced blocks and URLs are left as written: a command or a link
must work when copied.
"""

from __future__ import annotations

import re

# A protected span: a fence, an inline code span, or a URL.
_KEEP = re.compile(r"```.*?```|`[^`\n]*`|https?://\S+", re.S)

# The templates' own markers, read whole so the sentence after them stays a sentence.
_ASK = re.compile(r"^ask &?\S+ from thread:[\w.-]+: ", re.M)
_REC = re.compile(r"^rec: ", re.M)
_DELIVERED = re.compile(r"^delivered &\S+?: ", re.M)

# An id the ledger mints: a wish, a strand and its letter, a notice, a ``letter:`` id.
_ID = (r"w-[0-9a-z]{12}"
       r"|s-[0-9a-f]{4}-[0-9a-z]{6}(?:/[\w.-]*\w)?"
       r"|loom/[a-z][\w-]*-[0-9a-f]{10}"
       r"|letter:[\w:/.-]*\w")
# An ``&ref`` as ``refs.REF`` reads it: after a space or an opening bracket, ending on a word character.
_REF = r"(?<![^\s(\[])&[A-Za-z0-9](?:[\w:/#.-]*\w)?"
_TOKEN = rf"(?:{_REF}|(?<![\w/-])(?:{_ID})(?![\w/-]))"
# Before closing punctuation or the line's end, the token takes the space before it;
# elsewhere it takes the space after it.
_TAIL = re.compile(rf"[ \t]*{_TOKEN}(?=[ \t]*(?:[.,;:!?)\]]|$))", re.M)
_HEAD = re.compile(rf"{_TOKEN}[ \t]*")
_EMPTY_PARENS = re.compile(r"[ \t]*\([ \t,]*\)")
# A line the strip left opening on its separator (``- w-…: the line``) opens on its words.
_LEAD = re.compile(r"^([ \t]*)([-*](?=[ \t]*[:,;·][ \t]))?[ \t]*[:,;·][ \t]+", re.M)
_THREAD = re.compile(r"(?<![\w/-])&?thread:([\w.-]*\w)")


def for_person(text: str) -> str:
    """``text`` as a person's chat shows it: the same words, no ledger ids."""
    if not text:
        return text
    kept: list[str] = []

    def hold(match: re.Match) -> str:
        kept.append(match.group(0))
        return f"\x00{len(kept) - 1}\x00"
    part = _KEEP.sub(hold, text)
    asked = bool(_ASK.search(part))
    part = _ASK.sub("", part)
    if asked:
        part = _REC.sub("I recommend: ", part)
    part = _DELIVERED.sub("Delivered: ", part)
    part = _THREAD.sub(r"\1", part)
    part = _TAIL.sub("", part)
    part = _HEAD.sub("", part)
    part = _EMPTY_PARENS.sub("", part)
    part = _LEAD.sub(lambda m: m.group(1) + (m.group(2) + " " if m.group(2) else ""), part)
    return re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m.group(1))], part)

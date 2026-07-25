"""The run card's ``Now`` projection — one rule, one implementation.

The resident writes ``.card`` as ordinary Markdown: a ``Now`` section for the
compact live surface, the run's arc in sections below it. Three Python callers
need to know where that section starts and stops, and until #722 each carried
its own copy of the rule:

* :mod:`brr.daemon` projects it onto ``card_text`` — the copy that reaches the
  wire, and the only one whose output is size-bound by a transport.
* :mod:`brr.prompts` injects "the ``Now`` you left on the card" into the next
  wake.
* :mod:`brr.hooks` meters it on the boundary bar.

**Why a leaf module rather than folding it into one of the three.** The prompts
copy justified itself on import cost — "prompts must not pull the daemon module
into every wake's import graph for eighteen lines of string handling." Measured
(cold-import delta over a bare interpreter, best of 7), that argument only rules
out one of the three directions:

===================================  =========
``import brr.hooks``                   ~45 ms
``import brr.hooks, brr.card``         ~45 ms
``import brr.hooks, brr.prompts``      ~44 ms
``import brr.hooks, brr.daemon``       ~91 ms
``import brr.prompts, brr.daemon``     ~93 ms
===================================  =========

So "the rule lives in ``daemon``" costs its other two callers ~46 ms each and is
genuinely excluded. "The rule lives in ``prompts``" costs nothing measurable —
``hooks`` and ``prompts`` already share most of their dependency closure, which
the original docstring's reasoning did not anticipate. Between the two cheap
options the tiebreak is not cost: it is that ``hooks`` renders a statusline chip
and would have to import the wake-prompt builder to do it. The rule went
somewhere none of the three owns because no one of them owns it.

**Why the anchor is a name and not a depth.** The original rule matched the
literal line ``## now``. A card headed ``# Now`` missed it, fell through to
"return the whole body", and published the entire card into a 4096-char field —
turning a heading-level typo into a batch-wide 422 (#722, root cause of #685).
The heading level is invisible to the writer in every surface the resident can
see, so the parser accommodates the writer rather than the reverse.
"""

from __future__ import annotations

import re

#: Mirror of ``brnrd.schemas.LiveRunIn.card_text``'s ``max_length``. That field
#: is the authority — this constant exists because ``brr`` must not import the
#: API package to know the number, and ``tests/test_card.py`` pins the two
#: together so the mirror cannot drift silently.
CARD_TEXT_MAX_CHARS = 4096

#: The section anchor: any heading depth, any case, and any trailing text after
#: the word. The trailing-text allowance is driven, not defensive — 10 of 272
#: captured run bodies head the section ``## Now — done`` or ``## Now doing``,
#: and under an exact-match anchor every one of them published whole. ``\b``
#: keeps ``## Nowhere`` from matching.
_NOW_HEADING_RE = re.compile(r"(#{1,6})\s*now\b", re.IGNORECASE)

#: Any ATX heading, with its depth captured. ``#foo`` is not a heading in
#: CommonMark and is not one here.
_HEADING_RE = re.compile(r"(#{1,6})(?:\s|$)")

#: Fenced blocks are not scanned for headings. Generalising the anchor to any
#: depth generalises the *terminator* too, and a ``# comment`` line inside a
#: shell fence in a ``## Now`` section would otherwise end the section early —
#: a truncation path that the old H2-only rule never had, created by fixing it.
_FENCE_RE = re.compile(r"(?:```|~~~)")

#: Appended when a projection is truncated, so a reader sees truncation rather
#: than a body that merely appears to end.
_TRUNCATION_MARK = "\n…"


def _heading_depth(line: str) -> int | None:
    """Depth of *line* as an ATX heading, or ``None`` when it is not one."""

    match = _HEADING_RE.match(line.strip())
    return len(match.group(1)) if match else None


def now_projection(body: str, *, limit: int | None = None) -> str:
    """Project a run body onto its ``Now`` section.

    The section is anchored by *name* at any heading depth and terminates at
    the first heading at a depth **less than or equal to** the anchor's. Both
    halves of that rule matter: matching ``# Now`` without generalising the
    terminator would let an H1 card run to the end of the body, and
    terminating on any heading at all would break ``### Sub`` out of the
    ``## Now`` section it belongs to.

    A body with no ``Now`` section is returned whole — one-note cards stay
    valid — but *bounded*, when the caller names a *limit*. That fallback is
    where #722's outage lived: "return the whole body" is a sane default for a
    wake injection and an unbounded payload for a transport, so the caller that
    has a transport says so. Nothing is lost either way; the full text reaches
    ``body.md`` at closeout.

    :param body: the raw ``.card`` text.
    :param limit: maximum characters to return. ``None`` (the wake-injection
        callers) means unbounded; the publish path passes
        :data:`CARD_TEXT_MAX_CHARS`. Applied to the *whole* return value, not
        only to the no-section fallback — 4 of 272 captured bodies carry a
        ``Now`` section that is itself over the cap, and a section that
        oversteps the transport 422s exactly like a body that does.
    """

    lines = body.replace("\r\n", "\n").split("\n")
    start: int | None = None
    depth = 0
    fenced = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if _FENCE_RE.match(stripped):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = _NOW_HEADING_RE.match(stripped)
        if match:
            start = index + 1
            depth = len(match.group(1))
            break
    if start is None:
        return _bound(body.strip(), limit)
    projected: list[str] = []
    fenced = False
    for line in lines[start:]:
        stripped = line.strip()
        if _FENCE_RE.match(stripped):
            fenced = not fenced
        elif not fenced:
            found = _heading_depth(line)
            if found is not None and found <= depth:
                break
        projected.append(line)
    return _bound("\n".join(projected).strip(), limit)


def section_names(body: str, *, exclude_now: bool = True) -> list[str]:
    """The body's top-level section names, in order.

    "Top level" is the shallowest heading depth the body actually uses, not a
    hardcoded ``##`` — an H1-sectioned card has a shape too, and reporting it
    as shapeless is the same defect as projecting it whole.
    """

    headings: list[tuple[int, str]] = []
    fenced = False
    for line in body.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if _FENCE_RE.match(stripped):
            fenced = not fenced
            continue
        if fenced:
            continue
        found = _heading_depth(line)
        if found is not None:
            headings.append((found, stripped.lstrip("#").strip()))
    if not headings:
        return []
    top = min(depth for depth, _ in headings)
    names = [name for depth, name in headings if depth == top and name]
    if exclude_now:
        names = [n for n in names if not re.match(r"now\b", n, re.IGNORECASE)]
    return names


def _bound(text: str, limit: int | None) -> str:
    if limit is None or len(text) <= limit:
        return text
    keep = max(0, limit - len(_TRUNCATION_MARK))
    return text[:keep] + _TRUNCATION_MARK

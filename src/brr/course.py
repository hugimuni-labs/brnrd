"""The course: the run's own route, read back off its card.

The blueprint (:mod:`brr.promises`) records what a run told the *world* it
would make. This module reads what the run told *itself* it would do — the
checkbox plan a resident keeps on its ``.card`` — and hands it back at every
tool boundary, so the route survives the one thing that actually loses it:
a transcript that only grows.

The failure this closes is the maintainer's own wording (2026-08-06,
evt-…-5io2):

    sonnets runs go fine if I do not derail them in the process. But as soon
    as I start throwing in some more topics, then something definitely gets
    lost… my strong suspicion is that we do not have a right mechanism for
    delivering the current run's plan, in a live updated manner… refreshed,
    reminded of at the boundary… occasionally pulled up by you and updated
    or checked, especially at the stop hook.

And the mechanics that make it structural rather than attentional: a wake's
plan is written at t=0 of a scroll that only accretes. A steer arriving at
minute 40 lands *on top of* a transcript whose plan is a hundred kilobytes
down — attention is recency-weighted, so losing the thread is the default
physics. The boundary channel is the only writable position in the loud
zone; what rides it is what survives derailment. ``design-the-blueprint.md``
already named ``.card`` §Now as the fourth flag with no boundary view
("plan vs progress — read by no boundary"); this is that flag, generalised
the same way the blueprint generalised the other three.

**No new file, no new verb.** The course lives inside the ``.card`` the
resident already writes, as a ``## Plan`` (or ``## Course``) section of
GitHub-flavoured checkboxes:

    ## Plan
    - [x] read the ticket
    - [ ] fix the parser  ← (optional cursor mark; else first unchecked)
    - [ ] gate + PR

Editing the card *is* the API — checking a row is the discharge that moves
the chip, which is the maintainer's own off-switch rule for the boundary
bar ("every line should have a way to turn it off by performing the action
it asks for"). A card with no such section renders nothing: like the
blueprint, an absent course is not evidence of a plan kept or lost, and a
guard may only assert something the run can be proven wrong about.

Everything here is best-effort in the blueprint's posture: a malformed
section degrades to fewer rows, and no read path can raise into a boundary.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

#: Section headings that open a course. Two spellings, deliberately: `Plan`
#: is what residents naturally write on a card; `Course` is the surface's
#: own name (the blueprint owes the world, the course routes the run — and
#: "course correct" is the verb the whole feature exists for). Matched
#: case-insensitively, as `## <heading>` exactly (deeper headings don't
#: open a course; a `### Plan` inside another section is that section's
#: business).
HEADINGS = ("plan", "course")

_HEADING_RE = re.compile(
    r"^##\s+(?:%s)\s*$" % "|".join(HEADINGS), re.IGNORECASE
)
_ANY_H2_RE = re.compile(r"^##\s+\S")
_ROW_RE = re.compile(r"^\s*[-*]\s+\[(?P<mark>[ xX])\]\s+(?P<text>\S.*)$")
#: An explicit cursor: a row ending with `←` (with or without surrounding
#: space) claims "current" even when it is not the first unchecked row.
_CURSOR_RE = re.compile(r"\s*←\s*$")

#: Defensive caps, same posture as the blueprint's: a card appending more
#: rows than this is a queue wearing a plan's clothes, and a row longer than
#: this is prose. Overflow rows are *counted* (the chip's denominator stays
#: honest) but not rendered.
MAX_ROWS = 30
_MAX_ROW_CHARS = 160
#: Render caps for detail lines.
_LINE_ROW_CHARS = 80
_STOP_ROWS_MAX = 6


@dataclass
class Row:
    text: str
    done: bool
    cursor: bool = False


@dataclass
class Course:
    rows: list[Row] = field(default_factory=list)
    #: Rows beyond :data:`MAX_ROWS`, counted but unparsed.
    overflow: int = 0

    @property
    def total(self) -> int:
        return len(self.rows) + self.overflow

    @property
    def done_count(self) -> int:
        return sum(1 for row in self.rows if row.done)

    @property
    def open_rows(self) -> list[Row]:
        return [row for row in self.rows if not row.done]

    @property
    def current(self) -> Row | None:
        """The row the run is on: explicit `←` cursor, else first unchecked."""
        for row in self.rows:
            if row.cursor and not row.done:
                return row
        for row in self.rows:
            if not row.done:
                return row
        return None


def parse(card_body: str | None) -> Course | None:
    """Extract the course from a card body. ``None`` when there is none.

    The section runs from the matching ``## `` heading to the next ``## ``
    heading (or EOF). Only checkbox rows inside it count; interleaved prose
    is legal and ignored — the card stays a card, not a form.
    """
    if not card_body:
        return None
    in_section = False
    rows: list[Row] = []
    overflow = 0
    for raw_line in card_body.splitlines():
        if _HEADING_RE.match(raw_line):
            in_section = True
            continue
        if in_section and _ANY_H2_RE.match(raw_line):
            break
        if not in_section:
            continue
        match = _ROW_RE.match(raw_line)
        if not match:
            continue
        if len(rows) >= MAX_ROWS:
            overflow += 1
            continue
        text = match.group("text").strip()
        cursor = bool(_CURSOR_RE.search(text))
        if cursor:
            text = _CURSOR_RE.sub("", text).strip()
        if len(text) > _MAX_ROW_CHARS:
            text = text[: _MAX_ROW_CHARS - 1] + "…"
        rows.append(Row(text=text, done=match.group("mark").lower() == "x"))
        rows[-1].cursor = cursor
    if not rows and not overflow:
        return None
    return Course(rows=rows, overflow=overflow)


def token(course: Course | None) -> str:
    """Change token over the parsed rows — text and marks, nothing that
    ticks. Stable across boundaries where the section did not change, so the
    detail line latches on the course's *own* delta exactly like the
    blueprint's (`promises.token`): content dedupe cannot carry an
    obligation, and a moving field must never defeat the latch.
    """
    course = course or Course()
    payload = "\n".join(
        f"{'x' if row.done else ' '}|{int(row.cursor)}|{row.text}"
        for row in course.rows
    ) + f"|overflow:{course.overflow}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def chip(course: Course | None) -> str | None:
    """The bar chip: ``course 2/5``. Standing fact, seven characters.

    Renders only while rows are open — a finished (or absent) course costs
    nothing, the same asymmetry as ``owed``: absence is not evidence of a
    route kept, so absence renders silence, never a pass.
    """
    if course is None or not course.rows or not course.open_rows:
        return None
    return f"course {course.done_count}/{course.total}"


def _clip(text: str) -> str:
    if len(text) > _LINE_ROW_CHARS:
        return text[: _LINE_ROW_CHARS - 1] + "…"
    return text


def current_line(course: Course | None) -> str | None:
    """The one-line route reminder: current row + how much stands open.

    Rendered on the course's own delta and at the boundary a fresh event
    lands on — the derailment moment, where "update the plan, then decide:
    continue or turn" needs the plan in the loud zone to be decidable.
    """
    if course is None or not course.open_rows:
        return None
    current = course.current
    if current is None:
        return None
    remaining = len(course.open_rows) + course.overflow
    more = f" (+{remaining - 1} more open)" if remaining > 1 else ""
    return f"- course → {_clip(current.text)}{more}"


def stop_lines(course: Course | None) -> list[str]:
    """The closeout read-back: every open row, named.

    Unlatched at Stop for the blueprint's reason — the closeout is the
    moment the surface exists for, and an open row has to be said there
    even if it was said mid-run. Inject-only, never a block: the course is
    a self-report, so the honest ask is disposition ("check it, or say what
    changed"), not a wall. A run that finished its route pays nothing.
    """
    if course is None or not course.open_rows:
        return []
    open_rows = course.open_rows
    shown = open_rows[:_STOP_ROWS_MAX]
    lines = [
        f"- course open: {len(open_rows) + course.overflow} of "
        f"{course.total} rows unchecked — finished ⇒ check them off on "
        ".card; dropped or superseded ⇒ say so in the reply."
    ]
    for row in shown:
        lines.append(f"  - [ ] {_clip(row.text)}")
    hidden = len(open_rows) - len(shown) + course.overflow
    if hidden > 0:
        lines.append(f"  - … and {hidden} more")
    return lines

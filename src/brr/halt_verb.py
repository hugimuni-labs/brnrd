"""``halt:`` — the verb that lets a seat end (design-the-four-stops.md
§"The two verbs", signed 2026-09-18).

Every other exit was closed, each for a good reason, and together they
replaced *premature quitting* with a seat that **cannot leave** — the
worse failure, because it is invisible: measured 2026-09-18, a seat
carrying 778,000 tokens re-read at every boundary, for a day and a half,
announcing itself nowhere.

    halt: true
    reason:     why this body ends                          (required)
    carry:      the successor's brief                       (optional)
    resumable:  what it would take to pick this up, and who (required
                                                             when carry
                                                             is absent)
    shell: / core:  the next body, if one is wanted now

**Two verbs, not four** — ``park`` (the seat stays, the scroll stays warm,
costs nothing) and ``halt`` (the seat ends, costs one boot for whatever
comes next). ``respawn:`` retires into this one: *"respawn and quit carry
the same potential damage, so they should have the same precautions"*
(the maintainer, evt-…-kocz). The damage — everything unwritten, lost —
does not care whether a successor follows, which is why the precautions
here do not branch on ``carry:``.

**Why the two fields are not one.** ``carry:`` answers *why is this body
spent?* — cost, drift, a better core for what follows; the work continues
and the brief covers it. ``resumable:`` answers a different question,
*why does the work stop here?*, and it earns its place over a generic
"explain further" (which grows boilerplate inside a week) on three
counts: it is answerable from facts the bounce has just listed, it is
*checkable* against those same facts, and it produces something a later
reader wants — a reason is archaeology, a ``resumable:`` is a handle.

**What this module is.** The pure half — model ``hold_verb`` /
``cut_verb`` / ``await_verb``: parse, validate, and the one predicate the
bounce is made of (:func:`unnamed`). No filesystem, no clock, no daemon
state. ``daemon.py`` collects the open items (pending events, produce
with no PR, unticked course rows, live strands) and owns every effect;
``outbox/verbs.py`` routes the file.

**What it deliberately does not do: gate the halt behind approval.** A
halt that waits for a nod is today's state wearing a better name. What
makes immediacy defensible is that ending is reversible by construction —
a new seat is cheap and the user's next message mints one. So the risk is
not *that the seat ended*; it is *that the brief was never written*, and
the brief is what the bounce checks. Approval would gate the wrong thing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Marker tolerance, shared with every other outbox verb
#: (``await_verb._MARKER_VALUES``).
_MARKER_VALUES = {"", "true", "1", "yes", "on"}

#: Every key a ``halt:`` file may carry. Anything else is refused *by
#: name* — the #1187 lesson: a caller who typos ``carry_forward:`` for
#: ``carry:`` must get a parse error naming the typo, not a silently
#: ignored field and a halt that reads as if no brief was ever written.
#: ``topic:`` rides here because it is every act's modifier (outbox
#: ``table.py`` move 5c), not a halt field.
_KNOWN_KEYS = frozenset({
    "halt", "reason", "carry", "resumable", "shell", "core", "topic",
})

#: What a halt is counted as, per account. The two are *not* the same
#: event and the design counts them apart: a halt **with** carry is
#: ordinary metabolism — a body wearing out, which is what bodies do; a
#: halt **without** is the one that deserves the pattern count, because
#: four in a week means something is wrong with the work, not the seat.
KIND_CARRIED = "carried"
KIND_STOPPED = "stopped"


@dataclass(frozen=True)
class HaltDeclaration:
    """A parsed, structurally valid ``halt:`` declaration.

    Shape only. Whether the brief actually *names* what is open is
    :func:`unnamed`'s question, and it needs facts only the daemon holds.
    """

    reason: str
    carry: str | None = None
    resumable: str | None = None
    shell: str = ""
    core: str = ""

    @property
    def carried(self) -> bool:
        """Whether a successor is wanted: the work continues past this body."""
        return bool(self.carry)

    @property
    def kind(self) -> str:
        return KIND_CARRIED if self.carried else KIND_STOPPED

    @property
    def brief(self) -> str:
        """The text the bounce checks open items against.

        One field or the other, never both and never neither — the parse
        refuses the empty case. With ``carry:`` the brief must *name* what
        is open; without it, each open item must be dispositioned or
        declared owed in ``resumable:``. The same rule, twice, which is
        why it is one predicate rather than two code paths.
        """
        return (self.carry or self.resumable or "").strip()


@dataclass(frozen=True)
class OpenItem:
    """One thing the daemon attests is open at halt time.

    *handle* is the token the bounce prints and the brief is checked
    against; *aliases* are the other spellings of the same thing that
    count as naming it (an event's full id and its short tail, a strand's
    run id and tail, a course row's own text). *line* is the bounce row a
    person reads.
    """

    kind: str
    handle: str
    line: str
    aliases: tuple[str, ...] = ()

    def named_by(self, brief: str) -> bool:
        """Whether *brief* names this item, case-insensitively.

        Substring, not identity, and deliberately: a brief is prose, and
        the point of the check is that the claim **collides with the
        ledger** instead of floating beside it — not that it be written in
        a form language. A token this short can be named by accident; the
        residual is named in the report rather than patched around with a
        stricter grammar nobody would write.
        """
        haystack = (brief or "").casefold()
        if not haystack:
            return False
        for token in (self.handle, *self.aliases):
            token = str(token or "").strip().casefold()
            if token and token in haystack:
                return True
        return False


def unnamed(
    declaration: HaltDeclaration, items: "list[OpenItem] | tuple[OpenItem, ...]",
) -> list[OpenItem]:
    """The open items *declaration*'s brief does not account for.

    Empty ⇒ the halt stands. Non-empty ⇒ the bounce, once (the daemon owns
    the ladder and its cap, exactly as it owns the bolt's).

    **The pondering step is made of facts, not willpower.** A reason like
    "context is large" passes a non-empty check and prevents nothing; a
    brief that has to name four pending events, a branch with no PR and a
    live strand cannot be written without reading them.
    """
    brief = declaration.brief
    return [item for item in items or () if not item.named_by(brief)]


def parse_halt(fm: dict[str, Any]) -> tuple[HaltDeclaration | None, str | None]:
    """Parse a ``halt:`` directive's frontmatter.

    Returns ``(declaration, error)``. On any refusal *declaration* is
    ``None`` and *error* is the one-line reason a notice carries — and
    **nothing happens**: the seat does not end, which is the whole point
    of refusing rather than half-accepting.
    """
    marker = str(fm.get("halt") if fm.get("halt") is not None else "").strip()
    if marker.lower() not in _MARKER_VALUES:
        return None, (
            f"halt: {marker!r} is not a recognised marker (use `halt: true`)"
        )

    unknown = sorted(set(fm.keys()) - _KNOWN_KEYS)
    if unknown:
        return None, (
            "halt: unrecognised field(s) " + ", ".join(unknown)
            + " — known fields are reason, carry, resumable, shell, core"
        )

    reason = str(fm.get("reason") or "").strip()
    if not reason:
        return None, (
            "halt: reason: is required and must be non-empty — a body that "
            "ends says why, whether or not a successor follows"
        )

    carry = str(fm.get("carry") or "").strip() or None
    resumable = str(fm.get("resumable") or "").strip() or None
    shell = str(fm.get("shell") or "").strip()
    core = str(fm.get("core") or "").strip()

    if not carry and not resumable:
        return None, (
            "halt: with no carry: the work stops here, so resumable: is "
            "required — what it would take to pick this up, and who. "
            "(Halting *with* a successor ⇒ write carry: instead.)"
        )

    if not carry and (shell or core):
        # A next body *is* a successor, and a successor with no brief is
        # precisely the handover this design exists to stop losing. Refused
        # rather than silently treated as a carry: guessing the brief is
        # the one thing nothing here may do.
        named = ", ".join(part for part in (
            f"shell: {shell}" if shell else "", f"core: {core}" if core else "",
        ) if part)
        return None, (
            f"halt: {named} names the next body but carry: is absent — a "
            "successor with no brief is the handover that loses everything. "
            "Write carry:, or drop the body and keep resumable:."
        )

    return HaltDeclaration(
        reason=reason, carry=carry, resumable=resumable, shell=shell, core=core,
    ), None


def durable_declaration(
    declaration: HaltDeclaration,
    *,
    dissent: "tuple[str, ...] | list[str]" = (),
) -> dict[str, Any]:
    """The record a halt leaves behind — **permanent and public**.

    Bounded like ``cut_verb.durable_declaration``'s, and for the same
    reason: a syntactically valid half-record is more dangerous than an
    honest truncation marker. ``dissent`` carries the open items the halt
    went ahead over once the bounce ladder was spent.
    """
    def _clip(text: str | None) -> str | None:
        if text is None:
            return None
        text = str(text)
        if len(text) > _MAX_TEXT_CHARS:
            return text[: _MAX_TEXT_CHARS - 1] + "…"
        return text

    rows = [str(row) for row in (dissent or ())][:_MAX_DISSENT]
    return {
        "declaration_version": 1,
        "kind": declaration.kind,
        "reason": _clip(declaration.reason),
        "carry": _clip(declaration.carry),
        "resumable": _clip(declaration.resumable),
        "shell": declaration.shell,
        "core": declaration.core,
        "dissent": [_clip(row) for row in rows],
        "dissent_omitted": max(0, len(dissent or ()) - len(rows)),
    }


_MAX_TEXT_CHARS = 2048
_MAX_DISSENT = 64

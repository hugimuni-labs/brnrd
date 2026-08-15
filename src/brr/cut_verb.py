"""``cut:`` — the bolt: a run's completion, declared by the resident and
checked by the daemon against what it already attests
(``kb/design-the-bolt.md``).

The polarity flip the design signs (fork 1, maintainer, 2026-08-07): today's
closeout machinery asks *"did you forget anything?"* with independent
heuristics that nag and latch. The bolt replaces the question with *"present
your declaration"* — a resident-authored artifact the daemon diffs against
its own attested facts (pending events, ``.relics.jsonl`` + auto-derived
produce, ``.promises.jsonl``) and either accepts or bounces with a named
diff. This module is the pure parse/validate half — model: ``await_verb.py``
— no I/O, no cross-referencing against live daemon state; the daemon's own
``_drain_outbox`` branch owns that half.

**Declared keys, all optional except the marker** — a minimal bolt
(``cut: true`` and nothing else, just a woven body) is legal: *stopping is a
result*.

    cut: true
    asks:
      evt-...-txwl: answered
      evt-...-4e17: deferred:schedule.md
    decisions:
      "notify.gate stopgap": extended
    produce: attested
    owed: none
    spend: ~$12, 56m
    next: none

**Why ``asks``/``owed`` are keyed mappings, not YAML bullet lists.**
The design doc's own mockup writes ``asks:`` as a list of ``{event,
disposition}`` objects (fork-signed prose, not a wire contract) — but
``protocol.py``'s frontmatter grammar (``protocol._parse_block``) has no
list syntax at all; it parses ``key: value`` pairs and arbitrarily deep
*nested dicts*, never a ``- `` bulleted sequence. Extending that shared,
widely-used parser to add list support was judged out of scope for this
build (blast radius: every other outbox verb and every ``runners.md``-style
config block routes through the same function) and unnecessary: a dict
keyed by event id (``asks``) or by an arbitrary label (``owed``) expresses
the same information using only the nesting the grammar already has, and it
round-trips through ``protocol.parse_frontmatter`` today with zero changes
there. This is the one shape ``brnrd cut`` and a hand-authored file can both
produce reliably; it is documented here as a deliberate scope decision, not
an oversight. A value under ``asks`` may be the bare disposition string
(``evt-...: answered`` — the "flow-scalar form" the task named) or a
one-key dict reached through the grammar's *nested-indentation* form:

    asks:
      evt-...-txwl:
        disposition: answered

Both are accepted. **Inline flow-mapping syntax is not** — writing the
one-key dict on a single line (``evt-...-txwl: {disposition: answered}``)
does not produce a dict at all: ``protocol._parse_block`` has no brace
grammar, so that line parses as the literal string
``"{disposition: answered}"``, which then fails
:func:`_valid_disposition` and the whole declaration is refused. This is
not hypothetical — it is the exact drop #1219 measured live (``asks: …
unrecognised disposition '{disposition: answered}'``), traced back to this
docstring's own inline example reading as literal syntax when it was only
ever meant to describe the resulting shape. Only the indented nested-block
form above reaches the parser as a real nested dict.

Unknown top-level keys are refused by name — the #1187 lesson applied here
too: a caller who typos ``decision:`` for ``decisions:`` gets a parse error
naming the typo, not a silently-ignored field and a bolt that reads as if
nothing was ever declared.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

#: Values of the ``cut:`` key that mean "here is my declaration" — the same
#: marker tolerance every outbox verb uses (``await_verb._MARKER_VALUES``).
_MARKER_VALUES = {"", "true", "1", "yes", "on"}

#: What ``produce:`` may declare. Anything else is a parse error naming the
#: bad value — there is no lenient third state; an unrecognised value is not
#: silently treated as "undeclared".
_PRODUCE_VALUES = {"attested", "none"}

#: Every field the declaration frontmatter may carry, beyond the ``cut``
#: marker itself. A key outside this set is refused by name.
_KNOWN_KEYS = frozenset({
    "cut", "asks", "decisions", "produce", "owed", "spend", "next", "strands",
})

#: Disposition prefixes that take a free-text tail (``deferred:<where>``,
#: ``noted:<why>``). ``answered`` is the one disposition with no tail.
_DISPOSITION_PREFIXES = ("deferred:", "noted:")

#: Closed set of ``strands:`` dispositions (#1197) — a live child this run
#: dispatched must be left in one of these states, checked as the value's
#: leading word; the free tail after it carries the reason. Closed because
#: the whole point is that a machine can read it.
_STRAND_DISPOSITIONS = ("handoff", "converged", "stopped", "abandoned")
_STRAND_DISPOSITION_RE = re.compile(
    r"^(" + "|".join(_STRAND_DISPOSITIONS) + r")\b", re.IGNORECASE
)

# The declaration is reader-authored and therefore unbounded at the parse
# layer.  Persistence is deliberately narrower: a declaration either fits
# these limits whole or is replaced by an explicit omission marker.  Never
# clip individual strings or rows — a syntactically valid half-declaration is
# a more dangerous record than an honest absence.
PERSISTENCE_MAX_ASKS = 64
PERSISTENCE_MAX_OWED = 64
PERSISTENCE_MAX_DECISIONS = 64
PERSISTENCE_MAX_DISSENT = 64
PERSISTENCE_MAX_STRANDS = 64
PERSISTENCE_MAX_TEXT_CHARS = 1024
PERSISTENCE_MAX_JSON_BYTES = 65_536


@dataclass(frozen=True)
class AskDisposition:
    """One ``asks:`` row: an event this run carried, and how it was closed."""

    event: str
    disposition: str
    label: str = ""


@dataclass(frozen=True)
class OwedRow:
    """One carried ``owed:`` row — a promise the resident names rather than
    ships, with the reason and where the remainder goes."""

    label: str
    ref: str
    why: str
    where: str = ""


@dataclass(frozen=True)
class StrandDisposition:
    """One ``strands:`` row: a live child this run dispatched, and how the
    run leaves it (#1197)."""

    run: str
    disposition: str


@dataclass(frozen=True)
class CutDeclaration:
    """A parsed, structurally-valid ``cut:`` declaration.

    Everything here is *shape*-checked only: whether an ``asks`` row's event
    id is actually pending, whether ``produce: none`` matches the relics
    manifest, whether an ``owed`` row actually names an outstanding
    promise — none of that is decidable without the daemon's own live
    state, and belongs to ``daemon.py``'s ``_cut_mismatches``, not here.
    """

    asks: tuple[AskDisposition, ...] = ()
    decisions: tuple[str, ...] = field(default_factory=tuple)
    produce: str | None = None
    owed_none: bool = True
    owed: tuple[OwedRow, ...] = ()
    spend: str | None = None
    next: str | None = None
    strands: tuple[StrandDisposition, ...] = ()


def _valid_disposition(value: str) -> bool:
    if value == "answered":
        return True
    return any(
        value.startswith(prefix) and len(value) > len(prefix)
        for prefix in _DISPOSITION_PREFIXES
    )


def _parse_asks(raw: Any) -> tuple[tuple[AskDisposition, ...], str | None]:
    if raw in (None, ""):
        return (), None
    rows: list[AskDisposition] = []
    if isinstance(raw, dict):
        for event, value in raw.items():
            event = str(event).strip()
            if not event:
                return (), "asks: an empty event key names nothing"
            if isinstance(value, dict):
                disposition = str(value.get("disposition") or "").strip()
                label = str(value.get("label") or "").strip()
            else:
                disposition = str(value if value is not None else "").strip()
                label = ""
            if not _valid_disposition(disposition):
                return (), (
                    f"asks: {event} has an unrecognised disposition "
                    f"{disposition!r} — use answered, deferred:<where>, or "
                    "noted:<why>"
                )
            rows.append(AskDisposition(
                event=event, disposition=disposition, label=label,
            ))
        return tuple(rows), None
    if isinstance(raw, (list, tuple)):
        # Forward-compatible: accepted when a caller constructs `fm` with a
        # real Python list directly (tests, a future YAML-capable parser) —
        # never produced by `protocol.parse_frontmatter` today (see module
        # docstring).
        for item in raw:
            if not isinstance(item, dict):
                return (), f"asks: entry {item!r} is not an event/disposition pair"
            event = str(item.get("event") or "").strip()
            disposition = str(item.get("disposition") or "").strip()
            label = str(item.get("label") or "").strip()
            if not event:
                return (), f"asks: entry {item!r} is missing its event"
            if not _valid_disposition(disposition):
                return (), (
                    f"asks: {event} has an unrecognised disposition "
                    f"{disposition!r} — use answered, deferred:<where>, or "
                    "noted:<why>"
                )
            rows.append(AskDisposition(
                event=event, disposition=disposition, label=label,
            ))
        return tuple(rows), None
    return (), f"asks: {raw!r} is neither a mapping nor a list of rows"


def _parse_owed(raw: Any) -> tuple[bool, tuple[OwedRow, ...], str | None]:
    if raw in (None, ""):
        return True, (), None
    if isinstance(raw, str) and raw.strip().lower() == "none":
        return True, (), None
    rows: list[OwedRow] = []
    if isinstance(raw, dict):
        for label, value in raw.items():
            if not isinstance(value, dict):
                return True, (), (
                    f"owed: {label!r} must carry a ref and a why "
                    "(e.g. `owed: {label}: {ref: ..., why: ...}`)"
                )
            ref = str(value.get("ref") or "").strip()
            why = str(value.get("why") or "").strip()
            where = str(value.get("where") or "").strip()
            if not ref or not why:
                return True, (), f"owed: {label!r} is missing ref or why"
            rows.append(OwedRow(label=str(label), ref=ref, why=why, where=where))
        return False, tuple(rows), None
    if isinstance(raw, (list, tuple)):
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                return True, (), (
                    f"owed: entry {index} must carry a ref and a why"
                )
            ref = str(item.get("ref") or "").strip()
            why = str(item.get("why") or "").strip()
            where = str(item.get("where") or "").strip()
            if not ref or not why:
                return True, (), f"owed: entry {index} is missing ref or why"
            rows.append(OwedRow(label=str(index), ref=ref, why=why, where=where))
        return False, tuple(rows), None
    return True, (), (
        f"owed: {raw!r} is neither 'none' nor a mapping of carried rows"
    )


def _valid_strand_disposition(value: str) -> bool:
    return bool(_STRAND_DISPOSITION_RE.match(value.strip()))


def _parse_strands(raw: Any) -> tuple[tuple[StrandDisposition, ...], str | None]:
    if raw in (None, ""):
        return (), None
    rows: list[StrandDisposition] = []
    if isinstance(raw, dict):
        for run, value in raw.items():
            run = str(run).strip()
            if not run:
                return (), "strands: an empty run key names nothing"
            if isinstance(value, dict):
                disposition = str(value.get("disposition") or "").strip()
            else:
                disposition = str(value if value is not None else "").strip()
            if not _valid_strand_disposition(disposition):
                return (), (
                    f"strands: {run} has an unrecognised disposition "
                    f"{disposition!r} — use handoff, converged, stopped, or "
                    "abandoned"
                )
            rows.append(StrandDisposition(run=run, disposition=disposition))
        return tuple(rows), None
    if isinstance(raw, (list, tuple)):
        # Forward-compatible, same rationale as `_parse_asks`: never produced
        # by `protocol.parse_frontmatter` today, but accepted when a caller
        # constructs `fm` with a real Python list directly.
        for item in raw:
            if not isinstance(item, dict):
                return (), f"strands: entry {item!r} is not a run/disposition pair"
            run = str(item.get("run") or "").strip()
            disposition = str(item.get("disposition") or "").strip()
            if not run:
                return (), f"strands: entry {item!r} is missing its run"
            if not _valid_strand_disposition(disposition):
                return (), (
                    f"strands: {run} has an unrecognised disposition "
                    f"{disposition!r} — use handoff, converged, stopped, or "
                    "abandoned"
                )
            rows.append(StrandDisposition(run=run, disposition=disposition))
        return tuple(rows), None
    return (), f"strands: {raw!r} is neither a mapping nor a list of rows"


def _parse_decisions(raw: Any) -> tuple[str, ...]:
    """``decisions:`` is reader-facing and never validated — carry whatever
    shape arrived rather than reject a free-text field for structure nobody
    checks."""
    if raw in (None, ""):
        return ()
    if isinstance(raw, dict):
        return tuple(f"{key}: {value}" for key, value in raw.items())
    if isinstance(raw, (list, tuple)):
        return tuple(str(item) for item in raw)
    return (str(raw),)


def _clean_text(raw: Any) -> str | None:
    if raw in (None, ""):
        return None
    text = str(raw).strip()
    return text or None


def parse_cut(fm: dict[str, Any]) -> tuple[CutDeclaration | None, str | None]:
    """Parse a ``cut:`` directive's frontmatter.

    Returns ``(declaration, error)``. On any refusal *error* is a one-line
    reason meant for a notice and *declaration* is ``None``. On success
    *error* is ``None``. A bare ``cut: true`` with no other fields parses to
    a minimal, legal :class:`CutDeclaration` — the design's own "stopping is
    a result" principle.
    """
    marker = str(fm.get("cut") if fm.get("cut") is not None else "").strip()
    if marker.lower() not in _MARKER_VALUES:
        return None, f"cut: {marker!r} is not a recognised marker (use `cut: true`)"

    unknown = sorted(set(fm.keys()) - _KNOWN_KEYS)
    if unknown:
        return None, (
            "cut: unrecognised field(s) " + ", ".join(unknown)
            + " — known fields are asks, decisions, produce, owed, spend, "
            "next, strands"
        )

    asks, asks_error = _parse_asks(fm.get("asks"))
    if asks_error:
        return None, asks_error

    owed_none, owed_rows, owed_error = _parse_owed(fm.get("owed"))
    if owed_error:
        return None, owed_error

    strands, strands_error = _parse_strands(fm.get("strands"))
    if strands_error:
        return None, strands_error

    produce_raw = fm.get("produce")
    produce: str | None = None
    if produce_raw not in (None, ""):
        produce = str(produce_raw).strip().lower()
        if produce not in _PRODUCE_VALUES:
            return None, f"produce: {produce_raw!r} must be 'attested' or 'none'"

    decisions = _parse_decisions(fm.get("decisions"))
    spend = _clean_text(fm.get("spend"))
    next_step = _clean_text(fm.get("next"))

    return CutDeclaration(
        asks=asks,
        decisions=decisions,
        produce=produce,
        owed_none=owed_none,
        owed=owed_rows,
        spend=spend,
        next=next_step,
        strands=strands,
    ), None


def durable_declaration(
    declaration: CutDeclaration,
    *,
    dissent: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """Return the bounded declaration subset safe to persist.

    ``produce`` is intentionally absent: the relic manifest and ledger's
    ``external_refs`` are its canonical record.  The parser remains lenient;
    an oversized declaration may still be validated and accepted.  At the
    persistence boundary it becomes one explicit omission marker rather than
    a clipped collection whose valid JSON shape would conceal missing facts.
    """
    counts = (
        ("asks", len(declaration.asks), PERSISTENCE_MAX_ASKS),
        ("owed", len(declaration.owed), PERSISTENCE_MAX_OWED),
        ("decisions", len(declaration.decisions), PERSISTENCE_MAX_DECISIONS),
        ("dissent", len(dissent), PERSISTENCE_MAX_DISSENT),
        ("strands", len(declaration.strands), PERSISTENCE_MAX_STRANDS),
    )
    exceeded = [name for name, count, limit in counts if count > limit]
    if exceeded:
        return {
            "declaration_version": 1,
            "omitted": True,
            "reason": "persistence row cap exceeded: " + ", ".join(exceeded),
        }

    asks = [
        {
            "event": row.event,
            "disposition": row.disposition,
            **({"label": row.label} if row.label else {}),
        }
        for row in declaration.asks
    ]
    owed = [
        {
            "label": row.label,
            "ref": row.ref,
            "why": row.why,
            **({"where": row.where} if row.where else {}),
        }
        for row in declaration.owed
    ]
    strands = [
        {"run": row.run, "disposition": row.disposition}
        for row in declaration.strands
    ]
    payload: dict[str, Any] = {
        "asks": asks,
        "owed": owed,
        "decisions": list(declaration.decisions),
        "spend_declared": declaration.spend,
        "next": declaration.next,
        "dissent": list(dissent),
        "strands": strands,
    }
    texts = [
        value
        for value in (
            *(field for row in asks for field in row.values()),
            *(field for row in owed for field in row.values()),
            *(field for row in strands for field in row.values()),
            *payload["decisions"],
            payload["spend_declared"],
            payload["next"],
            *payload["dissent"],
        )
        if isinstance(value, str)
    ]
    if any(len(value) > PERSISTENCE_MAX_TEXT_CHARS for value in texts):
        return {
            "declaration_version": 1,
            "omitted": True,
            "reason": "persistence text cap exceeded",
        }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    if len(encoded) > PERSISTENCE_MAX_JSON_BYTES:
        return {
            "declaration_version": 1,
            "omitted": True,
            "reason": "persistence JSON byte cap exceeded",
        }
    return {"declaration_version": 1, **payload}

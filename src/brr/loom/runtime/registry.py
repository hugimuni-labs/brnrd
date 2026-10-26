"""The facts a body's lifecycle writes or reads, declared once.

One row per kind, read top-down:

- resource: what the fact is about.
- fields: the keys of ``data``. ``name?`` may be absent; every other one is
  written, though its value may be null.
- writer: the role in ``by`` (``loom:<install>``, ``strand:<id>``, ``person:<name>``).
- scope: ``global`` is true on any machine that reads it. ``machine`` is
  only meaningful where it was written: it names a process, a local clock or
  a local gauge reading.
- once: at most one per strand and generation, so its id is the kind, the
  strand and the generation, and a second write with other content is a
  ledger conflict.

A resource in ``CLOSED`` is declared in full: ``admit`` refuses, at the
ledger's one write path, a kind under its name that has no row here, a writer
the row does not name, and fields the row does not list. The body's table
folds ``body`` rows by strand and generation; the other closed resources
(wishes, pull request receipts, loom starts) are declared for that check
alone. Rows of any other resource are here because the body's table reads
them; their writers are not checked and kinds beside them stay undeclared.
"""

from __future__ import annotations

from dataclasses import dataclass

from brr.daemon2.facts import Fact


class UndeclaredFact(ValueError):
    """A fact of a closed resource that the registry does not allow."""


@dataclass(frozen=True)
class Kind:
    kind: str
    resource: str
    fields: tuple[str, ...]
    writer: tuple[str, ...]
    scope: str
    once: bool

    def names(self) -> tuple[str, ...]:
        return tuple(field.rstrip("?") for field in self.fields)

    def required(self) -> tuple[str, ...]:
        return tuple(field for field in self.fields if not field.endswith("?"))


def _kind(kind: str, resource: str, fields: str, writer: str, scope: str, once: bool = False) -> Kind:
    return Kind(kind, resource, tuple(fields.split()), tuple(writer.split()), scope, once)


CLOSED = ("body", "wish", "pr", "loom")
ROLES = ("loom", "strand", "person")
SCOPES = ("global", "machine")

REGISTRY = (
    #     kind                resource   fields                                              writer         scope
    _kind("body.pending",      "body",    "strand gen shell core",                            "loom",        "machine", once=True),
    _kind("body.parked",       "body",    "strand gen shell core window left_pct until",      "loom",        "machine"),
    _kind("body.started",      "body",    "strand gen pid shell core readme lstart",          "loom",        "machine", once=True),
    _kind("body.leaving",      "body",    "strand gen deadline",                              "loom",        "machine"),
    _kind("body.stayed",       "body",    "strand gen",                                       "loom",        "machine"),
    _kind("body.stopped",      "body",    "strand gen pid why",                               "loom",        "machine", once=True),
    _kind("body.unidentified", "body",    "strand gen pid why",                               "loom",        "machine"),
    _kind("body.exited",       "body",    "strand gen code why? failure_origin? failure_letters?",                             "loom",        "global",  once=True),
    _kind("wish",              "wish",    "wish says from line thread parent?",                "strand person", "global"),
    _kind("wish.read",         "wish",    "wish reading by",                                   "strand person", "global"),
    _kind("wish.result",       "wish",    "wish kind ref line delivered cmd?",                  "strand loom",   "global"),
    _kind("wish.verdict",      "wish",    "wish as evidence ref",                              "strand person", "global"),
    _kind("wish.left",         "wish",    "wish steps gen same?",                              "strand",        "global"),
    _kind("left.asked",        "wish",    "strand gen wishes",                                 "strand",        "global"),
    _kind("thread.reopened",   "wish",    "thread wish was closed? why?",                      "strand",        "global"),
    _kind("pr.opened",         "pr",      "thread repo number url head",                       "strand",        "global"),
    _kind("pr.merged",         "pr",      "thread repo number head merge_commit",              "strand",        "global"),
    # Read by the body's table, owned elsewhere, not enforced here.
    _kind("lease",             "thread",  "thread strand gen install router_gen shell core repos parent?", "loom", "global", once=True),
    _kind("released",          "thread",  "thread strand gen why install router_gen?",        "loom",        "global",  once=True),
    _kind("shown",             "letter",  "strand gen ids",                                   "loom strand", "global"),
    _kind("molt",              "letter",  "strand gen why fresh?",                            "strand",      "global"),
    _kind("attempt.ended",     "attempt", "thread strand gen ids cause code dispositions evidence", "loom",  "global",  once=True),
    _kind("port.drained",      "port",    "thread strand gen",                                "loom",        "global",  once=True),
    _kind("loom.started",      "loom",    "install pid commit? loaded",                       "loom",        "global"),
    _kind("loom.degraded",     "loom",    "install step error where since",                   "loom",        "machine"),
    _kind("loom.recovered",    "loom",    "install step since count last",                    "loom",        "machine"),
    _kind("loom.moved",        "loom",    "install from to merges started",                   "loom",        "global"),
)

KINDS = {row.kind: row for row in REGISTRY}


def resource_of(kind: str) -> str:
    """A declared kind's resource; otherwise the name before the first dot."""
    row = KINDS.get(kind)
    return row.resource if row else kind.split(".", 1)[0]


def problems(fact: Fact) -> list[str]:
    """What the registry has against ``fact``. A kind with no row has one problem."""
    row = KINDS.get(fact.kind)
    if row is None:
        return [f"kind {fact.kind} is not declared"]
    found = []
    role = fact.by.split(":", 1)[0]
    if role not in row.writer:
        found.append(f"{fact.kind} is written by {' or '.join(row.writer)}, not {role}")
    missing = [name for name in row.required() if name not in fact.data]
    if missing:
        found.append(f"{fact.kind} lacks {', '.join(missing)}")
    extra = sorted(set(fact.data) - set(row.names()))
    if extra:
        found.append(f"{fact.kind} does not declare {', '.join(extra)}")
    return found


def admit(fact: Fact) -> None:
    """Refuse a fact of a closed resource the registry has anything against.

    A wish is born for someone: the fact that creates one (its id is the
    wish) cites a letter in ``says`` or names a ``parent`` wish; one a
    resident makes of a person (an ask) names the wish it unblocks, so it
    always has a ``parent``. Any other
    ``wish`` fact is a touch, id ``wish:…``; one with neither shape would be
    a birth the rule never saw, since the fold creates a wish from whichever
    fact names it first. Whether a touched wish exists is the verb's check:
    admit sees one fact, not the ledger. The facts already in the ledger are
    not checked: only ``append`` admits.
    """
    if resource_of(fact.kind) not in CLOSED:
        return
    found = problems(fact)
    if fact.kind == "wish" and not found:
        if fact.id == fact.data["wish"]:
            if not fact.data["says"] and not fact.data.get("parent"):
                found.append("a new wish needs --say <letter> or --parent <wish>")
            elif fact.data["from"] == "resident" and not fact.data.get("parent"):
                found.append("an ask needs the wish it unblocks: ask --for <wish>")
        elif not fact.id.startswith("wish:"):
            found.append("a wish fact is a birth (id = the wish) or a touch (id wish:…)")
    if found:
        raise UndeclaredFact(f"registry refused {fact.id}: " + "; ".join(found))

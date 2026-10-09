"""The attention view: a pure fold. Never a letter, never a channel."""

from __future__ import annotations

from dataclasses import dataclass

from brr.daemon2.facts import Fact, union

from .home import Home
from .ledger import append
from .project import fold, owed


@dataclass(frozen=True)
class Row:
    kind: str
    subject: str
    why: str

    def render(self) -> str:
        return f"{self.kind} · {self.subject} · {self.why}"


def poison_ids(facts: list[Fact]) -> set[str]:
    """Letters shown to two bodies that then died, unless a person cleared them.

    The count is the one ``project.attempts`` makes: a ``body.died`` whose
    body had been ``shown`` the letter. A clear sets the floor at the count
    so far, so the letter wakes again, and two further deaths quarantine it
    once more.
    """
    counts: dict[str, int] = {}
    floor: dict[str, int] = {}
    open_ids: dict[str, set[str]] = {}
    for fact in fold(facts).accepted:
        if fact.kind == "attention.cleared":
            letter = fact.data.get("letter")
            if isinstance(letter, str) and letter:
                floor[letter] = counts.get(letter, 0)
            continue
        strand = fact.data.get("strand")
        if not isinstance(strand, str):
            continue
        if fact.kind == "body.started":
            open_ids[strand] = set()
        elif fact.kind == "shown" and strand in open_ids:
            open_ids[strand].update(str(item) for item in fact.data.get("ids") or ())
        elif fact.kind == "body.died" and strand in open_ids:
            for letter_id in open_ids.pop(strand):
                counts[letter_id] = counts.get(letter_id, 0) + 1
    return {
        letter_id for letter_id, count in counts.items()
        if count - floor.get(letter_id, 0) >= 2
    }


def clear_letter(home: Home, letter_id: str) -> None:
    """A person puts a poison letter back. Idempotent on the letter id."""
    if not letter_id:
        raise ValueError("attention --clear needs a letter id")
    append(home, Fact(
        kind="attention.cleared", by=f"person:{home.install_id()}",
        data={"letter": letter_id}, id=f"attention.cleared:{letter_id}",
    ))


def actionable(facts: list[Fact], thread: str) -> list[Fact]:
    """Owed letters a body should see. A poison letter stays owed and is not listed."""
    bad = poison_ids(facts)
    return [fact for fact in owed(facts, thread) if str(fact.data.get("id")) not in bad]


def _attention_row(fact: Fact) -> Row | None:
    if fact.kind != "attention":
        return None
    parts = fact.id.split(":")
    kind = parts[1] if len(parts) > 1 else "attention"
    subject = fact.data.get("letter") or fact.data.get("thread") or "-"
    return Row(kind, str(subject), str(fact.data.get("why") or ""))


def view(facts: list[Fact]) -> list[Row]:
    """One row per condition. Attention facts step 1 already wrote are folded in."""
    ordered = union(list(facts))
    rows = [row for fact in ordered if (row := _attention_row(fact)) is not None]
    seen = {(row.kind, row.subject) for row in rows}
    for letter_id in sorted(poison_ids(ordered)):
        if ("poison", letter_id) in seen:
            continue
        rows.append(Row("poison", letter_id, "shown to two bodies that died"))
    current = None
    for fact in ordered:
        if fact.kind == "router" and "gen" in fact.data:
            gen = int(fact.data["gen"])
            if current is None or gen > current:
                current = gen
    intended: set[str] = set()
    sent: set[str] = set()
    for fact in ordered:
        if fact.kind != "speech":
            continue
        key = str(fact.data.get("key") or "")
        if not key:
            continue
        if fact.data.get("state") == "sent":
            sent.add(key)
        elif fact.data.get("state") == "intended":
            intended.add(key)
    for key in sorted(intended - sent):
        rows.append(Row("maybe-sent", key, "attempted, never confirmed"))
    for fact in ordered:
        if fact.kind != "letter" or fact.data.get("to") != "channel:fake":
            continue
        key = str(fact.data.get("id") or fact.id)
        if key in sent or key in intended:
            continue
        raw = fact.data.get("router_gen")
        if current is None or raw is None:
            continue
        try:
            grant = int(raw)
        except (TypeError, ValueError):
            continue
        if grant < current:
            rows.append(Row(
                "stale-speech", key, f"drafted under router gen {grant}",
            ))
    return rows

"""Wishes are a projection of four facts, never a second store."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable

from brr.daemon2.facts import Fact, union


@dataclass
class Wish:
    id: str
    says: list = field(default_factory=list)
    sender: str = "person"
    line: str = ""
    thread: str = ""
    parent: str = ""
    reading: str = ""
    reads: list[dict] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)
    verdict: dict | None = None
    at: str = ""
    origin: str = "derived"

    @property
    def state(self) -> str:
        if self.verdict:
            return self.verdict["as"]
        if any(result["delivered"] for result in self.results):
            return "delivered"
        if self.results:
            return "granting"
        if any(read["by"] == "person" for read in self.reads):
            return "scoped"
        return "read" if self.reads else "heard"

    @property
    def open(self) -> bool:
        return self.state not in {"accepted", "dropped"}

    @property
    def derived_unconfirmed(self) -> bool:
        return self.origin == "derived" and self.state in {"heard", "read"}

    def unconfirmed(self, now: datetime | None = None) -> bool:
        """Age is assessed by the reader; silence never becomes acceptance."""
        return (self.state == "delivered" or self.derived_unconfirmed) and (
            (now or datetime.now(timezone.utc)) - datetime.fromisoformat(self.at)
            > timedelta(days=60))


def wishes(facts: Iterable[Fact]) -> dict[str, Wish]:
    ordered = union(facts)
    letters = {str(f.data.get("id", f.id)): f for f in ordered if f.kind == "letter"}
    found: dict[str, Wish] = {}
    for fact in ordered:
        if fact.kind not in {"wish", "wish.read", "wish.result", "wish.verdict"}:
            continue
        d = fact.data
        ident = d["wish"]
        if fact.kind == "wish":
            wish = found.setdefault(ident, Wish(ident))
            for say in d["says"]:
                if say not in wish.says:
                    wish.says.append(list(say))
            wish.sender, wish.line, wish.thread = d["from"], d["line"], d["thread"]
            wish.parent = d.get("parent") or wish.parent
            wish.verdict = None
        else:
            if ident not in found:
                continue
            wish = found[ident]
            if fact.kind == "wish.read":
                wish.reading = d["reading"]
                wish.reads.append(dict(d, at=fact.at))
            elif fact.kind == "wish.result":
                wish.results.append(dict(d, at=fact.at))
            else:
                wish.verdict = dict(d, at=fact.at)
        wish.at = fact.at
    for wish in found.values():
        def own_say(say):
            letter = letters.get(say[0])
            if letter is None:
                return False
            return (letter.by.startswith("person:") if wish.sender == "person"
                    else letter.by.startswith("strand:"))
        wish.origin = "said" if wish.says and all(map(own_say, wish.says)) else "derived"
    return found


def _short(value: str, size: int) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= size else text[:size - 1] + "…"


def wish_line(wish: Wish) -> str:
    state = wish.state
    if wish.origin == "derived":
        state += ", derived"
    if wish.unconfirmed():
        state += ", unconfirmed"
    ref = wish.results[-1]["ref"] if wish.results else "-"
    return f"{_short(wish.id, 48)} · {state} · {_short(wish.line, 160)} · {_short(ref, 120)}"


def wake_wishes(facts: Iterable[Fact], thread: str) -> str:
    rows = [w for w in wishes(facts).values() if w.thread == thread and w.open]
    if not rows:
        return ""
    rows.sort(key=lambda w: (w.sender != "resident", w.id))
    section = "## Wishes\n" + "".join(wish_line(w) + "\n" for w in rows[:20])
    if len(rows) > 20:
        section += f"+{len(rows) - 20} more · wishes --thread {thread}\n"
    return section


def insert_wishes(prompt: str, section: str) -> str:
    """Follow the Plan, or the thread README when it has no Plan yet."""
    start = prompt.find("## Plan\n")
    if start < 0:
        start = prompt.find("## threads/")
    end = prompt.find("\n## ", start + 3) if start >= 0 else -1
    if end < 0:
        return prompt + "\n" + section
    return prompt[:end + 1] + section + prompt[end + 1:]

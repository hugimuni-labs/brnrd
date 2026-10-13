"""A due ``when:`` is one letter, ``letter:timer:<thread>:<when>``.

The instant is the whole of once. The sentence is built only from the thread
id and that instant, so a second append of the same id is the ledger's no-op.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from brr.daemon2.facts import Fact

from .home import Home
from .ledger import LedgerConflict, append
from .lifecycle import CLOSED, main_snapshot

_INSTANT = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_DURATION = re.compile(r"^\+(\d+)([mhd])$")
_UNITS = {"m": "minutes", "h": "hours", "d": "days"}


def parse_instant(value: str) -> datetime | None:
    if not isinstance(value, str) or not _INSTANT.fullmatch(value):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def letter_id(thread: str, when: str) -> str:
    return f"letter:timer:{thread}:{when}"


def sentence(thread: str, when: str) -> str:
    return (
        f"The time {when} has come for thread {thread}. "
        f"when: still holds {when} until this body changes it."
    )


def column(when: object, thread: str, facts, now: datetime) -> str:
    """The instant, ``spent`` once it has passed and its letter is in the ledger, or ``—``."""
    raw = when.strip() if isinstance(when, str) else ""
    if not raw:
        return "—"
    due = parse_instant(raw)
    if due is not None and due <= now and any(fact.id == letter_id(thread, raw) for fact in facts):
        return "spent"
    return raw


def stored_when(text: str, now: datetime | None = None) -> str:
    """The absolute instant to store, or empty for ``clear``."""
    text = text.strip()
    if text == "clear":
        return ""
    if parse_instant(text) is not None:
        return text
    match = _DURATION.fullmatch(text)
    if match is None:
        raise ValueError(
            "when: expected YYYY-MM-DDTHH:MM:SSZ, +<n>m, +<n>h, +<n>d, or clear"
        )
    now = now if now is not None else datetime.now(timezone.utc)
    delta = timedelta(**{_UNITS[match.group(2)]: int(match.group(1))})
    return (now + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def set_when_line(text: str, value: str) -> str:
    """Replace ``when:`` or insert it before the frontmatter closes."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError("when: frontmatter missing")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise ValueError("when: frontmatter unclosed")
    written = f"when: {value}\n" if value else 'when: ""\n'
    for i in range(1, end):
        if lines[i].split(":", 1)[0].strip() == "when":
            lines[i] = written
            break
    else:
        lines.insert(end, written)
    return "".join(lines)


def fire(home: Home, facts: list, armed: bool) -> list:
    """Append each due thread's letter. Only the router-lease holder is armed."""
    if not armed:
        return facts
    now = datetime.now(timezone.utc)
    seen = {fact.id for fact in facts}
    for thread, data in main_snapshot(home).items():
        if data.get("status") in CLOSED:
            continue
        raw = data.get("when") if isinstance(data.get("when"), str) else ""
        due = parse_instant(raw)
        if due is None or due > now:
            continue
        ident = letter_id(thread, raw)
        if ident in seen:
            continue
        try:
            stored = append(home, Fact(
                kind="letter", by=f"loom:{home.install_id()}", id=ident,
                data={
                    "id": ident, "from": "loom", "to": f"thread:{thread}",
                    "body": sentence(thread, raw),
                    "label": {"taint": False, "audience": ["self"]},
                },
            ))
        except LedgerConflict:
            continue
        facts.append(stored)
        seen.add(stored.id)
    return facts

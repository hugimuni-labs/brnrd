"""Time enters ownership as an observation on an accepted letter."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from brr.daemon2.facts import Fact

from .home import Home
from .ledger import append
from .project import fold

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


def stored_when(text: str, now: datetime | None = None) -> str:
    """The absolute instant to store on a letter."""
    text = text.strip()
    if parse_instant(text) is not None:
        return text
    match = _DURATION.fullmatch(text)
    if match is None:
        raise ValueError(
            "send --at: expected YYYY-MM-DDTHH:MM:SSZ, +<n>m, +<n>h, +<n>d"
        )
    now = now if now is not None else datetime.now(timezone.utc)
    try:
        delta = timedelta(**{_UNITS[match.group(2)]: int(match.group(1))})
        return (now + delta).strftime("%Y-%m-%dT%H:%M:%SZ")
    except OverflowError:
        raise ValueError("send --at: duration is outside the supported calendar") from None


def observe_due(home: Home, facts: list, armed: bool) -> list:
    """Check only held facts; only the router-lease holder writes observations.

    An ordinary tick reads no file or git here. A newly due letter uses the
    ledger's normal durable append, with an id derived from the letter.
    """
    if not armed:
        return facts
    seen = {fact.id for fact in facts if fact.kind == "letter.due"}
    waiting = {fact.id for fact in facts if fact.kind == "letter"
               and fact.data.get("at") and f"due:{fact.id}" not in seen}
    if not waiting:
        return facts
    state = fold(facts)
    now = datetime.now(timezone.utc)
    for letter in state.accepted:
        if letter.kind != "letter" or letter.id not in waiting or letter.id in state.handled:
            continue
        due = parse_instant(letter.data["at"])
        if due is None or due > now:
            continue
        stored = append(home, Fact(
            kind="letter.due", by=f"loom:{home.install_id()}", id=f"due:{letter.id}",
            data={"letter": letter.id}, after=(letter.id,),
        ))
        facts.append(stored)
    return facts

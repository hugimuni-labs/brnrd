"""Room sense: a person's channel measured against that person's own past.

Every letter a person sends is scored on the letters of the last ``WINDOW``
seconds that end at it, against a baseline of their letters older than
``QUIET`` (so a burst never becomes its own baseline). Four measures, each
off or not:

- ``burst``: more letters in the window than in any window of the baseline;
- ``short``: the window's median length under a third of the baseline's;
- ``spelling``: words never seen in that person's earlier letters at over
  twice the rate of the last ``RECENT`` seconds (no dictionary, so any
  language works);
- ``door``: half the window came through a door that carried under 5% of
  the last ``RECENT`` seconds, so a door the person settles into stops
  counting by itself.

Words are never matched against a list: the 10-28 night that this was built
for moved the length, rhythm, spelling and door of the letters, and left the
swear and destroy-verb rates at their usual percentiles.

A shift opens at a letter whose window holds ``OPEN_MIN`` letters or more
with ``OPEN_AT`` measures off, stays through letters with two, and closes at
the first letter with one or none, or ``EXPIRE`` seconds after its last
off letter. No baseline (under ``FLOOR_LETTERS`` letters or ``FLOOR_DAYS``
days), no shift. ``room_line`` renders a live shift as one status line.
"""

from __future__ import annotations

import bisect
import re
import statistics
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

from .ledger import kind_facts

WINDOW = 20 * 60
QUIET = 2 * 3600
RECENT = 7 * 86400
EXPIRE = 6 * 3600
FLOOR_LETTERS = 100
FLOOR_DAYS = 7
OPEN_MIN = 3
OPEN_AT = 3
SPELLING_MIN_WORDS = 20
RARE_DOOR = 0.05

_WORD = re.compile(r"[^\W\d_]{3,}")


@dataclass(frozen=True)
class Letter:
    id: str
    at: float
    door: str
    length: int
    words: int
    unseen: int


@dataclass(frozen=True)
class Shift:
    person: str
    since: Letter
    last: Letter
    measures: tuple[str, ...]
    window: int
    drivers: tuple[str, ...]


def _door(data: dict) -> str:
    origin = str(data.get("origin") or "")
    return "board" if ":board-" in origin else str(data.get("platform") or "?")


def letters(facts) -> dict[str, list[Letter]]:
    """Each person's letters in order, with what scoring needs and never their text."""
    rows = sorted((f for f in kind_facts(facts, "source")
                   if str(f.data.get("from") or "").startswith("person:")), key=lambda f: f.at)
    seen: dict[str, Counter] = {}
    out: dict[str, list[Letter]] = {}
    for fact in rows:
        person = str(fact.data["from"]).removeprefix("person:")
        text = str(fact.data.get("text") or "")
        words = [w.lower() for w in _WORD.findall(text)]
        vocabulary = seen.setdefault(person, Counter())
        unseen = sum(1 for w in words if not vocabulary[w])
        vocabulary.update(words)
        origin = str(fact.data.get("origin") or fact.id)
        out.setdefault(person, []).append(Letter(
            origin.rsplit(":", 1)[-1], datetime.fromisoformat(fact.at).timestamp(),
            _door(fact.data), len(text), len(words), unseen))
    return out


def _busiest(times: list[float]) -> int:
    best, start = 0, 0
    for end, at in enumerate(times):
        while at - times[start] > WINDOW:
            start += 1
        best = max(best, end - start + 1)
    return best


def score(history: list[Letter], index: int) -> tuple[list[str], list[Letter], tuple[Letter, ...]] | None:
    """The measures off at ``history[index]``, its window and the letters driving them; None without a baseline."""
    letter = history[index]
    times = [x.at for x in history]
    cut = bisect.bisect_left(times, letter.at - QUIET)
    base = history[:cut]
    if len(base) < FLOOR_LETTERS or base[-1].at - base[0].at < FLOOR_DAYS * 86400:
        return None
    window = history[bisect.bisect_left(times, letter.at - WINDOW):index + 1]
    recent = base[bisect.bisect_left(times, letter.at - RECENT):]
    typical = statistics.median(x.length for x in base)
    words, unseen = sum(x.words for x in recent), sum(x.unseen for x in recent)
    rate = unseen / words if words else 0.0
    doors = Counter(x.door for x in recent)
    rare = {d for d in {x.door for x in window} if doors[d] / max(1, len(recent)) < RARE_DOOR}
    w_words, w_unseen = sum(x.words for x in window), sum(x.unseen for x in window)
    off = []
    if len(window) > _busiest(times[:cut]):
        off.append("burst")
    if statistics.median(x.length for x in window) < typical / 3:
        off.append("short")
    if w_words >= SPELLING_MIN_WORDS and rate and w_unseen / w_words > 2 * rate:
        off.append("spelling")
    if sum(1 for x in window if x.door in rare) * 2 >= len(window):
        off.append("door")
    # A driver is a letter off on two of its own measures: one long letter on a new door is not one.
    drivers = tuple(x for x in window if (x.length < typical / 3) + (x.door in rare)
                    + bool(x.words and rate and x.unseen / x.words > 2 * rate) >= 2)
    return off, window, drivers


def _run(person: str, history: list[Letter], start: int) -> tuple[list[Shift], Shift | None]:
    closed, open_ = [], None
    for index in range(start, len(history)):
        letter = history[index]
        if open_ is not None and letter.at - open_.last.at > EXPIRE:
            closed.append(open_)
            open_ = None
        scored = score(history, index)
        if scored is None:
            continue
        off, window, drivers = scored
        if open_ is None:
            if len(window) >= OPEN_MIN and len(off) >= OPEN_AT:
                # The letters before the opening one were read and left alone: they don't drive it.
                open_ = Shift(person, letter, letter, tuple(off), len(window), (letter.id,) * (letter in drivers))
        elif len(off) >= 2:
            open_ = Shift(person, open_.since, letter, tuple(dict.fromkeys(open_.measures + tuple(off))),
                          max(open_.window, len(window)),
                          tuple(dict.fromkeys(open_.drivers + tuple(x.id for x in drivers if x.at >= open_.since.at))))
        else:
            closed.append(open_)
            open_ = None
    return closed, open_


def shifts(person: str, history: list[Letter], start: int = 0) -> list[Shift]:
    """Every shift from ``history[start]`` on, each at its widest: the replay a test reads."""
    closed, open_ = _run(person, history, start)
    return closed + ([open_] if open_ is not None else [])


def live(facts, now: float | None = None) -> list[Shift]:
    """Shifts still open now: no calm letter closed them and their last off letter is under ``EXPIRE`` old."""
    now = time.time() if now is None else now
    out = []
    for person, history in letters(facts).items():
        times = [x.at for x in history]
        history = history[:bisect.bisect_right(times, now)]
        start = bisect.bisect_left(times, now - 2 * EXPIRE)
        _closed, open_ = _run(person, history, start)
        if open_ is not None and now - open_.last.at <= EXPIRE:
            out.append(open_)
    return out


_CACHE: list = [None, None]


def room_line(facts, now: float | None = None) -> str:
    """``room: …`` for each live shift, or ""."""
    now = time.time() if now is None else now
    key = (id(facts), len(kind_facts(facts, "source")), int(now // 60))
    if _CACHE[0] == key:
        return _CACHE[1]
    parts = []
    for shift in live(facts, now):
        since = datetime.fromtimestamp(shift.since.at, timezone.utc).strftime("%H:%MZ")
        drivers = " ".join(shift.drivers[:8]) + (" …" if len(shift.drivers) > 8 else "")
        parts.append(f"{shift.person}'s channel off baseline since {since} ({', '.join(shift.measures)}; "
                     f"up to {shift.window} letters in {WINDOW // 60}m; driven by {drivers})")
    line = ("room: " + "; ".join(parts) + "; irreversible acts they ordered ask them first, in one sentence"
            if parts else "")
    _CACHE[:] = [key, line]
    return line

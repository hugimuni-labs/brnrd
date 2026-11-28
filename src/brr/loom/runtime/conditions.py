"""Recurrence without a clock: the tick's sweeps as conditions a seat parks on.

Each condition is a query over facts the tick already holds. A ``Firing`` is
one subject for which it is true; its key carries the episode, so a wish
touched again and stale again is a new firing. Its notice id derives from
``<name>|<key>`` (``notice_id("cond", …)``), so it is written once on any
install, and writing it is the record: nothing else is stored.

- ``signal-unread``: an owed loom notice unsettled for ``UNREAD``.
- ``ask-moot``: an open ask whose parent wish ended.
- ``wish-stale``: an open wish untouched for ``STALE``, with no live park.
- ``room-kept``: a room the reaper keeps for leftovers in a checkout whose
  thread has since merged a pull request on that repo. Commits already on
  main need no condition: the reaper's own leftovers check reaps the room.
- ``pass-due``: once per local day, at or after ``pass_hour`` (default 8;
  ``off`` sends none). The key is that local date, so a missed hour fires
  on the first tick after it the same day, and a day the machine slept
  through is not made up. Always told to the home thread: a seat and a
  ``cond:`` park do not move it, and neither is required. The notice
  carries the week's pace for that thread's shell and up to three
  candidate moves. ``BRNRD_LOOM_PASS=off`` is test-only and sends none.

A firing goes to its thread when a seat holds it; otherwise to the thread of
a live ``cond:`` park that names the condition (the inbox's); with neither it
is not told yet, and is told on the first tick a park exists, since the
condition still holds. ``pass-due`` skips that routing. A ``cond:`` park
stands: ``waits.outcome`` never resolves it, so one park keeps receiving the
firings routed to it until it is re-read.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

NAMES = ("signal-unread", "ask-moot", "wish-stale", "room-kept", "pass-due")
#: A loom notice owed this long without a disposition is unread.
UNREAD = timedelta(hours=24)
#: An open wish with no fact and no left this long, and no park, is stale.
STALE = timedelta(days=7)
#: Seconds between evaluations: the queries fold the ledger.
EVERY = 60
#: Parent states that prompt a review of the standing ask.
ENDED = frozenset({"delivered", "accepted", "dropped", "expired", "answered"})

_KEPT_IN = re.compile(r"^(?:leftovers in|a stash in) ([^:\s]+)")


@dataclass(frozen=True)
class Firing:
    name: str
    key: str
    thread: str
    what: str
    can: str

    @property
    def subject(self) -> str:
        return f"{self.name}|{self.key}"


def parse(text: str) -> tuple[str, ...]:
    """The names a ``cond:`` condition lists, sorted; ``all`` is every one."""
    listed = [part.strip() for part in text.split(",") if part.strip()]
    if listed == ["all"]:
        return tuple(sorted(NAMES))
    unknown = [name for name in listed if name not in NAMES]
    if not listed or unknown:
        raise ValueError(f"--wait cond:{text}: expected all or a comma list of {', '.join(NAMES)}")
    return tuple(sorted(set(listed)))


def _instant(text) -> datetime | None:
    from .waits import _instant as instant
    return instant(str(text or ""))


def _age(since: datetime, now: datetime) -> str:
    from .idle import _age as age
    return age((now - since).total_seconds())


def _first(body: str) -> str:
    from .wishes import _short
    return _short(next((line for line in str(body).splitlines() if line.strip()), ""), 100)


def signal_unread(facts, state, now: datetime) -> list[Firing]:
    from .home import notice_id
    from .ledger import kind_facts
    own = notice_id("cond", "").rpartition("-")[0] + "-"
    rows = []
    for letter in kind_facts(facts, "letter"):
        data = letter.data
        ident = str(data.get("id") or letter.id)
        to = str(data.get("to") or "")
        if (data.get("from") != "loom" or not letter.by.startswith("loom:") or data.get("notice")
                or not to.startswith("thread:") or ident.startswith(own) or ident in state.handled):
            continue
        at = _instant(letter.at)
        if at is None or now - at < UNREAD:
            continue
        rows.append(Firing("signal-unread", ident, to[len("thread:"):],
                           f"signal-unread: {ident} to {to} has had no disposition for {_age(at, now)}: "
                           f"{_first(data.get('body', ''))}",
                           f"read {ident}, then file it or dismiss it with a why: a reply or a note re: {ident}"))
    return rows


def ask_moot(found: dict) -> list[Firing]:
    rows = []
    for ask in found.values():
        if not ask.open or ask.sender != "resident" or not ask.parent:
            continue
        parent = found.get(ask.parent)
        if parent is None or parent.state not in ENDED:
            continue
        rows.append(Firing("ask-moot", f"{ask.id}|{parent.state}", ask.thread,
                           f"ask-moot: {ask.id} (\"{_first(ask.line)}\") asks for {parent.id}, which is {parent.state}",
                           f"Your question {ask.id} is still open. If it still needs an answer, keep it or move it "
                           f"with `brnrd runtime wish {ask.id} --parent <wish-id>`. "
                           "Drop it with a reason only if its answer is no longer needed."))
    return rows


def wish_stale(found: dict, now: datetime) -> list[Firing]:
    from .waits import live
    parked = {wish.id for wish, _read in live(found)}
    rows = []
    for wish in found.values():
        if not wish.open or wish.sender == "resident" or wish.state == "delivered" or wish.id in parked:
            continue
        touched = max([wish.at, wish.born, *(left["at"] for left in wish.lefts)])
        at = _instant(touched)
        if at is None or now - at < STALE:
            continue
        left = "; ".join(wish.left["steps"]) if wish.left else "no left yet"
        rows.append(Firing("wish-stale", f"{wish.id}|{touched}", wish.thread,
                           f"wish-stale: {wish.id} (\"{_first(wish.line)}\", {wish.state}) on thread:{wish.thread} "
                           f"untouched for {_age(at, now)}, no park; last left: {_first(left)}",
                           f"give {wish.id} a --left, park it on what it waits for, drop it with a why, or ask"))
    return rows


def room_kept(home, facts) -> list[Firing]:
    from .ledger import kind_facts
    from .project import sender_threads
    from .reap import KEPT
    try:
        kept = json.loads((home.root / KEPT).read_text()).get("kept") or {}
    except (OSError, ValueError, AttributeError):
        return []
    if not kept:
        return []
    threads = sender_threads(facts)
    leased: dict[str, str] = {}
    for lease in kind_facts(facts, "lease"):
        leased.setdefault(str(lease.data.get("strand")), lease.at)
    merged = kind_facts(facts, "pr.merged")
    rows = []
    for strand, why in sorted(kept.items()):
        match = _KEPT_IN.match(str(why))
        thread = threads.get(strand)
        if not match or not thread:
            continue
        repo = match.group(1)
        since = leased.get(strand, "")
        pr = next((f for f in reversed(merged) if f.data.get("thread") == thread
                   and f.data.get("repo") == repo and f.at >= since), None)
        if pr is None:
            continue
        number = pr.data.get("number")
        rows.append(Firing("room-kept", f"{strand}|{repo}#{number}", thread,
                           f"room-kept: {strand} (thread:{thread}) is kept for {why}, and {repo}#{number} "
                           f"merged as {str(pr.data.get('merge_commit') or '')[:8]}",
                           f"if what it holds is in #{number}, reap it: `brnrd runtime reap {strand} --force "
                           f"--why \"in {repo}#{number}\"`; else land what is not"))
    return rows


def _pass_hour(home) -> int | None:
    """The local hour the pass is due, or None when ``pass_hour`` is off.

    ``BRNRD_LOOM_PASS=off`` is test-only, the way ``BRNRD_LOOM_CLOCK_SKEW_S`` is:
    the suite sets it so a live loop does not wake the home thread after the
    hour. Production leaves it unset, and the config key is the switch.
    """
    import os
    if os.environ.get("BRNRD_LOOM_PASS") == "off":
        return None
    from .config import load_config
    value = load_config(home.root).pass_hour
    if value == "off":
        return None
    return int(value)


def _local_now(now: datetime) -> datetime:
    """``now`` on the machine's local clock, as ``body_limits`` reads a reset."""
    return datetime.fromtimestamp(now.timestamp()).astimezone()


def _pass_lines(home, facts) -> str:
    """The week's pace for the home thread's shell, then up to three candidate moves.

    Either helper failing still leaves the pass: the schedule only opens the door.
    A notice already written for today never reaches here, so this does not
    refresh a gauge every tick of the afternoon.
    """
    from . import idle
    from .config import load_config
    from .home import HOME_THREAD
    from .loom import _log, _turn_pace
    lines: list[str] = []
    try:
        said = _turn_pace(home, facts, load_config(home.root))(HOME_THREAD)
    except Exception as exc:  # noqa: BLE001 - the pass is the notice; the pace is an extra line
        _log(home, f"pass: pace skipped ({type(exc).__name__}: {exc})")
        said = ""
    if said:
        lines.append(said)
    try:
        lines.extend(idle.candidates(home, facts, HOME_THREAD)[:3])
    except Exception as exc:  # noqa: BLE001 - same: a candidate failure must not swallow the pass
        _log(home, f"pass: candidates skipped ({type(exc).__name__}: {exc})")
    return "\n".join(lines)


def _pass_schedule(home, facts, now: datetime, known):
    """Return the next unsent pass's local date and scheduled UTC instant."""
    from . import idle
    from .config import load_config
    from .home import HOME_THREAD, notice_id
    from .loom import pace_shell

    hour = _pass_hour(home)
    if hour is None:
        return None
    shell = pace_shell(facts, load_config(home.root), HOME_THREAD)
    if shell and idle.share_for(home, shell) <= 0:
        return None
    day = _local_now(now).date()
    while notice_id("cond", f"pass-due|{day.isoformat()}") in known:
        day += timedelta(days=1)
    # Resolve the zone at the scheduled wall time, including a DST change
    # between now and the next pass.
    at = datetime(day.year, day.month, day.day, hour).astimezone(timezone.utc)
    return day.isoformat(), at


def next_pass(home, facts, now: datetime) -> datetime | None:
    """The drive's next calendar wake, with a missed hour due immediately."""
    from .ledger import LedgerView
    known = facts.by_id if isinstance(facts, LedgerView) else {f.id for f in facts}
    schedule = _pass_schedule(home, facts, now, known)
    return max(now, schedule[1]) if schedule else None


def pass_due(home, facts, now: datetime, known: set[str]) -> list[Firing]:
    """One firing for today's local date, once the local hour has been reached."""
    from .home import HOME_THREAD

    schedule = _pass_schedule(home, facts, now, known)
    if schedule is None or now < schedule[1]:
        return []
    day, _ = schedule
    what = (f"pass: {day}. Read the last pass's line and check it; read the board; "
            "pick one move (two if both cheap) across every thread; act; "
            "write one line saying what you picked and why.")
    return [Firing("pass-due", day, HOME_THREAD, what, _pass_lines(home, facts))]


def firings(home, facts, found: dict, state, now: datetime, known: set[str] | None = None) -> list[Firing]:
    return [*signal_unread(facts, state, now), *ask_moot(found), *wish_stale(found, now),
            *room_kept(home, facts), *pass_due(home, facts, now, known or set())]


def parks(home, facts, found: dict) -> tuple[dict[str, str], dict[str, set[str]]]:
    """Each condition’s original destination and any thread-owned subscriptions."""
    from .waits import parker, unresolved
    rows = sorted(unresolved(found, facts), key=lambda row: row[1]["at"])
    first: dict[str, str] = {}
    subscribed: dict[str, set[str]] = {}
    for wish, read in rows:
        on = str(read["wait"].get("on") or "")
        if not on.startswith("cond:"):
            continue
        thread = parker(home, facts, wish, read)
        for name in parse(on[5:]):
            first.setdefault(name, thread)
            if read.get("thread"):
                subscribed.setdefault(name, set()).add(thread)
    return first, subscribed


_LAST: dict[str, float] = {}


def observe_conditions(home, facts, armed: bool, now: datetime | None = None, force: bool = False):
    """Write one ``cond`` notice per new firing that has somewhere to go; only the lease holder writes."""
    if not armed:
        return facts
    key = str(home.root)
    if not force and time.monotonic() - _LAST.get(key, -EVERY) < EVERY:
        return facts
    _LAST[key] = time.monotonic()
    from .home import notice_id
    from .ledger import LedgerView
    from .loom import notice
    from .project import fold
    from .wishes import told
    now = now or datetime.now(timezone.utc)
    found = told(facts)
    state = fold(facts)
    parked, subscribed = parks(home, facts, found)
    held = set(state.holder)
    ids = facts.by_id if isinstance(facts, LedgerView) else {f.id for f in facts}
    for firing in firings(home, facts, found, state, now, ids):
        if notice_id("cond", firing.subject) in ids:
            continue
        # pass-due always wakes the home thread. The notice is the wake: route
        # starts a body for an owed letter on an unheld thread, the same path
        # every other notice uses.
        primary = firing.thread if firing.name == "pass-due" or firing.thread in held else parked.get(firing.name)
        if primary is None:
            continue
        others = () if firing.name == "pass-due" else sorted(subscribed.get(firing.name, set()) - {primary})
        # Keep the original firing id as its completion marker. Write it last
        # so a partial fanout can retry without duplicating existing notices.
        for thread in others:
            notice(home, facts, "cond", f"{firing.subject}|thread:{thread}", firing.what, firing.can, thread=thread)
        notice(home, facts, "cond", firing.subject, firing.what, firing.can, thread=primary)
    return facts

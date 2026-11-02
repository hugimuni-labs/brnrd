"""What an idle body could move, for the keepalive and a quiet face (w-i1xohccy9ht0).

The keepalive said "nothing new arrived: stop", and a body took the hook's
word over stance's "fuel left and a queue ahead: take its top". When the
week's fuel is ahead of pace, the keepalive and a quiet-face boundary carry
the top candidate acts instead, each drawn from what is already derived:
the person's order, a red or stale own pull request, the stalest wait, a
wish never read, a child thread quiet for days with nothing in hand. The
body takes one or says in one line why not. Behind pace they carry nothing:
resting is then the right call. The loom writes ``port/in/idle.md``; the
jack reads it and the gauge, never the ledger.
"""

from __future__ import annotations

import re
import time
from datetime import datetime

from .usage import _reset

TOP = 3
WEEK = 7 * 86400.0
STALE_PR = 86400.0
QUIET_THREAD = 3 * 86400.0
HEAD = "idle? fuel ahead of pace"


def ahead(value, now: float | None = None) -> bool:
    """The week's fuel left exceeds the week's time left: spending now stays on budget.

    An unknown or refreshing gauge, or one with no week window, is not ahead.
    """
    week = next((w for w in (value or {}).get("windows") or ()
                 if isinstance(w, dict) and w.get("name") == "week"), None)
    if week is None or not _reset(week):
        return False
    now = time.time() if now is None else now
    to_go = min(max(_reset(week) - now, 0.0) / WEEK, 1.0)
    try:
        return float(week["left_pct"]) / 100 > to_go
    except (KeyError, TypeError, ValueError):
        return False


def pace(value, now: float | None = None) -> str:
    """``week 92% left, 78% of it to go`` for the line that says why the body is asked."""
    week = next(w for w in value["windows"] if w.get("name") == "week")
    now = time.time() if now is None else now
    to_go = min(max(_reset(week) - now, 0.0) / WEEK, 1.0)
    return f"week {float(week['left_pct']):g}% left, {to_go:.0%} of it to go"


def _age(seconds: float) -> str:
    from .threads import _age as age
    return age(seconds)


def _epoch(at: str) -> float:
    return datetime.fromisoformat(str(at).replace("Z", "+00:00")).timestamp()


def candidates(home, facts, thread: str, now: float | None = None) -> list[str]:
    """Up to ``TOP`` acts this thread could move, most owed first, one line each."""
    from .lifecycle import main_snapshot
    from .queues import queues
    from .receipts import receipts
    from .threads import pr_cards, thread_table
    from .waits import live
    from .wishes import _short, told, waits_on
    now = time.time() if now is None else now
    found = told(facts)
    parked = sorted((row for row in live(found) if row[0].thread == thread), key=lambda row: row[1]["at"])
    held = {wish.id for wish, _ in parked}
    out: list[str] = []
    q = queues(found, waits_on(found, main_snapshot(home)))
    top = next((w for w in [*q.mine, *q.idle] if w.thread == thread and w.id not in q.blocked
                and w.id not in held), None)
    if top is not None:
        out.append(f'{top.id} is this thread\'s top in their order ({top.state}): "{_short(top.line, 60)}"'
                   f' — `wish {top.id} --pick "<why>"` and build its next step')
    cards = pr_cards(home)
    for r in receipts(facts, home.install_id()).get(thread, ()):
        card = cards.get(f"{r.repo}#{r.number}") or {}
        if r.merge_commit or card.get("state") != "open":
            continue
        try:
            age = now - _epoch(card["pushed"])
        except (KeyError, TypeError, ValueError):
            age = 0.0
        if card.get("ci") == "red":
            out.append(f"#{r.number} is red: read the failing run, fix and push")
        elif age >= STALE_PR:
            out.append(f"#{r.number} {card.get('ci') or 'pending'}, untouched {_age(age)}: "
                       "push what it lacks, ask for its review, or close it")
    for wish, read in parked[:1]:
        reading = read["reading"].removeprefix("waiting:").strip()
        out.append(f'{wish.id} parked {_age(now - _epoch(read["at"]))} on "{_short(reading, 50)}": '
                   "check whether it turned; re-park, or move it")
    for wish in sorted(found.values(), key=lambda w: (w.born, w.id)):
        if wish.thread == thread and wish.open and wish.state == "heard" and not (top and wish.id == top.id):
            out.append(f'{wish.id} is heard, never read: "{_short(wish.line, 60)}" — '
                       f'`wish {wish.id} --read "<what it asks>"`')
    for row in thread_table(home, facts, f"thread:{thread}"):
        match = re.fullmatch(r"idle (\d+)d", row["now"])
        if (row["status"] in {"settled", "retired"} or not match or row["owed"] or row.get("prs")
                or row.get("waits") or int(match[1]) * 86400 < QUIET_THREAD):
            continue
        out.append(f"thread {row['thread']} quiet {match[1]}d with nothing in hand: "
                   "write to it with its next step, or settle it")
    return out[:TOP]


def render(lines: list[str]) -> str:
    """The block a quiet face and ``idle.md`` carry: one unit to the jack's news."""
    if not lines:
        return ""
    return (f"{HEAD}: take one (or say in one line why not); 'nothing new arrived' is no reason to stop\n"
            + "".join(f"  - {line}\n" for line in lines))


def keepalive(lines: str, value, stop_news: str) -> str:
    """The keepalive's reason when the week is ahead of pace and the thread has acts to move."""
    items = "".join(line + "\n" for line in lines.splitlines() if line.startswith("  - "))
    return (f"cache keepalive, fuel ahead of pace ({pace(value)}): nothing new arrived, which is no reason"
            " to stop; take one of these and work it, or end with one line saying why not\n" + items + stop_news)

"""Timing on existing shown receipts, measured where the body gets the letter."""

import json
import math
from datetime import datetime

from .bodies import fact_for
from .dispositions import is_person, letter_index
from .home import check_thread_id, home_for
from .ledger import LedgerView, kind_facts, read_facts
from .refusals import Parser


def add_reading(fact, thread, event, at, previous_gap, letters):
    rows = []
    for letter in letters:
        arrival = datetime.fromisoformat(letter.at).timestamp()
        sender = str(letter.data.get("from", ""))
        kind = "person" if is_person(letter) else "loom" if sender == "loom" else "thread"
        rows.append({"letter": str(letter.data.get("id") or letter.id), "sender": kind,
                     "arrival_at": letter.at, "seconds": round(max(0, at - arrival), 3)})
    if rows:
        fact.data["wait"] = {"thread": thread, "event": event, "boundary_at": at,
                             "previous_gap": previous_gap, "letters": rows}


def add_port_reading(fact, raw, facts):
    """Malformed optional timing must never reject a shown receipt."""
    try:
        boundary = json.loads(raw)
        at = boundary["at"]
        gap = boundary["previous_gap"]
        event = boundary["event"]
        displayed = boundary["displayed"]
        if (event not in ("wake", "post", "stop", "attend") or not math.isfinite(at)
                or (gap is not None and (not math.isfinite(gap) or gap < 0))
                or not isinstance(displayed, list) or not set(displayed) <= set(fact.data["ids"])):
            return
    except (TypeError, ValueError, KeyError, OverflowError):
        return
    lease = fact_for(facts, "lease", fact.data["strand"], fact.data["gen"])
    if lease:
        index = facts.named_letters if isinstance(facts, LedgerView) else letter_index(facts)
        add_reading(fact, lease.data["thread"], event, at, gap,
                    (index[ident] for ident in displayed if ident in index))


def readings(facts, thread, limit):
    receipts = [f for f in kind_facts(facts, "shown")
                if f.data.get("wait", {}).get("thread") == thread]
    first = {}
    for fact in sorted(receipts, key=lambda f: (f.data["wait"]["boundary_at"], f.id)):
        wait = fact.data["wait"]
        for row in wait["letters"]:
            key = fact.data["strand"], fact.data["gen"], row["letter"]
            first.setdefault(key, (row, wait))
    latest = list(reversed(list(first.values())))[:limit]
    return sorted(latest, key=lambda item: item[0]["sender"] != "person")


def main(argv):
    parser = Parser(verb="letter-wait", prog="brnrd runtime letter-wait")
    parser.add_argument("thread")
    parser.add_argument("--limit", type=int, default=20, help="number of recent readings (default 20)")
    parser.add_argument("--home")
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit must be positive")
    try:
        thread = check_thread_id(args.thread)
    except ValueError as exc:
        parser.error(str(exc))
    for row, boundary in readings(read_facts(home_for(args.home)), thread, args.limit):
        gap = boundary["previous_gap"]
        previous = "—" if gap is None else f"{gap:.3f}s"
        print(f"{row['letter']} · {row['sender']} · wait {row['seconds']:.3f}s · "
              f"{boundary['event']} · previous boundary {previous}")
    return 0

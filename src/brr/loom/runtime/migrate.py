"""``migrate-plan``: move what README Plan rows still carry onto their wishes, once.

The one reader of Plan rows meant to outlive the others by a single cut-over
(threads/no-plan). It writes facts and never edits a README: the self commit
that deletes every ``## Plan`` lands after it, then the row readers go.

- results: ``landed_results`` once more (ids hash wish and ref, so a rerun is none);
- says: a person letter a row cites beside an open wish that does not say it, as a
  stamp dated at the letter, so it reads as what the person said then, not a new steer;
- lefts: an open wish not yet delivered with no left gets its own thread's rows
  that carry no receipt, in row order, each marked ``(from README)``.

Every fact is ``by loom:migrate`` and its id is derived, so running it twice writes
nothing new, and a left a body already said is never written over.
"""

from __future__ import annotations

import argparse
import hashlib
import sys

from brr.daemon2.facts import Fact
from .dispositions import letter_index, resolve
from .ledger import append, read_facts
from .lifecycle import main_snapshot
from .owners import plan_citations
from .wishes import landed_results, receipt, wishes

BY = "loom:migrate"
MARK = " (from README)"


def _key(*parts: str) -> str:
    return hashlib.sha256(" ".join(parts).encode()).hexdigest()[:12]


def _person(letter: Fact) -> bool:
    return str(letter.data.get("from", "")).startswith(("p-", "person:"))


def plan(facts: list[Fact], main: dict) -> list[Fact]:
    """Every fact the migration still owes, in the order it writes them."""
    found = wishes(facts)
    letters = letter_index(facts)
    out = list(landed_results(found, main, BY))
    said = {w.id: {resolve(letters, s.letter) or s.letter for s in w.says}
            | {resolve(letters, i) or i for st in w.stamps for i in st["letters"]} for w in found.values()}
    steps: dict[str, list[str]] = {}
    for thread, data in sorted(main.items()):
        for box, row in data.get("plan_rows") or ():
            cites = plan_citations(row)
            for ident in cites:
                wish = found.get(ident)
                if wish is None or not wish.open:
                    continue
                for raw in cites:
                    letter = resolve(letters, raw)
                    if letter is None or letter in said[ident] or not _person(letters[letter]):
                        continue
                    said[ident].add(letter)
                    out.append(Fact(kind="wish", by=BY, id="wish:migrated-" + _key(ident, letter),
                                    at=letters[letter].at, data={
                                        "wish": ident, "says": [{"letter": letter, "offset": 0}],
                                        "from": wish.sender, "line": wish.line, "thread": wish.thread}))
                if (thread == wish.thread and wish.left is None and wish.state != "delivered"
                        and receipt(thread, row, box) is None):
                    step = " ".join((row.rpartition(" · re: ")[0] or row).split())
                    steps.setdefault(ident, []).append(step + MARK)
    for ident, rows in steps.items():
        out.append(Fact(kind="wish.left", by=BY, id="wish.left:migrated-" + _key(ident),
                        data={"wish": ident, "steps": rows, "gen": 0}))
    return out


def describe(fact: Fact) -> str:
    d = fact.data
    if fact.kind == "wish.result":
        return f"result {d['wish']} {d['kind']}:{d['ref']}"
    if fact.kind == "wish":
        return f"say    {d['wish']} {d['says'][0]['letter']} (at {fact.at[:16]})"
    return f"left   {d['wish']} " + " · ".join(d["steps"])


def cmd_migrate_plan(args) -> int:
    from .verbs_wishes import _context
    try:
        home = _context(args)[0]
        owed = plan(read_facts(home), main_snapshot(home))
        for fact in owed:
            if args.write:
                append(home, fact)
            print(describe(fact))
        kinds = [f.kind for f in owed]
        print(f"{'wrote' if args.write else 'would write'}: {kinds.count('wish.result')} results, "
              f"{kinds.count('wish')} says, {kinds.count('wish.left')} lefts"
              + ("" if args.write or not owed else " · rerun with --write"))
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _migrate_plan(argv) -> int:
    parser = argparse.ArgumentParser(prog="python -m brr.loom.runtime migrate-plan",
                                     description="Move what README Plan rows carry onto their wishes (dry run by default).")
    parser.add_argument("--home")
    parser.add_argument("--room")
    parser.add_argument("--write", action="store_true", help="append the facts; without it, only list them")
    return cmd_migrate_plan(parser.parse_args(argv))

"""Propose the wish behind each open thread that has none. Prints; writes nothing.

A thread opened before ``send --to thread:<new>`` required ``--wish`` has no
``opened-for`` and often no wish living on it. This reads what the ledger and
main already say about why it was opened and prints, per such thread, the
``wish new`` that would record it:

- the letter that opened it: the first letter to ``thread:<t>``, citing its
  ``re`` (the letter the opener was answering), else
- the opener's Plan rows that name the thread and cite letters (`` · re: ``).

Every proposal is derived: the loom's reading of where a thread came from,
not anyone's words. Inbox reviews the list; a separate run stamps it.

    python scripts/propose_thread_wishes.py --home <loom home>
"""
import argparse
import re
import shlex
import sys
from pathlib import Path

from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import read_facts
from brr.loom.runtime.lifecycle import main_snapshot
from brr.loom.runtime.snapshot import _SENTENCE
from brr.loom.runtime.threads import opened_for
from brr.loom.runtime.wishes import wishes


def proposals(home: Home) -> list[tuple[str, str, list[str], str]]:
    """(thread, line, letters to cite, where they came from) per thread with no wish behind it."""
    facts = read_facts(home)
    living = {w.thread for w in wishes(facts).values()}
    first = {}
    for fact in sorted((f for f in facts if f.kind == "letter"), key=lambda f: f.at):
        to = fact.data.get("to", "")
        if to.startswith("thread:"):
            first.setdefault(to[len("thread:"):], fact)
    snap = main_snapshot(home)
    found = []
    for thread, data in sorted(snap.items()):
        if data.get("status") != "open" or opened_for(data) or thread in living:
            continue
        line = _SENTENCE.split(data.get("headline", "").strip(), 1)[0] or thread
        opening = first.get(thread)
        if opening is not None and opening.data.get("re"):
            found.append((thread, line, [opening.data["re"]], f"opening letter {opening.id}"))
            continue
        opener = (data.get("opened-by") or "").removeprefix("thread:")
        named = re.compile(rf"(?<![\w-]){re.escape(thread)}(?![\w-])")
        cites = []
        for _done, row in snap.get(opener, {}).get("plan_rows") or ():
            text, _, refs = row.rpartition(" · re: ")
            if text and named.search(text):
                cites += [r.strip() for r in refs.split(",") if r.strip() and not r.strip().startswith("w-")]
        found.append((thread, line, list(dict.fromkeys(cites)),
                      f"thread:{opener} Plan rows" if cites else "nothing to cite"))
    return found


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--home", type=Path, required=True)
    args = parser.parse_args(argv)
    for thread, line, cites, source in proposals(Home(args.home)):
        if not cites:
            print(f"# {thread}: derived, {source}; ask its opener")
            continue
        says = " ".join(f"--say {shlex.quote(c)}" for c in cites)
        print(f"# {thread}: derived from {source}")
        print(f"python -m brr.loom.runtime wish new --thread {thread} --line {shlex.quote(line)} {says}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

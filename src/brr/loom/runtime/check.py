"""Replay the ownership invariant at every causal ledger prefix."""
from __future__ import annotations

import argparse
import sys

from brr.daemon2.facts import union
from .home import Home, is_channel
from .ledger import read_facts
from .project import fold


def violations(facts, main):
    """Return coordinates, never repair a ledger while checking it."""
    from .owners import letter_owners
    problems = []
    rows = union(list(facts))
    for prefix in range(1, len(rows) + 1):
        current = rows[:prefix]
        table = letter_owners(current, main)
        accepted = {f.id for f in fold(current).accepted if f.kind == 'letter'}
        for ident in accepted:
            fate = table.get(ident)
            if fate is None:
                problems.append((prefix, ident, 'accepted letter has no table row'))
            elif not fate.done and fate.owner not in {'loom', 'body', 'channel', 'opener', 'operator'}:
                problems.append((prefix, ident, 'owed letter has no unique owner'))
            elif fate.owner in {'body', 'opener'} and not fate.thread:
                problems.append((prefix, ident, 'thread owner has no thread'))
            elif not fate.reason:
                problems.append((prefix, ident, 'ownership has no reason'))
        sent = {f.data.get('key') for f in current if f.kind == 'speech' and f.data.get('state') == 'sent'}
        for reply in fold(current).accepted:
            re = reply.data.get('re')
            if reply.kind == 'letter' and is_channel(reply.data.get('to')) and re in table:
                if table[re].done and reply.id not in sent:
                    # A later explicit note can retire an undelivered letter deliberately.
                    notes = [f for f in current if f.kind == 'note' and f.data.get('re') == re]
                    if not notes and table[re].reason not in {'worker settled by its opener'}:
                        problems.append((prefix, re, 'retired before send receipt; channel still owes reply'))

    return problems


def run(argv):
    parser = argparse.ArgumentParser(prog='brnrd loom check')
    parser.add_argument('--home', required=True)
    args = parser.parse_args(argv)
    home = Home(args.home)
    from .lifecycle import main_snapshot
    problems = violations(read_facts(home), main_snapshot(home))
    for prefix, letter, why in problems:
        print(f'prefix {prefix} · {letter} · {why}')
    if not problems:
        print('letter ownership clean at every ledger prefix')
    return int(bool(problems))

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
    problems = []
    rows = union(list(facts))
    for prefix in range(1, len(rows) + 1):
        current = rows[:prefix]
        state = fold(current)
        sent = {f.data.get('key') for f in current
                if f.kind == 'speech' and f.data.get('state') == 'sent'}
        for reply in state.accepted:
            original = reply.data.get('re')
            if (reply.kind == 'letter' and is_channel(reply.data.get('to'))
                    and original in state.handled and reply.id not in sent):
                problems.append((prefix, original, 'retired before send receipt; channel still owes reply'))
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

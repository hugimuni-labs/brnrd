#!/usr/bin/env python3
"""Record bounded real-body lifecycle drives in a scratch home (never CI)."""
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import inject_letter, read_facts
from brr.loom.runtime.loom import tick_once
from brr.loom.runtime.router import Router
from brr.loom.runtime.selfrepo import init_self, git, _commit
from brr.loom.runtime.lifecycle import read_thread

p = argparse.ArgumentParser()
p.add_argument('--output', type=Path, required=True)
p.add_argument('--core', default='gpt-6-luna')
p.add_argument('--timeout', type=float, default=240)
p.add_argument('--case', choices=['person', 'worker', 'failed-start', 'all'], default='all')
args = p.parse_args()
args.output.mkdir(parents=True, exist_ok=True)


def drive(name, instructions, failed=False):
    root = Path(tempfile.mkdtemp(prefix='loom-letter-' + name + '-'))
    init_self(root, person='test')
    home = Home(root)
    folder = home.thread_dir('inbox')
    folder.mkdir(parents=True, exist_ok=True)
    (folder/'README.md').write_text('---\nid: inbox\nstatus: open\ntense: plan\nopened-by: person:test\n---\n# Lifecycle drive\n' + instructions + '\n')
    (folder/'wait').write_text('0\n')
    git(root/'self', 'add', 'threads')
    _commit(root/'self', 'Seed lifecycle drive.\n\nLoom-Label: taint=0; audience=self')
    if failed:
        (root/'self/core/loom/wake').write_text('#!/bin/sh\necho deliberate-start-failure >&2\nexit 3\n')
        git(root/'self', 'add', 'core/loom/wake')
        _commit(root/'self', 'Record deliberately failed wake preparation.')
    original = inject_letter(root, to='thread:inbox', body=instructions, sender='p-test')
    router, bodies = Router(home), {}
    deadline = time.monotonic() + args.timeout
    try:
        while time.monotonic() < deadline:
            tick_once(home, bodies, 'codex', args.core, router)
            rows = read_facts(home)
            ends = [f for f in rows if f.kind in {'body.exited','attempt.ended'}]
            if failed and len(ends) >= (2 if any(f.kind == 'attempt.ended' for f in ends) else 1):
                break
            if not failed and ends and not bodies and read_thread(home,'inbox').get('status') == 'settled':
                break
            time.sleep(.15)
        else:
            raise RuntimeError('drive deadline; home retained at ' + str(root))
        rows = read_facts(home)
        # Facts and main snapshot contain only invented test identities. No account or credentials.
        record = {'drive':name, 'body':'codex/'+args.core, 'original':original,
                  'facts':[f.row() for f in rows], 'main':{}}
        for path in (root/'self/threads').glob('*/README.md'):
            record['main'][path.parent.name] = read_thread(home,path.parent.name)
        (args.output/(name+'.json')).write_text(json.dumps(record,indent=2)+'\n')
        print(name, 'home:',root, 'rows:',len(rows), 'ends:',len(ends), flush=True)
        print(' '.join(f.kind for f in rows), flush=True)
    finally:
        for body in bodies.values():
            if body.proc and body.proc.poll() is None:
                os.killpg(body.proc.pid,signal.SIGTERM)
                body.proc.wait(timeout=10)
            body.close_log()


if args.case in {'person', 'all'}:
    drive('person', 'Answer this letter with exactly RECEIVED using send --re <id> --to channel:fake, then settle inbox --why answered. Call the hand jack between acts. Do no other work.')
if args.case in {'worker', 'all'}:
    drive('worker', 'Open a worker with send --to thread:worker --body codex/'+args.core+' "Reply WORKED to thread:inbox with --re the received id, then end. Do no other work." Note this original letter with --re its id --to thread:inbox --note dispatched. Call the hand jack between acts until WORKED arrives. Settle worker --why complete, then settle inbox --why complete. Do no other work.')
if args.case in {'failed-start', 'all'}:
    drive('failed-start', 'Record a deliberately failed start.', failed=True)

"""Profile a disconnected copy: PYTHONPATH=src python bench/loom_home_idle.py HOME.

Copies ledger/, loom/, self/ and empty room outboxes. Never profiles the
original home. No real workers, connector children, forge or gauge collectors run.
"""
from __future__ import annotations

import argparse
import cProfile
import fcntl
import json
from contextlib import ExitStack
from collections import Counter
import pstats
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from brr.loom.runtime import connector, loom, usage, router as router_module
from brr.loom.runtime.project import fold
from brr.loom.runtime.home import Home
from brr.loom.runtime.router import Router


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('home', type=Path)
    parser.add_argument('--ticks', type=int, default=20)
    parser.add_argument('--start-checks', action='store_true',
                        help='include locked start authority checks; never advance a body')
    parser.add_argument('--full-tick', action='store_true',
                        help='advance fake held bodies, read cached gauges, poll empty connectors')
    parser.add_argument('--extra', type=int, default=0, help='add inert renewal history in the disconnected copy')
    parser.add_argument('--changed-ticks', type=int, default=3, help='measure ticks with one durable renewal append each')
    parser.add_argument('--profile-out', type=Path, help='also dump the profiled ticks for pstats')
    args = parser.parse_args()
    print(f'tick source: {loom.__file__}')
    with tempfile.TemporaryDirectory(prefix='loom-home-idle-') as temporary:
        root = Path(temporary)
        for name in ('ledger', 'loom', 'self'):
            shutil.copytree(args.home / name, root / name,
                            ignore=shutil.ignore_patterns('connectors', 'loop.lock'))
        for remote in subprocess.check_output(['git', '-C', str(root / 'self'), 'remote'], text=True).split():
            subprocess.run(['git', '-C', str(root / 'self'), 'remote', 'remove', remote], check=True)
        # Outbox enumeration is measured, but no live command or process handle
        # is copied. Starts below are disabled regardless of a thread's seat.
        for room in (args.home / 'rooms').iterdir():
            if room.is_dir():
                (root / 'rooms' / room.name / 'port/out').mkdir(parents=True)
        config = root / 'loom/config.toml'
        lines = config.read_text().splitlines()
        connectors = next((line for line in lines if line.startswith('connectors =')), 'connectors = []')
        config.write_text('\n'.join(
            line for line in lines
            if not line.lstrip().startswith(('connectors =', 'forge_token =', 'relay_state ='))
        ) + '\nconnectors = []\n')
        from brr.daemon2.facts import Fact
        from brr.loom.runtime.ledger import append, read_facts
        if args.extra:
            extra = root / 'ledger/facts/benchmark-history'
            extra.mkdir(parents=True)
            (extra / 'loom.jsonl').write_text(''.join(
                json.dumps(Fact(kind='router.renewed', by='loom:benchmark',
                                id=f'benchmark:{n}', data={'gen': 0},
                                hlc=(n, 0, 'benchmark')).row()) + '\n'
                for n in range(args.extra)))
        home, bodies = Home(root), {}
        initial_ids = {f.id for f in read_facts(home)}
        router = Router(home)
        real_start = loom._start
        def no_advance(home, strand, thread, gen, bodies, facts, **kwargs):
            return facts
        def no_start(home, strand, thread, gen, bodies, facts, choice, **kwargs):
            if args.start_checks:
                return real_start(home, strand, thread, gen, bodies, facts, choice, **kwargs)
            return facts
        def forbidden(*args, **kwargs):
            raise RuntimeError('disconnected benchmark forbids spawn, signal, send or collector')
        with ExitStack() as guards:
            guards.enter_context(patch.object(loom, '_spawn', forbidden))
            guards.enter_context(patch.object(loom, '_end_group', forbidden))
            guards.enter_context(patch.object(router_module, 'speak', forbidden))
            inputs = None
            if args.full_tick:
                config.write_text(config.read_text().replace('connectors = []', connectors))
                for thread, (strand, gen) in fold(router.reader.read(home)).holder.items():
                    room = home.room(strand)
                    (room / 'port/in').mkdir(parents=True, exist_ok=True)
                    lock = guards.enter_context((room / '.body').open('a+'))
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                for path in (root / 'loom/usage').glob('*.json'):
                    reading = json.loads(path.read_text())
                    reading['at'] = time.time()
                    path.write_text(json.dumps(reading))
                guards.enter_context(patch.object(usage, '_refresh', forbidden))
                with patch.object(connector.subprocess, 'Popen',
                                  return_value=SimpleNamespace(poll=lambda: None)):
                    inputs = connector.Inputs(home)
                    inputs.step()
            else:
                guards.enter_context(patch.object(usage, 'reading', return_value={'windows': []}))
                guards.enter_context(patch.object(loom, '_start', no_start))
                guards.enter_context(patch.object(loom, '_advance_body', no_advance))
            def tick():
                if inputs is not None:
                    inputs.step()
                    loom._source_fingerprint()
                loom.tick_once(home, bodies, 'fake', 'haiku', router)
            tick()
            # Hold lease time fixed to compare identical idle work, excluding
            # timer-triggered renewal appends from the profiled interval.
            if router.lease is not None:
                fixed = router.lease.until - router.config.router_ttl
                router.clock = router.authority.clock = lambda: fixed
            tick()
            if args.full_tick:
                print(f'full tick: {len(router.reader.read(home))} facts, '
                      f'{len(fold(router.reader.read(home)).holder)} fake holders, '
                      f'{len(list((root / "rooms").iterdir()))} empty rooms, '
                      f'{len(inputs.names)} supervisors')
            print('warmup added kinds:', dict(Counter(f.kind for f in read_facts(home) if f.id not in initial_ids)))
            profile = cProfile.Profile()
            started = time.process_time()
            with profile:
                for _ in range(args.ticks):
                    tick()
            print(f'{args.ticks} ticks: {time.process_time() - started:.3f}s CPU')
            if args.profile_out:
                profile.dump_stats(args.profile_out)
            stats = pstats.Stats(profile).strip_dirs().sort_stats('cumulative')
            stats.print_stats(25)
            stats.print_stats(r'_ingest|observe_due|notice|letter_owners|speak_out|_boundary|thread_table|reading')
            started = time.process_time()
            for _ in range(args.ticks):
                tick()
            print(f'{args.ticks} unprofiled ticks: {time.process_time() - started:.3f}s CPU')
            maximum = max((f.hlc[0] for f in read_facts(home) if f.hlc), default=0)
            started = time.process_time()
            for n in range(args.changed_ticks):
                append(home, Fact(kind='router.renewed', by=f'loom:{home.install_id()}',
                                  id=f'benchmark:new:{n}', data={'gen': 0},
                                  hlc=(maximum + n + 1, 0, home.install_id())))
                tick()
            print(f'{args.changed_ticks} changed ticks including writer: {time.process_time() - started:.3f}s CPU')
            started = time.process_time()
            for _ in range(args.ticks):
                loom._source_fingerprint()
            print(f'{args.ticks} source fingerprints: {time.process_time() - started:.3f}s CPU')
            # Measure the configured supervisors' empty polling path, with all
            # connector state removed and subprocess creation replaced.
            config.write_text(config.read_text().replace('connectors = []', connectors))
            with patch.object(connector.subprocess, 'Popen',
                              return_value=SimpleNamespace(poll=lambda: None)):
                inputs = connector.Inputs(home)
                inputs.step()
                started = time.process_time()
                for _ in range(args.ticks):
                    inputs.step()
                print(f'{args.ticks} empty connector polls ({len(inputs.names)} supervisors): '
                      f'{time.process_time() - started:.3f}s CPU')


if __name__ == '__main__':
    main()

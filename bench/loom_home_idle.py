"""Profile a disconnected copy: PYTHONPATH=src python bench/loom_home_idle.py HOME.

Copies ledger/, loom/, self/ and empty room outboxes. Never profiles the
original home. No workers, connectors, forge or shell gauges can be reached.
"""
from __future__ import annotations

import argparse
import cProfile
import pstats
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from brr.loom.runtime import connector, loom, usage
from brr.loom.runtime.home import Home
from brr.loom.runtime.router import Router


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('home', type=Path)
    parser.add_argument('--ticks', type=int, default=20)
    parser.add_argument('--start-checks', action='store_true',
                        help='include locked start authority checks; never advance a body')
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
        home, bodies = Home(root), {}
        router = Router(home)
        real_start = loom._start
        def no_advance(home, strand, thread, gen, bodies, facts, **kwargs):
            return facts
        def no_start(home, strand, thread, gen, bodies, facts, choice, **kwargs):
            if args.start_checks:
                return real_start(home, strand, thread, gen, bodies, facts, choice, **kwargs)
            return facts
        with patch.object(usage, 'reading', return_value={'windows': []}), \
                patch.object(loom, '_start', no_start), \
                patch.object(loom, '_advance_body', no_advance):
            loom.tick_once(home, bodies, 'fake', 'haiku', router)
            # Hold lease time fixed to compare identical idle work, excluding
            # timer-triggered renewal appends from the profiled interval.
            if router.lease is not None:
                fixed = router.lease.until - router.config.router_ttl
                router.clock = router.authority.clock = lambda: fixed
            loom.tick_once(home, bodies, 'fake', 'haiku', router)
            profile = cProfile.Profile()
            started = time.process_time()
            with profile:
                for _ in range(args.ticks):
                    loom.tick_once(home, bodies, 'fake', 'haiku', router)
            print(f'{args.ticks} ticks: {time.process_time() - started:.3f}s CPU')
            stats = pstats.Stats(profile).strip_dirs().sort_stats('cumulative')
            stats.print_stats(25)
            stats.print_stats(r'_ingest|observe_due|notice|letter_owners|speak_out')
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

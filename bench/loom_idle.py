"""Profile idle ticks: PYTHONPATH=src python bench/loom_idle.py [--ticks 20]."""
from __future__ import annotations

import argparse
import cProfile
import json
import inspect
import pstats
import tempfile
import time
from pathlib import Path

from brr.daemon2.facts import Fact
from brr.loom.runtime.home import Home
from brr.loom.runtime.loom import tick_once
from brr.loom.runtime.router import Router
from brr.loom.runtime.selfrepo import init_self


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ticks', type=int, default=20)
    args = parser.parse_args()
    print(f"tick source: {inspect.getsourcefile(tick_once)}")
    with tempfile.TemporaryDirectory(prefix='loom-idle-') as temporary:
        root = Path(temporary) / 'home'
        init_self(root, person='test')
        home = Home(root)
        path = home.facts_dir() / 'loom.jsonl'
        path.write_text(''.join(json.dumps(Fact(
            kind='router.renewed', by='loom:abcd', id=f'renew-{n}',
            data={'install': 'abcd', 'gen': 1, 'until': n},
            hlc=(n, 0, 'abcd'),
        ).row()) + '\n' for n in range(8000)))
        router, bodies = Router(home), {}
        tick_once(home, bodies, 'fake', 'haiku', router)
        router.clock = lambda fixed=router.lease.until - router.config.router_ttl: fixed
        tick_once(home, bodies, 'fake', 'haiku', router)
        profile = cProfile.Profile()
        started = time.process_time()
        profile.enable()
        for _ in range(args.ticks):
            tick_once(home, bodies, 'fake', 'haiku', router)
        profile.disable()
        print(f'{args.ticks} ticks: {time.process_time() - started:.3f}s CPU')
        pstats.Stats(profile).strip_dirs().sort_stats('cumulative').print_stats(15)


if __name__ == '__main__':
    main()

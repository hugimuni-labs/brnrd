"""Run from this checkout with PYTHONPATH=src; never touches the live audit home.

Optional --trace uses a working body's tools.jsonl: replay the final complete
wall-clock hour of pre/post events, keeping digests only in a temporary home.
"""
import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path

from brr.loom.runtime import audit
from brr.loom.runtime.home import Home
from brr.loom.runtime.port import render_boundary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scratch', type=Path, required=True)
    parser.add_argument('--trace', type=Path)
    parser.add_argument('--calls', type=int, default=500)
    args = parser.parse_args()
    print('module:', audit.__file__)
    rows = None
    if args.trace:
        all_rows = [json.loads(line) for line in args.trace.read_text().splitlines()]
        end = max(r['at'] for r in all_rows)
        assert end - min(r['at'] for r in all_rows) >= 3600, 'need a full hour'
        rows = [r for r in all_rows if end - 3600 < r['at'] <= end]
        print(json.dumps({'window_end_unix': end, 'source_events': len(rows),
                          'source_calls': sum(r.get('end') == 0 for r in rows)}))
    with tempfile.TemporaryDirectory(dir=args.scratch) as raw:
        home = Home(Path(raw))
        room = home.root / 'rooms/s-abcd-aaaaaa'
        (room / 'port/in').mkdir(parents=True)
        (room / 'port/in/boundary.md').write_text(render_boundary(room.name, 3, 'bench', [], set(), {}))
        (home.root / 'loom').mkdir(exist_ok=True)
        (home.root / 'loom/config.toml').write_text('audit = "on"\n')
        writer = audit.Writer(home)
        try:
            times = []
            for i in range(len(rows) if rows is not None else args.calls):
                source = rows[i] if rows is not None else None
                payload = json.dumps({'tool_name': source['tool'] if source else 'Bash',
                                      'tool_use_id': source.get('id', str(i)) if source else str(i),
                                      'cwd': '/work', 'tool_input': source if source else {'command': 'pytest tests/loom/test_jack.py'},
                                      'tool_response': None if source else 'sample result ' * 100})
                phases = ['post' if source.get('end') else 'pre'] if source else ['pre', 'post']
                start = time.perf_counter()
                for phase in phases:
                    audit.note(room, payload, phase)
                times.append((time.perf_counter() - start) * 1000)
            result = audit.verify(writer.journal.base)
            size = sum(p.stat().st_size for p in writer.journal.base.glob('*/entries.jsonl'))
            print(json.dumps({'operations': len(times), 'captured_entries': result['entries'],
                              'mean_ms': statistics.mean(times), 'p95_ms': sorted(times)[int(len(times)*.95)-1],
                              'p99_ms': sorted(times)[int(len(times)*.99)-1], 'max_ms': max(times),
                              'journal_bytes': size, 'bytes_per_hour': size if rows is not None else None,
                              'warning': (home.root / 'loom/audit-warning').exists()}))
        finally:
            writer.close()


if __name__ == '__main__':
    main()

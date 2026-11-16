"""Run from this checkout with PYTHONPATH=src; never touches the live audit home.

--startup-bytes N writes a synthetic valid journal of about N bytes, then times
a loom start (Journal) against the full walk (`audit verify`). --off times a
hook with capture off. Optional --trace uses a working body's tools.jsonl: replay the final complete
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
    parser.add_argument('--startup-bytes', type=int)
    parser.add_argument('--off', action='store_true')
    args = parser.parse_args()
    print('module:', audit.__file__)
    if args.startup_bytes:
        return startup(args.scratch, args.startup_bytes)
    if args.off:
        return off(args.scratch, args.calls)
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


def startup(scratch, target):
    with tempfile.TemporaryDirectory(dir=scratch) as raw:
        home = Home(Path(raw), install='abcd')
        audit.Journal(home).close()
        base = home.root / 'audit/abcd'
        name = f"{1:020d}-bench000"
        epoch = base / name
        epoch.mkdir()
        previous = json.loads((base / sorted(p.name for p in base.iterdir() if p.is_dir())[0] / 'head.json').read_bytes())['hash']
        seq, head, size = 0, audit.ZERO, 0
        built = time.perf_counter()
        with open(epoch / 'entries.jsonl', 'wb', buffering=1 << 20) as stream:
            while size < target:
                if seq == 0:
                    body = {'kind': 'epoch', 'previous_epoch': previous}
                else:
                    body = {'kind': 'tool', 'event': {
                        'id': f'{seq:032x}', 'at_ns': 1794788460591000000 + seq, 'phase': 'pre',
                        'identity': 's-abcd-aaaaaa', 'thread': 'bench', 'strand': 's-abcd-aaaaaa', 'wake': 3,
                        'session': '0' * 36, 'agent': '', 'call': f'toolu_{seq:024d}', 'engine': 'claude',
                        'mode': 'pre-dispatch-hook', 'tool': 'Bash', 'cwd': '/Users/someone/work/a/room',
                        'input_digest': audit.digest(seq), 'output_digest': audit.digest(None),
                        'source_digest': audit.digest([seq]), 'outcome': 'pending'}}
                row = dict(body, seq=seq, prev=head, epoch=name, install='abcd')
                head = audit.entry_hash(row)
                row['hash'] = head
                line = audit.canonical(row) + b'\n'
                stream.write(line)
                size += len(line)
                seq += 1
        audit._replace(epoch / 'head.json', {'seq': seq - 1, 'hash': head, 'bytes': size})
        audit._replace(base / 'epochs.json', sorted(p.name for p in base.iterdir() if p.is_dir()))
        print(json.dumps({'journal_bytes': size, 'entries': seq, 'build_s': round(time.perf_counter() - built, 1)}))
        times = []
        for _ in range(5):
            start = time.perf_counter()
            audit.Journal(home).close()
            times.append((time.perf_counter() - start) * 1000)
        start = time.perf_counter()
        result = audit.verify(base)
        walk = time.perf_counter() - start
        print(json.dumps({'start_ms': [round(t, 2) for t in times], 'full_walk_s': round(walk, 1),
                          'checked_entries': result['entries'], 'unclean': len(result['unclean'])}))


def off(scratch, calls):
    from brr.loom.runtime.config import load_config
    from brr.loom.runtime.home import home_of_room
    with tempfile.TemporaryDirectory(dir=scratch) as raw:
        home = Home(Path(raw))
        room = home.root / 'rooms/s-abcd-aaaaaa'
        (room / 'port/in').mkdir(parents=True)
        (home.root / 'loom').mkdir()
        (home.root / 'loom/config.toml').write_text('body = "claude"\n')
        payload = json.dumps({'tool_name': 'Bash', 'tool_input': {'command': 'ls'}})
        def timed(fn):
            out = []
            for _ in range(calls):
                start = time.perf_counter()
                fn()
                out.append((time.perf_counter() - start) * 1e6)
            out.sort()
            return {'mean_us': round(statistics.mean(out), 1), 'p95_us': round(out[int(len(out) * .95) - 1], 1)}
        print(json.dumps({'note_off': timed(lambda: audit.note(room, payload, 'pre')),
                          'old_off_path_home_and_config': timed(lambda: load_config(home_of_room(room).root))}))


if __name__ == '__main__':
    main()

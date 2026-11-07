"""Loom-owned feedback worker: bounded snapshots, no platform I/O on a tick."""
import json
import subprocess
import threading
import time

from .adapters import resolve
from .connector import environment, instances


class Presence:
    def __init__(self, home):
        self.home = home
        self.snapshot = ({}, {})
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = None
        self.began = time.time()
        self.updated = time.monotonic()
        self.revision = None
        self.seen = set()
        self.working = {}

    def update(self, facts, *, armed=True):
        if not armed:
            with self.lock:
                self.snapshot = ({}, {})
            self.revision = None
            return
        self.updated = time.monotonic()
        revision = (len(facts), facts[-1].id if facts else None)
        if self.revision == revision:
            return
        from .letter_presence import observe
        facts = observe(self.home, facts)
        self.revision = (len(facts), facts[-1].id if facts else None)
        rows = [f.data for f in facts if f.kind == 'letter.presence']
        admitted = {r['letter']: r for r in rows if r['step'] == 'admitted' and r.get('channel')}
        self.seen.update(ident for ident, row in admitted.items() if row["at"] < self.began)
        answered = {r['letter'] for r in rows if r['step'] == 'first-word'}
        ended = {(f.data.get('strand'), f.data.get('gen')) for f in facts
                 if f.kind in {'body.exited', 'body.stopped', 'attempt.ended'}}
        local = {(f.data.get('strand'), f.data.get('gen')) for f in facts
                 if f.kind == 'body.started' and f.by == f'loom:{self.home.install_id()}'}
        working = {r['letter']: r for r in rows if r['step'] == 'booted' and r.get('channel')
                   and r['letter'] not in answered and (r.get('strand'), r.get('gen')) not in ended
                   and (r.get('strand'), r.get('gen')) in local}
        with self.lock:
            self.snapshot = admitted, working
        if self.thread is None and admitted:
            self.thread = threading.Thread(target=self.run, daemon=True, name='connector-presence')
            self.thread.start()

    def flush(self):
        with self.lock:
            admitted, working = self.snapshot
        if time.monotonic() - self.updated > 10:
            working = {}  # A stalled loom cannot keep claiming a body is working.
        batches = {}
        def put(verb, row, **extra):
            instance = row['channel'].removeprefix('channel:').split('/', 1)[0]
            batches.setdefault(instance, []).append(dict(verb=verb, letter=row, **extra))
        for ident in admitted.keys() - self.seen:
            put('seen', admitted[ident])
        for ident in self.working.keys() - working.keys():
            put('working', self.working[ident], on=False)
        for row in working.values():
            put('working', row, on=True)
        # Best effort: one attempt for seen, typing renewed on the next snapshot.
        self.seen.update(admitted)
        self.working = working
        names = instances(self.home)
        for name, operations in batches.items():
            try:
                if name not in names:
                    continue
                command = resolve(self.home, names[name], 'connectors') / 'presence'
                if command.is_file():
                    subprocess.run([str(command)], input=json.dumps(operations), text=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   env=environment(self.home, name), timeout=8, check=False)
            except Exception:
                pass  # Never hold up admission or the body for a presence signal.

    def run(self):
        while not self.stop.is_set():
            started = time.monotonic()
            try:
                self.flush()
            except Exception:
                pass
            self.stop.wait(max(0, 4 - (time.monotonic() - started)))

    def close(self):
        self.stop.set()
        with self.lock:
            self.snapshot = self.snapshot[0], {}
        # Indicators expire at the platform. Shutdown never waits on transport.

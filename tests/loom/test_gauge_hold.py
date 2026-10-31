"""Hold fuel probes share their cache and attempt floor across jack processes."""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from brr.daemon2.facts import Fact
from brr.loom.runtime import loom, usage
from brr.loom.runtime.home import Home


SOURCE = Path(usage.__file__).resolve().parents[3]


def gauge(tmp_path, windows):
    folder = tmp_path / 'loom/bodies/gauge'
    folder.mkdir(parents=True)
    (folder / 'run').touch()
    script = folder / 'usage'
    script.write_text(f'''#!{sys.executable}
import json, time
from pathlib import Path
with Path({str(tmp_path / 'runs')!r}).open('a') as out:
    out.write('probe\\n')
time.sleep(.3)
print(json.dumps({{'windows': {windows!r}}}))
''')
    script.chmod(0o755)
    room = tmp_path / 'rooms/s-ab12-aaaaaa'
    (room / 'port/in').mkdir(parents=True)
    (room / 'port/out').mkdir()
    (room / 'port/wait').write_text('.5')
    (room / 'port/in/boundary.md').write_text('')
    return room


def hold(room):
    # Run the production hook caller in a new process, like each real jack.
    # Drain the async collector before exiting so its result is inspectable.
    code = '''import sys, time
from pathlib import Path
from brr.loom.runtime import jack, usage
assert Path(usage.__file__).resolve().is_relative_to(Path(sys.argv[2]))
jack.execute('stop', Path(sys.argv[1]), '')
end = time.monotonic() + 5
while usage._REFRESHING and time.monotonic() < end:
    time.sleep(.01)
assert not usage._REFRESHING
'''
    env = dict(os.environ, PYTHONPATH=str(SOURCE), BRNRD_BODY_SHELL='gauge',
               BRNRD_BODY_JACK='hooks', BRNRD_THREAD='work')
    return subprocess.Popen([sys.executable, '-c', code, str(room), str(SOURCE)],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def finish(proc):
    out, err = proc.communicate(timeout=8)
    assert proc.returncode == 0, (out, err)


def cache(root, at, windows):
    path = root / 'loom/usage/gauge.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'at': at, 'windows': windows}))
    return path


WINDOWS = [{'name': 'week', 'left_pct': 80}]


def test_hold_uses_fresh_cached_fuel_without_probe(tmp_path):
    room = gauge(tmp_path, WINDOWS)
    cache(tmp_path, time.time() - 20, WINDOWS)
    finish(hold(room))
    assert not (tmp_path / 'runs').exists()


def test_concurrent_jack_refreshes_probe_once(tmp_path):
    room = gauge(tmp_path, WINDOWS)
    first, second = hold(room), hold(room)
    finish(first)
    finish(second)
    assert (tmp_path / 'runs').read_text() == 'probe\n'
    assert json.loads((tmp_path / 'loom/usage/gauge.json').read_text())['windows'] == WINDOWS


def test_empty_probe_has_cross_process_attempt_floor(tmp_path):
    room = gauge(tmp_path, [])
    finish(hold(room))
    assert (tmp_path / 'runs').read_text() == 'probe\n'
    # Missing caches and process restarts must not bypass the attempt floor.
    (tmp_path / 'loom/usage/gauge.json').unlink()
    finish(hold(room))
    assert (tmp_path / 'runs').read_text() == 'probe\n'
    (tmp_path / 'loom/usage/gauge.lock').write_text(str(time.time() - 61))
    finish(hold(room))
    assert (tmp_path / 'runs').read_text() == 'probe\nprobe\n'


def test_body_exit_requires_newer_known_fuel(tmp_path, monkeypatch):
    gauge(tmp_path, WINDOWS)
    path = cache(tmp_path, time.time() - 20, WINDOWS)
    home = Home(tmp_path)
    monkeypatch.setattr(loom, 'body_alive', lambda room: False)
    from brr.loom.runtime import lifecycle
    monkeypatch.setattr(lifecycle, 'read_thread', lambda *a: {'status': 'open'})
    exited = time.time() - 10
    def fact(kind, **data):
        return Fact(by='loom:aaaa', kind=kind,
                    at=datetime.fromtimestamp(exited, timezone.utc).isoformat(),
                    data={'strand': 's', 'gen': 1, **data})
    facts = [fact('lease', shell='gauge', core='echo'), fact('body.started', pid=123),
             fact('body.exited', code=0)]
    observation, _ = loom._observe_body(home, facts, 's', 'work', 1, {})
    assert observation.gauge == 'refreshing'
    deadline = time.monotonic() + 5
    while path.resolve() in usage._REFRESHING and time.monotonic() < deadline:
        time.sleep(.01)
    assert json.loads(path.read_text())['at'] > exited
    observation, _ = loom._observe_body(home, facts, 's', 'work', 1, {})
    assert observation.gauge == 'ok'


def test_a_blocked_refresh_reads_unknown_past_its_deadline_and_the_body_starts(tmp_path, monkeypatch):
    # 2026-10-31: one refresh never returned and every wanted body stayed
    # pending from 09:35Z until the 20:06Z reboot.
    import threading
    from brr.loom.runtime import bodies, lifecycle
    from brr.loom.runtime.health import Health
    from brr.loom.runtime.ledger import kind_facts, read_facts
    gauge(tmp_path, WINDOWS)
    home = Home(tmp_path)
    path = (tmp_path / 'loom/usage/gauge.json').resolve()
    blocked, release = threading.Event(), threading.Event()
    def popen(*args, **kwargs):
        blocked.set()
        release.wait(10)
        raise OSError('released')
    monkeypatch.setattr(usage.subprocess, 'Popen', popen)
    monkeypatch.setattr(loom, 'body_alive', lambda room: False)
    monkeypatch.setattr(lifecycle, 'read_thread', lambda *a: {'status': 'open'})
    facts = [Fact(by='loom:aaaa', kind='lease', data={'strand': 's', 'gen': 1,
                                                       'shell': 'gauge', 'core': 'echo'})]
    try:
        observation, _ = loom._observe_body(home, facts, 's', 'work', 1, {})
        assert observation.gauge == 'refreshing'
        assert blocked.wait(5)
        assert bodies.TABLE[bodies.row_for(facts, observation)].next == 'wanted'
        health = Health(home, [], lambda *a: None)
        health.guard('gauge', usage.check_stuck, backoff=False)
        assert not kind_facts(read_facts(home), 'loom.degraded')
        usage._STARTED[path] -= usage.STUCK_AFTER  # two minutes on
        observation, _ = loom._observe_body(home, facts, 's', 'work', 1, {})
        assert observation.gauge == 'unknown'
        assert bodies.TABLE[bodies.row_for(facts, observation)].next == 'running'
        health.guard('gauge', usage.check_stuck, backoff=False)
        degraded = kind_facts(read_facts(home), 'loom.degraded')
        assert [f.data['step'] for f in degraded] == ['gauge']
        assert 'usage refresh stuck' in degraded[0].data['error'] and 'gauge for 12' in degraded[0].data['error']
        assert path in usage._REFRESHING  # no second refresh joined the blocked one
    finally:
        release.set()
    deadline = time.monotonic() + 5
    while path in usage._REFRESHING and time.monotonic() < deadline:
        threading.Event().wait(.01)
    assert path not in usage._REFRESHING and path not in usage._STARTED
    health.guard('gauge', usage.check_stuck, backoff=False)
    assert [f.data['step'] for f in kind_facts(read_facts(home), 'loom.recovered')] == ['gauge']

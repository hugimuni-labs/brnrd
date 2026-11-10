"""Clock refresh and recovery run through the process loop."""
import os
import sys
import threading
import subprocess

import pytest

from brr.loom.runtime import config, loom, usage
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import read_facts
from _step import write_thread


def test_bad_config_keeps_inputs_and_ticks_on_last_good_snapshot(tmp_path, monkeypatch):
    write_thread(tmp_path, 'brnrd', 'Brnrd', 'quit-now')
    path = tmp_path / 'loom/config.toml'
    good = path.read_text()
    stop = threading.Event()
    seen = []
    real_step = loom.tick_once
    monkeypatch.setattr(usage, 'shells', lambda home: ())

    def step(home, bodies, adapter, core, router):
        # Exercise actual input/route/output callers with the invalid file.
        real_step(home, bodies, adapter, core, router)
        seen.append(config.load_config(home.root))
        if len(seen) == 1:
            path.write_text(good + '\nunknown_key = "future"\n')
        elif len(seen) == 3:
            path.write_text(good + '\nname = "recovered"\n')
        elif len(seen) == 4:
            stop.set()
    monkeypatch.setattr(loom, 'tick_once', step)
    loom.run(tmp_path, stop=stop, tick=.001)
    assert [c.name for c in seen] == [None, None, None, 'recovered']
    notices = [f for f in read_facts(Home(tmp_path)) if f.id.startswith('loom/config-error-')]
    assert len(notices) == 1
    assert 'unknown_key' in notices[0].data['body']


def test_runtime_touch_requests_same_argv_exec_without_stopping_bodies(tmp_path, monkeypatch):
    write_thread(tmp_path, 'brnrd', 'Brnrd', 'quit-now')
    source = tmp_path / 'runtime'
    source.mkdir()
    module = source / 'loom.py'
    module.write_text('# runtime\n')
    monkeypatch.setattr(loom, '__file__', str(module))
    monkeypatch.setattr(usage, 'shells', lambda home: ())
    calls = []
    monkeypatch.setattr(loom.os, 'execv', lambda *args: calls.append(args))
    monkeypatch.setattr(loom, '_end_group', lambda *args: (_ for _ in ()).throw(AssertionError('body stopped')))
    body = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                            start_new_session=True)
    def step(home, bodies, *args):
        bodies['survivor'] = loom.Body('survivor', 'brnrd', 1, body)
        stat = module.stat()
        os.utime(module, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1))
    monkeypatch.setattr(loom, 'tick_once', step)
    try:
        loom.run(tmp_path, stop=threading.Event())
        assert calls == [(sys.executable, [sys.executable, *sys.orig_argv[1:]])]
        assert body.poll() is None
    finally:
        body.kill()
        body.wait(timeout=5)


def test_loop_refreshes_all_resolved_usage_shells_every_ten_minutes(tmp_path, monkeypatch):
    write_thread(tmp_path, 'brnrd', 'Brnrd', 'quit-now')
    for name in ('local', 'no-gauge'):
        folder = tmp_path / 'loom/bodies' / name
        folder.mkdir(parents=True)
        (folder / 'run').touch()
        if name == 'local':
            (folder / 'usage').touch()
    expected = set(usage.shells(Home(tmp_path)))
    assert 'local' in expected and 'no-gauge' not in expected
    stop = threading.Event()
    now = [100.]
    monkeypatch.setattr(loom.time, 'monotonic', lambda: now[0])
    calls = []
    monkeypatch.setattr(usage, 'reading', lambda home, shell: calls.append((now[0], shell)))
    monkeypatch.setattr(loom, 'tick_once', lambda *args: None)
    def pause(*args):
        now[0] += 300
        if now[0] > 1300:
            stop.set()
        return stop.is_set()
    monkeypatch.setattr(loom, '_pause', pause)
    loom.run(tmp_path, stop=stop)
    assert calls == [(at, shell) for at in (100., 700., 1300.) for shell in sorted(expected)]


@pytest.mark.parametrize("setting", [
    'lease_remote = "https://example.invalid/self.git"',
    'lease_mode = "strict"',
    'router_ttl = 180',
    'forge_token = "command: gh auth token"',
])
def test_lease_config_change_fences_before_next_input_or_route(tmp_path, monkeypatch, setting):
    from brr.loom.runtime.connector import Inputs
    from brr.daemon2.leases import LocalLeaseAuthority
    from brr.loom.runtime.config import loom_clock

    write_thread(tmp_path, 'brnrd', 'Brnrd', 'quit-now')
    path = tmp_path / 'loom/config.toml'
    good = path.read_text()
    stop = threading.Event()
    real_step = loom.tick_once
    monkeypatch.setattr(usage, 'shells', lambda home: ())
    polls, routes, execs = [], [], []
    monkeypatch.setattr(Inputs, 'step', lambda self: polls.append(path.read_text()))

    def step(home, bodies, adapter, core, router):
        real_step(home, bodies, adapter, core, router)
        routes.append(router.lease)
        if len(routes) == 2:
            assert router.armed()
            path.write_text(good + '\n' + setting + '\n')
        if len(routes) > 2:
            stop.set()

    def execute(*args):
        authority = LocalLeaseAuthority(tmp_path / 'ledger/leases', clock=loom_clock)
        assert not authority.authorize(routes[-1])
        execs.append(args)

    monkeypatch.setattr(loom, 'tick_once', step)
    monkeypatch.setattr(loom.os, 'execv', execute)
    loom.run(tmp_path, stop=stop, tick=.001)
    assert len(routes) == 2
    assert polls == [good]
    assert execs == [(sys.executable, [sys.executable, *sys.orig_argv[1:]])]


def test_taken_board_port_reaches_the_boundary_without_stopping_the_loop(tmp_path, monkeypatch):
    import socket
    from brr.loom.runtime.board import Parts
    from brr.loom.runtime.health import degraded_line

    write_thread(tmp_path, 'brnrd', 'Brnrd', 'quit-now')
    stop = threading.Event()
    ticks = []
    monkeypatch.setattr(usage, 'shells', lambda home: ())
    monkeypatch.setattr(Parts, 'run', lambda self: None)
    monkeypatch.setenv('BRNRD_FRONTEND_DIR', str(tmp_path / 'no-build'))

    def step(*args):
        ticks.append(True)
        stop.set()

    monkeypatch.setattr(loom, 'tick_once', step)
    with socket.socket() as taken:
        taken.bind(('127.0.0.1', 0))
        taken.listen()
        port = taken.getsockname()[1]
        path = tmp_path / 'loom/config.toml'
        path.write_text(path.read_text() + f'\nboard_port = "{port}"\n')
        loom.run(tmp_path, stop=stop, tick=.001)
    facts = read_facts(Home(tmp_path))
    line = degraded_line(facts)
    assert ticks == [True]
    assert 'board since' in line and f'board port {port}:' in line
    assert 'Address already in use' in line

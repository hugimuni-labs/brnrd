"""Clock refresh and recovery run through the process loop."""
import os
import sys
import threading
import subprocess

from brr.loom.runtime import config, loom, usage
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import read_facts
from _step import write_thread
from brr.loom.runtime.selfrepo import git, _commit


def test_bad_config_keeps_inputs_and_ticks_on_last_good_snapshot(tmp_path, monkeypatch):
    write_thread(tmp_path, 'inbox', 'Inbox', 'quit-now')
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
    write_thread(tmp_path, 'inbox', 'Inbox', 'quit-now')
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
        bodies['survivor'] = loom.Body('survivor', 'inbox', 1, body)
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


def test_loop_refreshes_selected_usage_shells_every_ten_minutes(tmp_path, monkeypatch):
    write_thread(tmp_path, 'inbox', 'Inbox', 'quit-now')
    for name in ('local', 'no-gauge'):
        folder = tmp_path / 'loom/bodies' / name
        folder.mkdir(parents=True)
        (folder / 'run').touch()
        if name == 'local':
            (folder / 'usage').touch()
    (tmp_path / 'self/threads/inbox/seat').write_text('body: local/test\n')
    git(tmp_path / "self", "add", "threads/inbox/seat")
    _commit(tmp_path / "self", "Select gauge shell")
    expected = {'local'}
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


def test_unused_gauge_is_not_run_until_an_open_seat_selects_it(tmp_path, monkeypatch):
    import time
    write_thread(tmp_path, 'inbox', 'Inbox', 'quit-now')
    folder = tmp_path / 'loom/bodies/unused'
    folder.mkdir(parents=True)
    (folder / 'run').touch()
    monkeypatch.setattr(usage, 'shells', lambda home: iter(['unused']))
    marker = tmp_path / 'probed'
    script = folder / 'usage'
    script.write_text(f'#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\nimport json; print(json.dumps({{"windows": []}}))\n')
    script.chmod(0o755)
    monkeypatch.setattr(loom, 'tick_once', lambda *args: None)
    stop = threading.Event()
    def pause(*args):
        deadline = time.monotonic() + 5
        while usage._REFRESHING and time.monotonic() < deadline:
            time.sleep(.01)
        stop.set()
        return True
    monkeypatch.setattr(loom, '_pause', pause)
    loom.run(tmp_path, stop=stop)
    assert not marker.exists()
    (tmp_path / 'self/threads/inbox/seat').write_text('body: unused/test\n')
    git(tmp_path / "self", "add", "threads/inbox/seat")
    _commit(tmp_path / "self", "Select gauge shell")
    stop.clear()
    loom.run(tmp_path, stop=stop)
    assert marker.exists()


def test_usage_selection_includes_default_and_pending_parked_live_bodies(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from brr.daemon2.facts import Fact
    facts = []
    for i, state in enumerate(('pending', 'parked', 'started', 'exited', 'ended')):
        data = dict(strand=f's-ab12-{i:06}', gen=1, shell=state)
        facts.append(Fact(kind='lease', by='loom:test', data=data))
        facts.append(Fact(kind=('attempt.ended' if state == 'ended' else 'body.' + state),
                          by='loom:test', data=dict(data, until=99)))
    monkeypatch.setattr(loom, 'body_alive', lambda room: room.name == 's-ab12-000002')
    monkeypatch.setattr(loom, 'read_facts', lambda home: facts)
    monkeypatch.setattr(loom, 'fold', lambda fs: SimpleNamespace(accepted=fs))
    monkeypatch.setattr(loom, 'main_snapshot', lambda home: {})
    monkeypatch.setattr(usage, 'shells', lambda home: iter(['default', 'pending', 'parked', 'started', 'exited', 'ended', 'idle']))
    assert loom._usage_shells(Home(tmp_path), SimpleNamespace(body='default/core')) == ['default', 'parked', 'pending', 'started']

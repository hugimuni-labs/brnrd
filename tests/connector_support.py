"""Folder oracle helpers: replace only transport I/O, keep the loom's callers."""
import contextlib
import io
import json
import runpy
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

from brr.loom.runtime import connector as source
from brr.loom.runtime import connector
from brr.loom.runtime.connector import person_dm, route_bare, thread_for_message
from brr.loom.runtime.home import atomic_write

FOLDER = Path(connector.__file__).parent / 'connectors/relay'
input_module = SimpleNamespace(**runpy.run_path(str(FOLDER / 'in')))
output_module = SimpleNamespace(**runpy.run_path(str(FOLDER / 'out')))
import common
cloud = common.cloud
RelayClient = common.RelayClient
append = source.append


def enable(home, names=('relay', 'fake')):
    config = home.root / 'loom/config.toml'
    config.parent.mkdir(parents=True, exist_ok=True)
    lines = config.read_text().splitlines() if config.exists() else []
    lines = [l for l in lines if not l.startswith('connectors =')]
    config.write_text('\n'.join([*lines, 'connectors = ' + json.dumps(list(names))]) + '\n')
    # A passive local input is a valid folder: the tests drive transport polls.
    local = home.root / 'loom/connectors/relay'
    local.mkdir(parents=True, exist_ok=True)
    for file in ('carries', 'out', 'common.py'):
        shutil.copy2(FOLDER / file, local / file)
    if not (local / 'in').exists():
        (local / 'in').mkdir()
    return home


def directories(home):
    state = home.root / 'loom/connectors/relay/state'
    drop = state / 'in'
    state.mkdir(parents=True, exist_ok=True)
    drop.mkdir(parents=True, exist_ok=True)
    return drop, state


def read_cursor(home):
    return input_module.cursor_at(directories(home)[1])


def _write_cursor(home, cursor):
    return input_module.write_cursor(directories(home)[1], cursor)


def pull_once(home, client, cursor, failures=None):
    enable(home)
    drop, state = directories(home)
    # Tests can interrupt each durability boundary without mocking the source step.
    real_write = input_module.pull_once.__globals__['write_cursor']
    real_append = source.append
    input_module.pull_once.__globals__['write_cursor'] = lambda _state, n: _write_cursor(home, n)
    source.append = append
    try:
        result = input_module.pull_once(drop, state, client, cursor, failures)
        source.drain(home, 'relay', drop)
        return result
    finally:
        input_module.pull_once.__globals__['write_cursor'] = real_write
        source.append = real_append


def poll_forever(home, client, lock, stop, log=None):
    enable(home)
    drop, state = directories(home)
    real_pull = input_module.poll_forever.__globals__['pull_once']
    def pull(*args):
        result = real_pull(*args)
        source.drain(home, 'relay', drop)
        return result
    input_module.poll_forever.__globals__['pull_once'] = pull
    try:
        input_module.poll_forever(drop, state, client, lock, stop, log or (lambda *_: None))
    finally:
        input_module.poll_forever.__globals__['pull_once'] = real_pull


def install_out(monkeypatch, home, client):
    enable(home)
    state = directories(home)[1]
    atomic_write(state / 'messages.json', json.dumps({'ev_1': 'ev_1', 'ev_7': 'ev_7'}))
    real = subprocess.run
    def run(args, **kw):
        if Path(str(args[0])).name != 'out':
            return real(args, **kw)
        stdout, stderr = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = output_module.deliver(Path(args[1]), state, client)
        except Exception as exc:
            return subprocess.CompletedProcess(args, 1, '', str(exc))
        return subprocess.CompletedProcess(args, code, stdout.getvalue(), stderr.getvalue())
    monkeypatch.setattr(subprocess, 'run', run)


def install_fake(monkeypatch, callback):
    real = subprocess.run
    def run(args, **kw):
        if Path(str(args[0])).parts[-2:] != ('fake', 'out'):
            return real(args, **kw)
        from brr.loom.runtime.port import parse_frontmatter
        letter = parse_frontmatter(Path(args[1]).read_text())
        callback(letter['body'])
        return subprocess.CompletedProcess(args, 0, '{"ids": []}', '')
    monkeypatch.setattr(subprocess, 'run', run)


def install_input(monkeypatch, home):
    """Run the real input program in a thread with a test wire client."""
    import threading
    enable(home)
    local = home.root / 'loom/connectors/relay'
    if (local / 'in').is_dir():
        (local / 'in').rmdir()
    shutil.copy2(FOLDER / 'in', local / 'in')
    real = subprocess.Popen
    class InputProcess:
        def __init__(self, args, **kw):
            from brr.gates.relay_lock import RelayLock
            from brr.loom.runtime.config import load_config
            from brr.loom.runtime.loom import _log
            self.stop = threading.Event()
            state = Path(kw['env']['BRNRD_STATE'])
            config = load_config(home.root)
            self.thread = threading.Thread(target=input_module.poll_forever,
                args=(Path(args[1]), state, RelayClient(config.relay_state),
                      RelayLock(config.relay_state, 'loom'), self.stop),
                kwargs=dict(log=lambda text: _log(home, 'relay: poll failed: ' + text)), daemon=True)
            self.thread.start()
        def poll(self):
            return None if self.thread.is_alive() else 0
        def terminate(self):
            self.stop.set()
        kill = terminate
        def wait(self, timeout=None):
            self.thread.join(timeout)
            if self.thread.is_alive():
                raise subprocess.TimeoutExpired('relay/in', timeout)
            return 0
    def start(args, **kw):
        if Path(str(args[0])).parts[-2:] == ('relay', 'in'):
            return InputProcess(args, **kw)
        return real(args, **kw)
    monkeypatch.setattr(subprocess, 'Popen', start)

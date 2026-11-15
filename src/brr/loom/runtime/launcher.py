"""Installed outside the checkout; stdlib only, including when brr cannot import.

The service manager retries failed starts. Before the fourth start we restore
last healthy HEAD. Counters are per checkout, shared by homes using that tree.
No supervisor process stays around: signals and service PIDs keep their meaning.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from contextlib import contextmanager


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def save(path, data):
    temp = path.with_name(path.name + f'.{os.getpid()}')
    temp.write_text(json.dumps(data) + '\n')
    os.replace(temp, path)


def state_path(root):
    # Worktrees share a lock, but each checkout has its own HEAD and health.
    return (root / git(root, 'rev-parse', '--git-dir')).resolve() / 'brnrd-startup.json'


@contextmanager
def locked(root):
    common = (root / git(root, 'rev-parse', '--git-common-dir')).resolve()
    with (common / 'brnrd-update.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield state_path(root)


def read(path):
    return json.loads(path.read_text()) if path.exists() else {}


def dependencies(root, before, after):
    """Dependency/build metadata changed; install into this exact interpreter's prefix."""
    if before == after:
        return False
    names = git(root, 'diff', '--name-only', before, after, '--',
                'pyproject.toml', 'uv.lock', 'setup.py', 'setup.cfg', 'requirements*.txt')
    return bool(names)


def reinstall(root, python):
    """Caller holds the checkout lock. Leave a retry marker if installation dies."""
    path = state_path(root)
    state = read(path)
    state['reinstall'] = True
    save(path, state)
    uv = shutil.which('uv')
    command = ([uv, 'pip', 'install', '--python', str(python)] if uv else
               [str(python), '-m', 'pip', 'install'])
    env = os.environ.copy()
    env.pop('PYTHONPATH', None)
    subprocess.run([*command, '--editable', str(root)], env=env, check=True, timeout=300)
    state['reinstall'] = False
    save(path, state)


def healthy():
    """Only the process launched by this guard may acknowledge its attempted HEAD."""
    root, attempted = os.environ.get('BRNRD_START_ROOT'), os.environ.get('BRNRD_START_HEAD')
    if not root or not attempted:
        return
    root = Path(root)
    with locked(root) as path:
        state = read(path)
        if (state.get('head') == attempted and state.get('pid') == os.getpid()
                and git(root, 'rev-parse', 'HEAD') == attempted):
            state.update(healthy=attempted, failures=0)
            save(path, state)
            return state.get('rollback')


def start(root, python, args):
    with locked(root) as path:
        state = read(path)
        head = git(root, 'rev-parse', 'HEAD')
        if state.get('head') != head:
            state.update(head=head, failures=0)
        if state.get('failures', 0) >= 3:
            good = state.get('healthy')
            if not good or good == head:
                print('brnrd: three starts failed; no different healthy commit to restore', file=sys.stderr)
            else:
                if git(root, 'status', '--porcelain', '--untracked-files=no'):
                    raise RuntimeError('three starts failed; checkout has uncommitted changes; not touched')
                # A named ref keeps all private commits reachable after reset.
                rescue = f'refs/brnrd/failed-start/{head}'
                git(root, 'update-ref', rescue, head)
                print(f'brnrd: three starts failed at {head}; restoring healthy {good}', file=sys.stderr)
                changed = dependencies(root, head, good)
                git(root, 'reset', '--keep', good)
                # Save before installation: an interrupted reinstall must be retried.
                state.update(head=good, failures=0, reinstall=changed, rollback={'from': head, 'to': good, 'ref': rescue})
                save(path, state)
                head = good
        if state.get('reinstall'):
            reinstall(root, python)
            state['reinstall'] = False
        state['pid'] = os.getpid()
        state['failures'] = state.get('failures', 0) + 1
        save(path, state)
        os.environ.update(BRNRD_START_ROOT=str(root), BRNRD_START_HEAD=head)
    os.execv(str(python), [str(python), '-I', '-m', 'brr', *args])


def main():
    layout = Path(__file__).resolve().parent
    python = layout / 'current' / 'bin' / 'python'
    args = sys.argv[1:]
    source = layout / 'launcher-source'
    try:
        if args[:2] == ['runtime', 'loom'] and source.exists():
            config = read(source)
            if (layout / 'current').resolve() == Path(config['prefix']).resolve():
                start(Path(config['source']), python, args)
        os.execv(str(python), [str(python), '-I', '-m', 'brr', *args])
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'brnrd launcher: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())

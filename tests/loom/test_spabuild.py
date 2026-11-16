"""The loom keeps src/frontend/build on its checkout's tree: a moved tree builds into a fresh dir that replaces the
served one only on success; a failed build keeps the old one served and raises (the loop's ``spa`` degraded step)."""
import subprocess

import pytest

from brr.loom.runtime.spabuild import BuildFailed, SpaBuild, npm_supported as check_npm


@pytest.fixture(autouse=True)
def board_dependencies(monkeypatch):
    from brr.loom.runtime import board_asset, spabuild
    monkeypatch.setattr(board_asset, 'available', lambda: set())
    monkeypatch.setattr(spabuild, 'npm_supported', lambda: spabuild.shutil.which('npm') is not None)
    # Builds run on a worker thread: a pytest.fail (or any error) raised there must fail the test.
    import threading
    raised = []
    monkeypatch.setattr(threading, 'excepthook', lambda args: raised.append(args.exc_value))
    yield
    assert not raised, f'raised on the build thread: {raised!r}'


def git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "repo"
    frontend = root / "src" / "frontend"
    frontend.mkdir(parents=True)
    (frontend / "package.json").write_text("{}")
    (frontend / "package-lock.json").write_text("{}")
    (frontend / "node_modules").mkdir()
    (frontend / "node_modules" / ".package-lock.json").write_text("{}")
    git(root, "init", "-q")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "a", "--allow-empty")
    git(root, "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "b")
    return root, frontend


def settle(spa):
    spa.step()
    if spa.worker is not None:
        spa.worker.join(10)


def test_a_moved_tree_builds_and_swaps_only_on_success(checkout):
    root, frontend = checkout
    calls, outcome = [], {"fail": False}

    def run(args, cwd, env=None):
        calls.append(args)
        if outcome["fail"]:
            raise BuildFailed("npm run build exited 1: x.svelte: boom")
        out = cwd / env["BRNRD_SPA_OUT"]
        out.mkdir()
        (out / "200.html").write_text(f"shell {len(calls)}")

    (frontend / "build").mkdir()
    (frontend / "build" / "200.html").write_text("stale, unstamped")
    spa = SpaBuild(frontend, lambda text: None, run, every=0)
    settle(spa)
    assert calls == [["npm", "run", "build"]]  # node_modules is newer than the lock: no npm ci
    assert (frontend / "build" / "200.html").read_text() == "shell 1"
    assert spa.built() == spa.wanted()
    assert not (frontend / "build.next").exists() and not (frontend / "build.old").exists()
    settle(spa)
    assert len(calls) == 1  # same tree: nothing to do

    (frontend / "app.svelte").write_text("moved")
    git(root, "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    outcome["fail"] = True
    settle(spa)
    assert (frontend / "build" / "200.html").read_text() == "shell 1"  # the old build stays served
    with pytest.raises(BuildFailed, match="boom.*serving the build of tree"):
        spa.step()
    assert len(calls) == 2  # a failed tree is not rebuilt every tick

    (frontend / "app.svelte").write_text("fixed")
    git(root, "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "d")
    outcome["fail"] = False
    settle(spa)
    spa.step()  # recovered: no raise
    assert (frontend / "build" / "200.html").read_text() == "shell 3" and spa.built() == spa.wanted()


def test_a_newer_lock_installs_first(checkout):
    _, frontend = checkout
    calls = []

    def run(args, cwd, env=None):
        calls.append(args[1])
        if env:
            (cwd / env["BRNRD_SPA_OUT"]).mkdir()
            (cwd / env["BRNRD_SPA_OUT"] / "200.html").write_text("ok")

    import os
    os.utime(frontend / "node_modules" / ".package-lock.json", (0, 0))
    settle(SpaBuild(frontend, lambda text: None, run, every=0))
    assert calls == ["ci", "run"]


def test_head_is_read_once_per_interval_not_every_tick(checkout, monkeypatch):
    root, frontend = checkout
    clock, reads = [0.0], []
    spa = SpaBuild(frontend, lambda text: None, lambda *a, **k: None, every=30, clock=lambda: clock[0])
    monkeypatch.setattr(spa, "wanted", lambda: reads.append(clock[0]) or spa.built())
    for _ in range(10):
        spa.step()
        clock[0] += 0.2
    assert reads == [0.0]
    clock[0] = 30.0
    spa.step()
    assert reads == [0.0, 30.0]


def managed(root, frontend):
    (frontend / '.gitignore').write_text('build\nbuild.next\nbuild.old\nnode_modules\n')
    git(root, 'add', '-A')
    git(root, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'ignore builds')
    git(root, 'remote', 'add', 'origin', 'https://github.com/hugimuni-labs/brnrd.git')
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')


def test_managed_upstream_fetch_needs_no_npm(checkout, monkeypatch):
    from brr.loom.runtime import board_asset, spabuild
    root, frontend = checkout
    managed(root, frontend)
    downloads = []

    def fetch(commit, tree, destination):
        downloads.append((commit, tree))
        destination.mkdir()
        (destination / '200.html').write_text('upstream')
        (destination / '.tree').write_text(tree)

    monkeypatch.setattr(board_asset, 'fetch', fetch)
    monkeypatch.setattr(spabuild.shutil, 'which', lambda name: None)
    spa = SpaBuild(frontend, lambda text: None, lambda *a: pytest.fail('npm ran'), every=0)
    settle(spa)
    settle(spa)
    assert len(downloads) == 1
    assert (frontend / 'build' / '200.html').read_text() == 'upstream'
    assert spa.built() == spa.wanted()


def test_changed_without_npm_keeps_upstream_board_and_says_once(checkout, monkeypatch):
    from brr.loom.runtime import board_asset, spabuild
    root, frontend = checkout
    managed(root, frontend)
    (frontend / 'build').mkdir()
    (frontend / 'build' / '200.html').write_text('good')
    spa = SpaBuild(frontend, lambda text: messages.append(text), every=0)
    spa.wanted()
    (frontend / 'build' / '.tree').write_text(spa.upstream[1])
    (frontend / 'build' / '.commit').write_text(spa.upstream[0])
    (frontend / 'own.svelte').write_text('own edit')
    messages = []
    monkeypatch.setattr(board_asset, 'fetch', lambda *a: pytest.fail('unnecessary download'))
    monkeypatch.setattr(spabuild.shutil, 'which', lambda name: None)
    settle(spa)
    settle(spa)
    assert (frontend / 'build' / '200.html').read_text() == 'good'
    assert len(messages) == 1 and 'npm is unavailable' in messages[0]
    assert spa.failed is None


def test_missing_asset_without_npm_does_not_fail_loom(checkout, monkeypatch):
    from brr.loom.runtime import board_asset, spabuild
    monkeypatch.setattr(spabuild.shutil, 'which', lambda _: None)
    root, frontend = checkout
    managed(root, frontend)
    messages = []

    def missing(*args):
        raise OSError('404')

    monkeypatch.setattr(board_asset, 'fetch', missing)
    spa = SpaBuild(frontend, messages.append, lambda *a: pytest.fail('npm ran'), every=0)
    settle(spa)
    settle(spa)
    assert spa.failed is None
    assert not spa.build.exists()
    assert any('installed without a board' in line and 'brr.loom.runtime.board_asset' in line for line in messages)
    assert any('archive not served' in line for line in messages)


def test_changed_frontend_with_npm_builds_locally(checkout, monkeypatch):
    from brr.loom.runtime import board_asset, spabuild
    root, frontend = checkout
    managed(root, frontend)
    calls = []

    def fetch(commit, tree, destination):
        destination.mkdir()
        (destination / '200.html').write_text('upstream')
        (destination / '.tree').write_text(tree)

    def run(args, cwd, env=None):
        calls.append(args)
        if env:
            destination = cwd / env['BRNRD_SPA_OUT']
            destination.mkdir()
            (destination / '200.html').write_text('own board')

    monkeypatch.setattr(board_asset, 'fetch', fetch)
    monkeypatch.setattr(spabuild.shutil, 'which', lambda name: '/fixture/npm')
    (frontend / 'own.svelte').write_text('own change')
    spa = SpaBuild(frontend, lambda text: None, run, every=0)
    settle(spa)
    settle(spa)
    assert calls == [['npm', 'run', 'build']]
    assert (spa.build / '200.html').read_text() == 'own board'


def test_changed_without_npm_preserves_last_good_local_board(checkout, monkeypatch):
    from brr.loom.runtime import board_asset, spabuild
    root, frontend = checkout
    managed(root, frontend)
    spa = SpaBuild(frontend, lambda _: None, every=0)
    spa.wanted()
    spa.build.mkdir()
    (spa.build / '200.html').write_text('last good local board')
    (spa.build / '.tree').write_text('previous local content hash')  # a local build: no .commit
    (frontend / 'own.svelte').write_text('new edit')
    monkeypatch.setattr(board_asset, 'fetch', lambda *a: pytest.fail('discarded last good local build'))
    monkeypatch.setattr(spabuild.shutil, 'which', lambda _: None)
    settle(spa)
    assert (spa.build / '200.html').read_text() == 'last good local board'


@pytest.mark.parametrize('node,npm,allowed', [
    ('v20.18.0', '10.0.0', False), ('v20.19.0', '10.0.0', True),
    ('v22.12.0', '10.0.0', False), ('v22.13.0', '10.0.0', True),
    ('v23.0.0', '10.0.0', False), ('v24.0.0', '9.0.0', True),
    ('v24.0.0', '8.0.0', False), ('broken', '10.0.0', False),
])
def test_local_build_checks_supported_node_and_npm(monkeypatch, node, npm, allowed):
    from brr.loom.runtime import spabuild
    monkeypatch.setattr(spabuild.shutil, 'which', lambda name: '/fixture/' + name)
    monkeypatch.setattr(spabuild.subprocess, 'run', lambda args, **kw:
                        subprocess.CompletedProcess(args, 0, node if args[0] == 'node' else npm, ''))
    assert check_npm() is allowed


def test_missing_exact_asset_builds_locally_before_trying_ancestors(checkout, monkeypatch):
    from brr.loom.runtime import board_asset, spabuild
    root, frontend = checkout
    managed(root, frontend)
    monkeypatch.setattr(board_asset, 'fetch', lambda *a: (_ for _ in ()).throw(OSError('404')))
    monkeypatch.setattr(board_asset, 'available', lambda: pytest.fail('ancestor before supported local build'))
    monkeypatch.setattr(spabuild, 'npm_supported', lambda: True)
    calls = []
    def run(args, cwd, env=None):
        calls.append(args)
        if env:
            out = cwd / env['BRNRD_SPA_OUT']
            out.mkdir()
            (out / '200.html').write_text('locally built')
    spa = SpaBuild(frontend, lambda _: None, run, every=0)
    settle(spa)
    assert calls == [['npm', 'run', 'build']]
    assert (spa.build / '200.html').read_text() == 'locally built'
    assert spa.built() == spa.wanted()


def test_fallback_selects_newest_reachable_asset_and_reports_older_board(checkout, monkeypatch):
    from brr.loom.runtime import board_asset, spabuild
    root, frontend = checkout
    managed(root, frontend)
    older = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    (frontend / 'new.svelte').write_text('new upstream')
    git(root, 'add', '-A')
    git(root, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'new upstream')
    newer = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    calls, messages = [], []
    monkeypatch.setattr(spabuild, 'npm_supported', lambda: False)
    # Unrelated available assets cannot be selected just because they are newer.
    monkeypatch.setattr(board_asset, 'available', lambda: {older, subprocess.check_output(['git', 'rev-parse', older + '^'], cwd=root, text=True).strip(), 'f' * 40})
    def fetch(commit, tree, out):
        calls.append(commit)
        if commit == newer:
            raise OSError('not published yet')
        assert commit == older
        out.mkdir()
        (out / '200.html').write_text('older verified board')
        (out / '.tree').write_text(tree)
    monkeypatch.setattr(board_asset, 'fetch', fetch)
    spa = SpaBuild(frontend, messages.append, every=0)
    settle(spa)
    assert calls == [newer, older]
    assert (spa.build / '200.html').read_text() == 'older verified board'
    assert spa.fetched() == older != spa.upstream[0]
    assert any('older ancestor commit ' + older in line for line in messages)
    assert spa.failed is None


def test_bad_digest_is_never_served_and_idle_retry_recovers(checkout, monkeypatch):
    from brr.loom.runtime import board_asset, spabuild
    root, frontend = checkout
    managed(root, frontend)
    clock, attempts, messages = [0.0], [], []
    monkeypatch.setattr(spabuild, 'npm_supported', lambda: False)
    def fetch(commit, tree, out):
        attempts.append(clock[0])
        if len(attempts) == 1:
            out.mkdir()
            (out / '200.html').write_text('unverified bytes')
            raise ValueError('board archive digest mismatch')
        out.mkdir()
        (out / '200.html').write_text('verified later')
        (out / '.tree').write_text(tree)
    monkeypatch.setattr(board_asset, 'fetch', fetch)
    spa = SpaBuild(frontend, messages.append, every=0, clock=lambda: clock[0])
    settle(spa)
    assert not spa.build.exists() and not spa.next.exists()
    assert any('digest mismatch' in line and 'archive not served' in line for line in messages)
    assert any('installed without a board' in line and ' -I -m brr.loom.runtime.board_asset' in line for line in messages)
    clock[0] = 299
    settle(spa)
    assert attempts == [0.0]
    clock[0] = 300
    spa.step(idle=False)
    assert attempts == [0.0]
    settle(spa)
    assert attempts == [0.0, 300]
    assert (spa.build / '200.html').read_text() == 'verified later'


def upstream_fetch(downloads=None):
    def fetch(commit, tree, destination):
        if downloads is not None:
            downloads.append(commit)
        destination.mkdir()
        (destination / '200.html').write_text('upstream')
    return fetch


def local_run(calls):
    def run(args, cwd, env=None):
        calls.append(args)
        if env:
            out = cwd / env['BRNRD_SPA_OUT']
            out.mkdir()
            (out / '200.html').write_text('own board')
    return run


def test_reverted_frontend_change_brings_the_upstream_board_back(checkout, monkeypatch):
    """A local build of a changed tree never passes for upstream: revert it and upstream's board returns."""
    from brr.loom.runtime import board_asset, spabuild
    root, frontend = checkout
    managed(root, frontend)
    downloads, calls = [], []
    monkeypatch.setattr(board_asset, 'fetch', upstream_fetch(downloads))
    monkeypatch.setattr(spabuild, 'npm_supported', lambda: True)
    spa = SpaBuild(frontend, lambda _: None, local_run(calls), every=0)
    settle(spa)
    assert (spa.build / '200.html').read_text() == 'upstream' and len(downloads) == 1
    (frontend / 'own.svelte').write_text('own change')
    settle(spa)
    assert (spa.build / '200.html').read_text() == 'own board' and spa.fetched() == ''
    (frontend / 'own.svelte').unlink()
    settle(spa)
    assert (spa.build / '200.html').read_text() == 'upstream'
    assert spa.built() == spa.wanted() and len(downloads) == 2
    settle(spa)
    assert spa.worker is None or not spa.worker.is_alive()
    assert len(downloads) == 2 and calls == [['npm', 'run', 'build']]


def test_moved_tree_builds_while_the_loom_is_busy(checkout, monkeypatch):
    """Only the network retry waits for idle: a changed tree builds locally at once, as before."""
    from brr.loom.runtime import board_asset, spabuild
    root, frontend = checkout
    managed(root, frontend)
    monkeypatch.setattr(board_asset, 'fetch', upstream_fetch())
    monkeypatch.setattr(spabuild, 'npm_supported', lambda: True)
    calls, checks = [], []
    spa = SpaBuild(frontend, lambda _: None, local_run(calls), every=0)

    def busy():
        checks.append(1)
        return False
    spa.step(busy)
    spa.worker.join(10)
    assert (spa.build / '200.html').read_text() == 'upstream'
    (frontend / 'own.svelte').write_text('own change')
    spa.step(busy)
    spa.worker.join(10)
    assert (spa.build / '200.html').read_text() == 'own board'
    assert checks == []


def test_idle_gates_only_the_retry_and_is_read_only_when_it_is_due(checkout, monkeypatch):
    from brr.loom.runtime import board_asset, spabuild
    root, frontend = checkout
    managed(root, frontend)
    monkeypatch.setattr(spabuild, 'npm_supported', lambda: False)
    clock, checks, attempts = [0.0], [], []

    def missing(commit, tree, destination):
        attempts.append(clock[0])
        raise OSError('404')
    monkeypatch.setattr(board_asset, 'fetch', missing)
    spa = SpaBuild(frontend, lambda _: None, every=30, clock=lambda: clock[0])
    idle = [False]

    def check():
        checks.append(clock[0])
        return idle[0]

    def tick():
        spa.step(check)
        if spa.worker is not None:
            spa.worker.join(10)
    tick()
    assert attempts == [0.0] and checks == []  # the first attempt does not wait for idle
    while clock[0] < 270:
        clock[0] += 30
        tick()
    assert attempts == [0.0] and checks == []
    clock[0] = 300
    tick()
    assert attempts == [0.0] and checks == [300]
    idle[0] = True
    clock[0] = 330
    tick()
    assert attempts == [0.0, 330]


def test_printed_retry_command_fetches_the_served_board(checkout, monkeypatch, tmp_path):
    """The one command an install without a board prints, run as printed, brings the board."""
    import hashlib
    import http.server
    import io
    import json
    import os
    import shlex
    import sys
    import tarfile
    import threading
    from brr.loom.runtime import board_asset, spabuild
    root, frontend = checkout
    managed(root, frontend)
    layout = tmp_path / 'layout'
    layout.mkdir()
    (layout / 'current').symlink_to(sys.prefix)
    monkeypatch.setenv('BRNRD_ROOT', str(layout))
    monkeypatch.setattr(spabuild, 'npm_supported', lambda: False)
    monkeypatch.setattr(board_asset, 'fetch', lambda *a: (_ for _ in ()).throw(OSError('404')))
    messages = []
    spa = SpaBuild(frontend, messages.append, every=0)
    settle(spa)
    assert not spa.build.exists()
    line = next(m for m in messages if 'installed without a board' in m)
    command = shlex.split(line.split('fetch it later with: ', 1)[1].split('; the loom will retry', 1)[0])
    # Publish the asset the command will look for, as the workflow does.
    commit, tree = spa.upstream
    served = tmp_path / 'served'
    served.mkdir()
    page = io.BytesIO()
    with tarfile.open(fileobj=page, mode='w:gz') as archive:
        body = b'the published board'
        info = tarfile.TarInfo('200.html')
        info.size = len(body)
        archive.addfile(info, io.BytesIO(body))
    data = page.getvalue()
    (served / f'board-{commit}.tar.gz').write_bytes(data)
    (served / f'board-{commit}.tar.gz.json').write_text(json.dumps(
        {'commit': commit, 'tree': tree, 'sha256': hashlib.sha256(data).hexdigest()}))
    handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(*a, directory=str(served), **kw)
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        env = {k: v for k, v in os.environ.items() if k not in {'PYTHONPATH', 'PYTHONHOME'}}
        env['BRNRD_BOARD_BASE'] = f'http://127.0.0.1:{server.server_port}'
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=60)
    finally:
        server.shutdown()
    assert result.returncode == 0, result.stdout + result.stderr
    assert (spa.build / '200.html').read_text() == 'the published board'
    assert spa.fetched() == commit and spa.built() == spa.wanted()


def test_taking_main_under_a_local_edit_rebuilds(checkout, monkeypatch):
    """The stamp of a local edit carries its base: main moving another file is a new board."""
    from brr.loom.runtime import board_asset, spabuild
    root, frontend = checkout
    managed(root, frontend)
    monkeypatch.setattr(board_asset, 'fetch', upstream_fetch())
    monkeypatch.setattr(spabuild, 'npm_supported', lambda: True)
    (frontend / 'a.txt').write_text('upstream a')
    (frontend / 'b.txt').write_text('upstream b')
    git(root, 'add', '-A')
    git(root, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'files')
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    calls = []
    spa = SpaBuild(frontend, lambda _: None, local_run(calls), every=0)
    (frontend / 'a.txt').write_text('own edit')
    settle(spa)
    assert len(calls) == 1
    settle(spa)
    assert len(calls) == 1  # nothing moved: nothing rebuilt
    # main changes b.txt only, and the resident takes it with its edit still on top
    (frontend / 'b.txt').write_text('new upstream b')
    git(root, 'add', 'src/frontend/b.txt')
    git(root, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'upstream moves b')
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    settle(spa)
    assert len(calls) == 2
    assert spa.built() == spa.wanted()


def test_leftover_staging_dirs_are_not_a_change(checkout, monkeypatch):
    root, frontend = checkout
    managed(root, frontend)
    (frontend / '.gitignore').write_text('build\nnode_modules\n')  # a resident's own ignore file
    git(root, 'add', '-A')
    git(root, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'own ignore')
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    spa = SpaBuild(frontend, lambda _: None, every=0)
    clean = spa.wanted()
    for name in ('build.next', 'build.old'):
        (frontend / name).mkdir()
        (frontend / name / '200.html').write_text('half a build from a killed loom')
    assert spa.wanted() == clean == spa.upstream[1]
    assert spa.changed is False


def test_retry_with_an_older_board_served_does_not_list_the_api(checkout, monkeypatch):
    from brr.loom.runtime import board_asset, spabuild
    root, frontend = checkout
    managed(root, frontend)
    monkeypatch.setattr(spabuild, 'npm_supported', lambda: False)
    monkeypatch.setattr(board_asset, 'fetch', lambda *a: (_ for _ in ()).throw(OSError('404')))
    listings = []  # a recorder: pytest.fail inside the build's thread would not fail the test
    monkeypatch.setattr(board_asset, 'available', lambda: listings.append(1) or set())
    spa = SpaBuild(frontend, lambda _: None, every=0)
    spa.build.mkdir()
    (spa.build / '200.html').write_text('older board')
    (spa.build / '.tree').write_text('0' * 40)
    (spa.build / '.commit').write_text('1' * 40)
    settle(spa)
    assert (spa.build / '200.html').read_text() == 'older board'
    assert listings == []

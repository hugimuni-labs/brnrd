"""Execute the installed service argv with its own prefix, source and home."""
import json
import os
import plistlib
from pathlib import Path
import shutil
import subprocess
import sys
import venv

import pytest

from brr.daemon_install import macos
from brr.loom.runtime import entry, launcher, service
from _step import wait_until


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def installed(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    package = source / 'src/brr'
    shutil.copytree(Path(entry.__file__).parents[2], package,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    git(tmp_path, 'init', '-q', str(source))
    (source / '.gitignore').write_text('__pycache__/\n*.egg-info/\n')
    git(source, 'add', '.')
    git(source, 'commit', '-qm', 'healthy')
    good = git(source, 'rev-parse', 'HEAD')
    prefix = tmp_path / 'prefix'
    venv.EnvBuilder(system_site_packages=True).create(prefix)
    site = next(prefix.glob('lib/python*/site-packages'))
    (site / 'source.pth').write_text(str(source / 'src') + '\n')
    monkeypatch.setenv('BRNRD_LOOM_SOURCE', str(source))
    monkeypatch.delenv('PYTHONPATH', raising=False)
    monkeypatch.setattr(entry, "prefix", lambda: prefix)
    entry.write_shim()
    entry.point(prefix)
    home = tmp_path / 'home'
    (home / 'loom').mkdir(parents=True)
    (home / 'loom/config.toml').write_text('body = "fake"\nupdate = "off"\nconnectors = []\n')
    unit = plistlib.loads(macos.render_plist(entry.shim(), home=tmp_path,
                         workdir=home, label=service.LABEL,
                         program_args=service._program(home)).encode())
    state = source / '.git/brnrd-startup.json'
    class Install:
        pass
    i = Install()
    i.source, i.home, i.good, i.state = source, home, good, state
    i.prefix, i.unit = prefix, unit
    def run(healthy=False):
        with (tmp_path / 'out').open('w+') as out:
            proc = subprocess.Popen(unit['ProgramArguments'], cwd=home,
                                    stdout=out, stderr=out)
            try:
                if healthy:
                    def acknowledged():
                        return (state.exists() and json.loads(state.read_text()).get('pid') == proc.pid
                                and json.loads(state.read_text()).get('failures') == 0)
                    wait_until(lambda: proc.poll() is not None or acknowledged(), 20,
                               lambda: (tmp_path / 'out').read_text())
                    assert proc.poll() is None, (tmp_path / 'out').read_text()
                    assert acknowledged()
                    proc.terminate()
                code = proc.wait(timeout=20)
                return code, (tmp_path / 'out').read_text()
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
    i.run = run
    return i


@pytest.mark.parametrize('failure', ['package', 'import', 'config', 'router'])
def test_three_failed_service_starts_restore_last_healthy_head(installed, failure):
    i = installed
    i.run(healthy=True)
    assert json.loads(i.state.read_text())['healthy'] == i.good
    path = (i.source / 'src/brr/__init__.py' if failure == 'package' else
            i.source / 'src/brr/loom/runtime' / {
                'import': 'loom.py', 'config': 'config.py', 'router': 'router.py'}[failure])
    if failure in ('import', 'package'):
        path.write_text("raise RuntimeError('bad import')\n")
    elif failure == 'config':
        path.write_text(path.read_text() + "\ndef load_config(*args, **kwargs):\n    raise ValueError('bad config')\n")
    else:
        path.write_text(path.read_text() + "\nclass Router:\n    def __init__(self, *args, **kwargs):\n        raise ValueError('bad router')\n")
    git(i.source, 'add', '.')
    git(i.source, 'commit', '-qm', 'own broken code')
    bad = git(i.source, 'rev-parse', 'HEAD')
    for count in range(1, 4):
        code, output = i.run()
        assert code != 0, output
        state = json.loads(i.state.read_text())
        assert state['healthy'] == i.good and state['failures'] == count
        assert git(i.source, 'rev-parse', 'HEAD') == bad
    # This invocation is launchd/systemd's retry after the third failure.
    _, output = i.run(healthy=True)
    assert 'three starts failed' in output
    assert git(i.source, 'rev-parse', 'HEAD') == i.good
    assert git(i.source, 'rev-parse', f'refs/brnrd/failed-start/{bad}') == bad
    assert json.loads(i.state.read_text())['failures'] == 0
    from brr.loom.runtime.home import Home
    from brr.loom.runtime.ledger import read_facts
    letters = [f.data['body'] for f in read_facts(Home(i.home)) if f.kind == 'letter']
    assert any(i.good in body and bad in body and 'refs/brnrd/failed-start' in body for body in letters)
    # A later healthy service start still runs normally.
    i.run(healthy=True)


def test_dirty_checkout_is_never_reset_and_no_healthy_commit_is_invented(installed):
    i = installed
    broken = i.source / 'src/brr/__init__.py'
    broken.write_text("raise RuntimeError('first boot broken')\n")
    git(i.source, 'commit', '-qam', 'broken first boot')
    for _ in range(3):
        assert i.run()[0] != 0
    _, output = i.run()
    assert 'no different healthy commit' in output
    # Establish a real healthy start, then fail a committed change three times.
    git(i.source, 'reset', '--hard', i.good)
    i.run(healthy=True)
    broken.write_text("raise RuntimeError('broken again')\n")
    git(i.source, 'commit', '-qam', 'broken later')
    for _ in range(3):
        assert i.run()[0] != 0
    bad = git(i.source, 'rev-parse', 'HEAD')
    broken.write_text('# uncommitted repair\n')
    _, output = i.run(healthy=True)
    assert 'uncommitted changes' in output and 'starting current tree' in output
    assert broken.read_text() == '# uncommitted repair\n'
    assert git(i.source, 'rev-parse', 'HEAD') == bad
    assert json.loads(i.state.read_text())['healthy'] == i.good


def test_dependency_metadata_is_compared_in_both_directions(installed):
    i = installed
    (i.source / 'pyproject.toml').write_text('[project]\nname = "example"\n')
    git(i.source, 'add', '.')
    git(i.source, 'commit', '-qm', 'dependencies')
    tip = git(i.source, 'rev-parse', 'HEAD')
    assert launcher.dependencies(i.source, i.good, tip)
    assert launcher.dependencies(i.source, tip, i.good)
    assert not launcher.dependencies(i.source, tip, tip)


def test_status_checks_the_loaded_program_not_only_the_plist(tmp_path, capsys):
    path = macos.plist_path(home=tmp_path, label=service.LABEL)
    path.parent.mkdir(parents=True)
    path.write_text(macos.render_plist(entry.shim(), home=tmp_path, workdir=tmp_path))
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, 'state = running\npid = 42\nprogram = /old/bin/python\n', '')
    assert service.main(['status'], system='Darwin', agents_home=tmp_path,
                        run=run, pid_alive=lambda pid: True) == 0
    assert 'loaded service starts /old/bin/python' in capsys.readouterr().err


def test_real_dependency_install_targets_only_the_install_prefix(installed, tmp_path):
    """A local build backend and dependency wheel require no index or build downloads."""
    import zipfile
    i = installed
    wheel = tmp_path / 'way_back_dependency-1.0-py3-none-any.whl'
    info = 'way_back_dependency-1.0.dist-info'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('way_back_dependency.py', 'VALUE = 42\n')
        archive.writestr(info + '/METADATA', 'Metadata-Version: 2.1\nName: way-back-dependency\nVersion: 1.0\n')
        archive.writestr(info + '/WHEEL', 'Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        archive.writestr(info + '/RECORD', '')
    (i.source / 'pyproject.toml').write_text(
        '[build-system]\nrequires = []\nbuild-backend = "backend"\nbackend-path = ["."]\n')
    (i.source / 'backend.py').write_text('''
from pathlib import Path
from zipfile import ZipFile

def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    name = 'way_back_install-1.0-py3-none-any.whl'
    info = 'way_back_install-1.0.dist-info/'
    requirement = Path('dependency.txt').read_text().strip()
    with ZipFile(str(Path(wheel_directory) / name), 'w') as z:
        z.writestr('way_back_install.pth', str(Path('src').resolve()) + '\\n')
        z.writestr(info + 'METADATA', 'Metadata-Version: 2.1\\nName: way-back-install\\nVersion: 1.0\\n'
                   + ('Requires-Dist: ' + requirement + '\\n' if requirement else ''))
        z.writestr(info + 'WHEEL', 'Wheel-Version: 1.0\\nGenerator: test\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n')
        z.writestr(info + 'RECORD', '')
    return name
''')
    (i.source / 'dependency.txt').write_text(f'way-back-dependency @ {wheel.as_uri()}')
    python = i.prefix / 'bin/python'
    absent = subprocess.run([str(python), '-I', '-c', 'import way_back_dependency'], capture_output=True)
    assert absent.returncode != 0
    launcher.reinstall(i.source, python)
    checked = subprocess.run([str(python), '-I', '-c',
                              'import way_back_dependency as d; assert d.VALUE == 42; print(d.__file__)'],
                             capture_output=True, text=True)
    assert checked.returncode == 0, checked.stderr
    assert str(i.prefix) in checked.stdout
    # Exercise the service after the dependency became importable in its prefix.
    path = i.source / 'src/brr/loom/runtime/loom.py'
    path.write_text(path.read_text() + '\nimport way_back_dependency\n')
    git(i.source, 'add', '.')
    git(i.source, 'commit', '-qm', 'new dependency')
    i.run(healthy=True)


def test_a_healthy_start_breaks_the_failure_streak(installed):
    i = installed
    i.run(healthy=True)
    (i.source / 'own.py').write_text('# own change\n')
    git(i.source, 'add', '.')
    git(i.source, 'commit', '-qm', 'own code')
    tip = git(i.source, 'rev-parse', 'HEAD')
    config = i.home / 'loom/config.toml'
    good_config = config.read_text()
    config.write_text('not valid TOML !\n')
    for _ in range(2):
        assert i.run()[0] != 0
    assert json.loads(i.state.read_text())['failures'] == 2
    config.write_text(good_config)
    i.run(healthy=True)
    assert json.loads(i.state.read_text())['healthy'] == tip
    assert json.loads(i.state.read_text())['failures'] == 0
    assert git(i.source, 'rev-parse', 'HEAD') == tip


@pytest.mark.parametrize('rollback', [False, True])
def test_failed_pending_reinstall_still_starts_and_keeps_the_retry_marker(installed, rollback):
    i = installed
    i.run(healthy=True)
    state = json.loads(i.state.read_text())
    if rollback:
        broken = i.source / 'src/brr/__init__.py'
        broken.write_text("raise RuntimeError('bad package')\n")
        git(i.source, 'commit', '-qam', 'broken code with unchanged dependencies')
        state.update(head=git(i.source, 'rev-parse', 'HEAD'), failures=3)
    state['reinstall'] = True
    i.state.write_text(json.dumps(state))
    # Fail only the independently installed guard's installer, then execute the
    # generated unit command. The product startup and health acknowledgement run.
    guard = entry.root() / 'launcher.py'
    text = guard.read_text()
    begin, end = text.index('def reinstall('), text.index('def healthy(')
    guard.write_text(text[:begin] + "def reinstall(root, python):\n    raise RuntimeError('offline')\n\n\n" + text[end:])
    _, output = i.run(healthy=True)
    assert 'dependency reinstall failed (offline)' in output
    assert 'starting with installed dependencies' in output
    state = json.loads(i.state.read_text())
    assert state['reinstall'] is True and state['failures'] == 0


@pytest.mark.parametrize("args", [
    ["status"], ["check", "--help"], ["--help"], ["-h"],
    ["--home", "/unused", "--help"],
])
def test_non_start_loom_commands_do_not_change_startup_state(installed, args):
    i = installed
    prior = '{"failures": 2, "healthy": "keep"}\n'
    i.state.write_text(prior)
    proc = subprocess.run(
        [str(entry.shim()), "runtime", "loom", *args],
        capture_output=True, text=True, timeout=20,
    )
    assert proc.returncode in (0, 2)
    assert i.state.read_text() == prior

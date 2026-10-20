"""Home discovery and config edits at the CLI/editor boundary."""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from brr.loom.runtime import service, verbs_loom, verbs_self, verbs_threads
from brr.loom.runtime.config import config_path, load_config
from brr.loom.runtime.home import resolve_home


@pytest.fixture
def user_home(tmp_path, monkeypatch):
    user = tmp_path / 'user'
    user.mkdir()
    monkeypatch.setattr(Path, 'home', lambda: user)
    monkeypatch.setenv('HOME', str(user))
    monkeypatch.delenv('BRNRD_HOME', raising=False)
    monkeypatch.delenv('BRNRD_ROOM', raising=False)
    return user


def test_resolver_order_and_expansion(user_home, monkeypatch):
    default = user_home / 'brnrd'
    config_path(default).parent.mkdir(parents=True)
    config_path(default).write_text('')
    assert resolve_home() == (default, 'default')
    monkeypatch.setenv('BRNRD_HOME', '~/env-home')
    assert resolve_home() == (user_home / 'env-home', 'env')
    assert resolve_home('~/flag-home') == (user_home / 'flag-home', 'flag')
    monkeypatch.setenv('BRNRD_HOME', '')
    assert resolve_home() == (default, 'default')


@pytest.mark.parametrize('marker', ['loom/install-id', 'self/core/loom/wake'])
def test_default_recognises_existing_home(user_home, marker):
    path = user_home / 'brnrd' / marker
    path.parent.mkdir(parents=True)
    path.write_text('seed\n')
    assert resolve_home() == (user_home / 'brnrd', 'default')


def test_resolver_refusal_names_all_three_places(user_home):
    (user_home / 'brnrd').mkdir()
    with pytest.raises(SystemExit) as err:
        resolve_home()
    assert '--home' in str(err.value)
    assert 'BRNRD_HOME' in str(err.value)
    assert str(user_home / 'brnrd') in str(err.value)
    assert not list((user_home / 'brnrd').iterdir())


@pytest.mark.parametrize('source', ['flag', 'env', 'default'])
def test_where_prints_existing_layout_without_creating_it(user_home, monkeypatch, capsys, source):
    home = user_home / 'brnrd'
    args = ['where']
    if source == 'flag':
        args += ['--home', str(home)]
    elif source == 'env':
        monkeypatch.setenv('BRNRD_HOME', str(home))
    else:
        config_path(home).parent.mkdir(parents=True)
        config_path(home).write_text('')
    before = set(home.rglob('*'))
    assert service.main(args) == 0
    out = capsys.readouterr().out
    for line in [f'home: {home}', f'source: {source}', f'config: {config_path(home)}',
                 f'self: {home / "self"}', f'ledger: {home / "ledger"}',
                 f'rooms: {home / "rooms"}', f'logs: {home / "loom/loom.log"}',
                 f'body logs: {home / "rooms/<strand>/port/body.log"}']:
        assert line in out
    assert set(home.rglob('*')) == before


def test_config_uses_env_home_and_keeps_origin_and_help(user_home, monkeypatch, capsys):
    config_path(user_home).parent.mkdir()
    config_path(user_home).write_text('margin = 2\n')
    monkeypatch.setenv('BRNRD_HOME', str(user_home))
    assert service.main(['config']) == 0
    out = capsys.readouterr().out
    assert '= 2 · file · Extra seconds' in out
    assert '= 30 · default · Seconds a router lease' in out


def test_runtime_where_dispatch(user_home):
    proc = subprocess.run([sys.executable, '-m', 'brr.loom.runtime', 'where', '--home', str(user_home)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert f'home: {user_home}' in proc.stdout


def test_runtime_verbs_resolve_env(user_home, monkeypatch, capsys):
    monkeypatch.setenv('BRNRD_HOME', str(user_home))
    seen = []
    monkeypatch.setattr(verbs_loom, 'run_loom', lambda home, **kw: seen.append(home))
    monkeypatch.setattr(verbs_self, 'init_self', lambda home, **kw: seen.append(home) or 'seeded')
    assert verbs_loom._loom(['--adapter', 'fake']) == 0
    assert verbs_self.cmd_init([]) == 0
    assert seen == [user_home, user_home]
    assert verbs_loom._attention([]) == 0
    assert verbs_threads._threads([]) == 0
    from brr.loom.runtime.check import run
    assert run([]) == 0


def test_threads_room_takes_priority_over_env_home(user_home, monkeypatch, capsys):
    room = user_home / 'rooms' / 'strand'
    monkeypatch.setenv('BRNRD_ROOM', str(room))
    monkeypatch.setenv('BRNRD_HOME', str(user_home / 'other'))
    seen = []
    monkeypatch.setattr('brr.loom.runtime.ledger.read_facts', lambda home: seen.append(home.root) or [])
    assert verbs_threads._threads([]) == 0
    assert seen == [user_home]
    assert not (user_home / 'other').exists()


def test_service_install_freezes_env_home(user_home, monkeypatch):
    import plistlib
    from brr.daemon_install import macos
    config_path(user_home).parent.mkdir()
    config_path(user_home).write_text('body = "fake/echo"\n')
    monkeypatch.setenv('BRNRD_HOME', str(user_home))
    assert service.main(['install', '--no-start', '--dev'], system='Darwin', agents_home=user_home / 'agents') == 0
    unit = plistlib.loads(macos.plist_path(home=user_home / 'agents', label=service.LABEL).read_bytes())
    argv = unit['ProgramArguments']
    assert argv[-2:] == ['--home', str(user_home.resolve())]
    assert Path(argv[-1]).is_absolute()


def _editor(tmp_path, monkeypatch, body, *, variable='EDITOR'):
    script = tmp_path / 'editor script.py'
    script.write_text('import sys\nfrom pathlib import Path\np = Path(sys.argv[-1])\n' + body)
    monkeypatch.delenv('EDITOR', raising=False)
    monkeypatch.delenv('VISUAL', raising=False)
    monkeypatch.setenv(variable, f'{shlex.quote(sys.executable)} {shlex.quote(str(script))}')


@pytest.mark.parametrize('variable', ['EDITOR', 'VISUAL'])
def test_clean_edit_seeds_and_validates(user_home, monkeypatch, variable):
    _editor(user_home, monkeypatch,
            'assert "# margin = " in p.read_text()\np.write_text("margin = 4\\n")\n', variable=variable)
    assert service.main(['config', 'edit', '--home', str(user_home)]) == 0
    assert load_config(user_home).margin == 4
    assert config_path(user_home).read_text() == 'margin = 4\n'
    assert not list(config_path(user_home).parent.glob('.config-edit-*'))


def test_public_cli_config_edit_reaches_editor(user_home, monkeypatch):
    from brr.cli import main
    _editor(user_home, monkeypatch, 'p.write_text("margin = 4\\n")\n')
    monkeypatch.setenv('BRNRD_HOME', str(user_home))
    main(['loom', 'config', 'edit'])
    assert load_config(user_home).margin == 4


def test_bad_edit_then_fixed_preserves_live_file(user_home, monkeypatch, capsys):
    path = config_path(user_home)
    path.parent.mkdir()
    path.write_text('margin = 2\n')
    path.chmod(0o600)
    marker = user_home / 'edited-once'
    _editor(user_home, monkeypatch, f'''
live = Path({str(path)!r})
assert live.read_text() == 'margin = 2\\n'
marker = Path({str(marker)!r})
if not marker.exists():
    p.write_text('margn = 4\\n')
    marker.touch()
else:
    text = p.read_text()
    assert text.startswith('# Config error')
    assert 'did you mean margin?' in text
    assert str(live) in text
    p.write_text(text.replace('margn = 4', 'margin = 4'))
''')
    assert service.main(['config', '--home', str(user_home), 'edit']) == 0
    assert path.read_text() == 'margin = 4\n'
    assert path.stat().st_mode & 0o777 == 0o600
    assert 'unknown key' in capsys.readouterr().err


def test_bad_edit_then_abandoned_keeps_last_good_file(user_home, monkeypatch, capsys):
    path = config_path(user_home)
    path.parent.mkdir()
    path.write_text('margin = 2\n')
    marker = user_home / 'edited-once'
    _editor(user_home, monkeypatch, f'''
marker = Path({str(marker)!r})
if not marker.exists():
    p.write_text('margin = -1\\n')
    marker.touch()
else:
    assert p.read_text().startswith('# Config error')
''')
    assert service.main(['config', 'edit', '--home', str(user_home)]) == 1
    assert path.read_text() == 'margin = 2\n'
    assert load_config(user_home).margin == 2
    assert 'abandoned' in capsys.readouterr().err


def test_editor_failure_keeps_file(user_home, monkeypatch, capsys):
    path = config_path(user_home)
    path.parent.mkdir()
    path.write_text('margin = 2\n')
    _editor(user_home, monkeypatch, 'p.write_text("margin = 4\\n")\nsys.exit(2)\n')
    assert service.main(['config', 'edit', '--home', str(user_home)]) == 1
    assert path.read_text() == 'margin = 2\n'
    assert 'editor exited 2' in capsys.readouterr().err


def test_vi_is_the_fallback(user_home, monkeypatch):
    script = user_home / 'vi'
    script.write_text('#!/bin/sh\nprintf "margin = 4\\n" > "$1"\n')
    script.chmod(0o755)
    monkeypatch.delenv('EDITOR', raising=False)
    monkeypatch.delenv('VISUAL', raising=False)
    monkeypatch.setenv('PATH', str(user_home) + os.pathsep + os.environ['PATH'])
    assert service.main(['config', 'edit', '--home', str(user_home)]) == 0
    assert load_config(user_home).margin == 4

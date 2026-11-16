"""Installer activation uses isolated roots and service doubles, never host units."""
import json
from pathlib import Path

import pytest

from brr.loom.runtime import entry, install_checkout as install


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    root = tmp_path / 'root'
    prefix = root / 'envs' / 'new'
    (prefix / 'bin').mkdir(parents=True)
    python = prefix / 'bin/python'
    python.write_text('#!/bin/sh\n')
    python.chmod(0o755)
    source = tmp_path / 'source'
    source.mkdir()
    monkeypatch.setenv('BRNRD_ROOT', str(root))
    monkeypatch.setattr(entry, 'prefix', lambda: prefix)
    monkeypatch.setattr(install, 'prepare', lambda path: 'head')
    monkeypatch.setattr(install, 'git', lambda *args: 'head')
    monkeypatch.setattr(install.service, '_platform', lambda _: 'Linux')
    unit = tmp_path / 'loom.service'
    monkeypatch.setattr(install.service.linux, 'unit_path', lambda _: unit)
    def probe(args, **kwargs):
        return json.dumps([str(prefix), str(source / 'src/brr/__init__.py')])
    monkeypatch.setattr(install.subprocess, 'check_output', probe)
    monkeypatch.setattr(install.subprocess, 'run', lambda *args, **kwargs: None)
    # Exercise the real shim and link writer, with its source discovery isolated.
    from brr.loom.runtime import receipts
    monkeypatch.setattr(receipts, 'source_root', lambda: source)
    return source, prefix, unit


def test_fresh_install_activates_exact_prefix_without_installing_service(candidate, monkeypatch):
    source, prefix, unit = candidate
    monkeypatch.setattr(install.service, 'main', lambda *a: pytest.fail('fresh install touched service'))
    install.activate(source)
    assert entry.current().resolve() == prefix
    assert entry.sound()
    assert not unit.exists()


def test_success_keeps_current_and_the_one_before_and_prunes_older_installer_envs(candidate, monkeypatch, tmp_path, capsys):
    source, prefix, unit = candidate
    envs = prefix.parent
    made = {name: envs / name for name in ['install-older', 'install-before', 'install-failed']}
    for path in made.values():
        (path / 'bin').mkdir(parents=True)
        (path / 'bin/python').write_text('#!/bin/sh\n')
    hand = envs / 'hand-made'  # not installer-made: never removed
    hand.mkdir()
    outside = tmp_path / 'uv-tool-prefix'
    outside.mkdir()
    (envs / 'install-link').symlink_to(outside)
    entry.point(made['install-before'])
    monkeypatch.setattr(install.service, 'main', lambda *a: pytest.fail('fresh install touched service'))
    install.activate(source)
    assert entry.current().resolve() == prefix
    assert sorted(path.name for path in envs.iterdir()) == ['hand-made', 'install-before', 'install-link', 'new']
    assert outside.is_dir()
    out = capsys.readouterr().out
    assert f'previous prefix retained: {made["install-before"]}' in out
    assert 'service: none installed' in out


def test_failed_activation_prunes_nothing(candidate, monkeypatch):
    source, prefix, unit = candidate
    older = prefix.parent / 'install-older'
    older.mkdir(parents=True)
    old = old_install(candidate)
    monkeypatch.setattr(install.service, '_installed_home', lambda *a: Path('/isolated/home'))
    monkeypatch.setattr(install.service, 'main', lambda args: int(args[0] == 'install'))
    with pytest.raises(RuntimeError):
        install.activate(source)
    assert older.is_dir() and entry.current().resolve() == old


def old_install(candidate):
    source, prefix, unit = candidate
    old = prefix.parent / 'old'
    (old / 'bin').mkdir(parents=True)
    (old / 'bin/python').write_text('old interpreter')
    entry.point(old)
    entry.shim().parent.mkdir(parents=True)
    entry.shim().write_text('old shim')
    unit.write_text('old unit with custom environment')
    return old


def test_existing_service_handoff_and_readback_keep_old_prefix(candidate, monkeypatch, capsys):
    source, prefix, unit = candidate
    old = old_install(candidate)
    monkeypatch.setattr(install.service, '_installed_home', lambda *a: Path('/isolated/home'))
    calls = []
    def service(args):
        calls.append(args)
        if args[0] == 'install':
            entry.write_shim()
            entry.point(prefix)
            unit.write_text('candidate unit')
        return 0
    monkeypatch.setattr(install.service, 'main', service)
    install.activate(source)
    assert calls == [['install', '--home', '/isolated/home'], ['status']]
    assert entry.current().resolve() == prefix
    assert (old / 'bin/python').read_text() == 'old interpreter'
    assert f'service: moved from {old} to {prefix}' in capsys.readouterr().out


@pytest.mark.parametrize('failure', ['install', 'status', 'probe'])
def test_failed_handoff_restores_unit_current_and_launcher_before_restart(candidate, monkeypatch, failure):
    source, prefix, unit = candidate
    old = old_install(candidate)
    monkeypatch.setattr(install.service, '_installed_home', lambda *a: Path('/isolated/home'))
    calls = []
    def service(args):
        calls.append(args[0])
        if args[0] == 'restart':
            assert entry.current().resolve() == old
            assert unit.read_text() == 'old unit with custom environment'
            assert entry.shim().read_text() == 'old shim'
            return 0
        if args[0] == 'install':
            entry.write_shim()
            entry.point(prefix)
            unit.write_text('candidate unit')
        return int(args[0] == failure)
    monkeypatch.setattr(install.service, 'main', service)
    if failure == 'probe':
        monkeypatch.setattr(install.subprocess, 'check_output', lambda *a, **kw: '[]')
    with pytest.raises((RuntimeError, ValueError)):
        install.activate(source)
    assert calls[-1] == 'restart'
    assert (old / 'bin/python').exists()
    assert entry.current().resolve() == old


def test_source_validation_failure_precedes_current_or_service_writes(candidate, monkeypatch):
    source, prefix, unit = candidate
    old = old_install(candidate)
    def unavailable(_):
        raise RuntimeError('candidate source mismatch')
    monkeypatch.setattr(install, 'prepare', unavailable)
    monkeypatch.setattr(install.service, 'main', lambda *a: pytest.fail('service touched'))
    with pytest.raises(RuntimeError, match='source mismatch'):
        install.activate(source)
    assert entry.current().resolve() == old
    assert unit.read_text() == 'old unit with custom environment'


def test_prepare_fetches_matching_board_without_npm(tmp_path, monkeypatch):
    import brr
    from brr.loom.runtime import board_asset, spabuild
    # Keep package identity real; only use a temporary frontend for downloads.
    source = Path(brr.__file__).resolve().parents[2]
    frontend = tmp_path / 'frontend'
    frontend.mkdir()
    spa = spabuild.SpaBuild(frontend, lambda _: None)
    def wanted():
        spa.upstream = ('a' * 40, 'b' * 40)
        spa.changed = False
        return 'b' * 40
    monkeypatch.setattr(spa, 'wanted', wanted)
    monkeypatch.setattr(install, 'SpaBuild', lambda *args: spa)
    monkeypatch.setattr(install, 'managed_source', lambda _: True)
    monkeypatch.setattr(install, 'git', lambda *args: 'a' * 40)
    monkeypatch.setattr(spabuild.shutil, 'which', lambda _: None)
    def fetch(commit, tree, destination):
        assert commit == 'a' * 40 and tree == 'b' * 40
        destination.mkdir()
        (destination / '200.html').write_text('board')
        (destination / '.tree').write_text(tree)
    monkeypatch.setattr(board_asset, 'fetch', fetch)
    assert install.prepare(source) == 'a' * 40
    assert (spa.build / '200.html').read_text() == 'board'
    # Public board lookup reaches the actual downloaded file in this fixture.
    from brr.loom.runtime.board import spa_file
    assert spa_file(spa.build, '/threads/example') == spa.build / '200.html'


def test_prepare_allows_missing_matching_board(tmp_path, monkeypatch):
    import brr
    source = Path(brr.__file__).resolve().parents[2]
    class MissingBoard:
        upstream = ('a' * 40, 'b' * 40)
        build = tmp_path / 'missing'
        def wanted(self): return 'b' * 40
        def built(self): return ''
        def _build(self, _): pass
    monkeypatch.setattr(install, 'SpaBuild', lambda *a: MissingBoard())
    monkeypatch.setattr(install, 'managed_source', lambda _: True)
    monkeypatch.setattr(install, 'git', lambda *a: 'a' * 40)
    assert install.prepare(source) == 'a' * 40


@pytest.mark.parametrize('system', ['Linux', 'Darwin'])
def test_migration_runs_real_service_install_with_isolated_manager(candidate, monkeypatch, tmp_path, system):
    import plistlib
    from test_loom_service import _Linux, _Mac
    source, prefix, unit = candidate
    old = old_install(candidate)
    home = tmp_path / 'resident-home'
    (home / 'loom').mkdir(parents=True)
    (home / 'loom/config.toml').write_text('body = "codex"\n')
    monkeypatch.setattr(install.service, '_platform', lambda _: system)
    monkeypatch.setattr(install.service, '_agents', lambda _: tmp_path)
    monkeypatch.setattr(install.service.macos, 'plist_path', lambda **kw: unit)
    monkeypatch.setattr(install.service, '_brr_source', lambda: None)
    monkeypatch.setattr(install.service.linux, 'supported', lambda: True)
    monkeypatch.setattr(install.service, '_settle_body', lambda *a, **kw: ('codex', 'existing'))
    monkeypatch.setattr(install.service, '_settle_forge', lambda *a, **kw: 'existing')
    monkeypatch.setenv('PATH', '/new/tools:/usr/bin')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    old_path = f'{old}/bin:/kept/tools'
    if system == 'Linux':
        unit.write_text(f'[Service]\nWorkingDirectory={home}\nEnvironment=CUSTOM=kept\nEnvironment="PATH={old_path}"\n')
        manager = _Linux()
    else:
        unit.write_bytes(plistlib.dumps({'WorkingDirectory': str(home),
                         'EnvironmentVariables': {'CUSTOM': 'kept', 'PATH': old_path}}))
        manager = _Mac()
    real_main = install.service.main
    calls = []
    def through_service(args):
        calls.append(args[0])
        return real_main(args, run=manager.run, sleep=manager.sleep,
                         pid_alive=manager.pid_alive, poll_timeout=0, system=system)
    monkeypatch.setattr(install.service, 'main', through_service)
    install.activate(source)
    assert calls == ['install', 'status']
    assert entry.current().resolve() == prefix
    assert (old / 'bin/python').exists()
    if system == 'Linux':
        text = unit.read_text()
        assert 'Environment=CUSTOM=kept' in text
        assert str(entry.shim()) in text
        assert f'{entry.current()}/bin:/kept/tools:/new/tools:/usr/bin' in text
    else:
        payload = plistlib.loads(unit.read_bytes())
        assert payload['ProgramArguments'][0] == str(entry.shim())
        assert payload['EnvironmentVariables']['CUSTOM'] == 'kept'
        assert payload['EnvironmentVariables']['PATH'].endswith(f'{entry.current()}/bin:/kept/tools:/new/tools:/usr/bin')

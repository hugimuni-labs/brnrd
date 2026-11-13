"""Drive the console-script entry with isolated PATHs, without invoking alternatives."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

import brr


@pytest.fixture
def installed(tmp_path):
    entry = tmp_path / 'active' / 'brnrd'
    entry.parent.mkdir()
    entry.write_text(f'#!{sys.executable}\nfrom brr.cli import main\nmain()\n')
    entry.chmod(0o755)
    return entry


def run_cli(entry, path, *args):
    env = dict(os.environ, PATH=os.pathsep.join(map(str, path)),
               PYTHONPATH=str(Path(brr.__file__).parent.parent))
    return subprocess.run([str(entry), *args], env=env, text=True, capture_output=True)


def alternative(tmp_path):
    entry = tmp_path / 'old' / 'brnrd'
    entry.parent.mkdir()
    entry.write_text('#!/bin/sh\nexit 97\n')
    entry.chmod(0o755)
    return entry


@pytest.mark.parametrize('args', [('--help',), ('loom', '--help'), ('runtime', '--help')])
def test_cli_names_invoked_package_and_alternatives(installed, tmp_path, args):
    old = alternative(tmp_path)
    before = {p: (p.read_bytes(), p.stat().st_mode) for p in (installed, old)}
    # Invoke explicitly even though the stale entry is first on PATH.
    result = run_cli(installed, [old.parent, installed.parent], *args)
    assert result.returncode == 0, result.stderr
    assert result.stderr.count('warning: multiple brnrd installations') == 1
    assert f'invoked entry: {installed}' in result.stderr
    assert f'imported package: {brr.__file__}' in result.stderr
    assert result.stderr.index(f'    {old}') < result.stderr.index(f'    {installed}')
    assert {p: (p.read_bytes(), p.stat().st_mode) for p in before} == before


def test_single_install_symlinks_repeated_path_and_nonexecutables_are_quiet(installed, tmp_path):
    alias = tmp_path / 'alias'
    alias.mkdir()
    (alias / 'brnrd').symlink_to(installed)
    ignored = alternative(tmp_path)
    ignored.chmod(0o644)
    broken = tmp_path / 'broken'
    broken.mkdir()
    (broken / 'brnrd').symlink_to(tmp_path / 'missing')
    result = run_cli(installed, [alias, installed.parent, alias, ignored.parent, broken], '--help')
    assert result.returncode == 0, result.stderr
    assert result.stderr == ''


def test_distinct_install_with_alias_is_reported_once(installed, tmp_path):
    old = alternative(tmp_path)
    alias = tmp_path / 'alias'
    alias.mkdir()
    (alias / 'brnrd').symlink_to(old)
    result = run_cli(installed, [installed.parent, alias, old.parent], '--help')
    assert result.returncode == 0
    assert f'{alias / "brnrd"} -> {old}' in result.stderr
    assert f'    {old}\n' not in result.stderr


def test_module_runtime_fast_path_warns(installed, tmp_path):
    old = alternative(tmp_path)
    env = dict(os.environ, PATH=os.pathsep.join(map(str, [installed.parent, old.parent])),
               PYTHONPATH=str(Path(brr.__file__).parent.parent))
    result = subprocess.run([sys.executable, '-m', 'brr', 'runtime', '--help'],
                            env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stderr.count('warning: multiple brnrd installations') == 1
    assert f'invoked entry: {sys.executable} -m brr' in result.stderr


def test_loom_shim_preserves_invoked_entry(installed, tmp_path, monkeypatch):
    from brr.loom.runtime import entry

    monkeypatch.setenv('BRNRD_ROOT', str(tmp_path / 'root'))
    prefix = tmp_path / 'prefix'
    (prefix / 'bin').mkdir(parents=True)
    (prefix / 'bin' / 'python').symlink_to(sys.executable)
    entry.point(prefix)
    entry.write_shim()
    old = alternative(tmp_path)
    result = run_cli(entry.shim(), [entry.shim().parent, old.parent], 'runtime', '--help')
    assert result.returncode == 0, result.stderr
    assert f'invoked entry: {entry.shim()}' in result.stderr
    assert result.stderr.count('warning: multiple brnrd installations') == 1


def test_empty_and_relative_path_entries_use_current_directory(installed, tmp_path):
    old = alternative(tmp_path)
    env = dict(os.environ, PATH=os.pathsep.join(['', '../old', '.']),
               PYTHONPATH=str(Path(brr.__file__).parent.parent))
    result = subprocess.run([str(installed), '--help'], cwd=installed.parent,
                            env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert 'warning: multiple brnrd installations' in result.stderr
    assert str(old) in result.stderr

"""Run the public POSIX installer with isolated HOME, tools and checkout."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

SCRIPT = Path(__file__).parents[1] / 'docs/public/install.sh'
SHELLS = [shell for shell in ['/bin/sh', '/bin/dash'] if Path(shell).exists()]
ORIGIN = 'https://github.com/hugimuni-labs/brnrd'


def tool(directory, name, text):
    path = directory / name
    path.write_text('#!/bin/sh\n' + text)
    path.chmod(0o755)


@pytest.fixture
def room(tmp_path):
    binary = tmp_path / 'bin'
    binary.mkdir()
    for name in ['grep', 'mktemp', 'mkdir', 'rm', 'sh', 'git']:
        (binary / name).symlink_to(shutil.which(name))
    tool(binary, 'claude', 'exit 0\n')
    tool(binary, 'gh', 'exit 0\n')
    tool(binary, 'uv', '''printf 'uv %s\\n' "$*" >> "$TRACE"
case "$1" in
  venv)
    mkdir -p "$2/bin"
    printf '#!/bin/sh\\nprintf "candidate %%s\\\\n" "$*" >> "$TRACE"\\n' > "$2/bin/python"
    chmod +x "$2/bin/python"
    ;;
esac
''')
    # The uv fixture uses chmod; every other host utility remains inaccessible.
    (binary / 'chmod').symlink_to(shutil.which('chmod'))
    env = {**os.environ, 'HOME': str(tmp_path / 'home'), 'PATH': str(binary),
           'TRACE': str(tmp_path / 'trace'), 'BRNRD_INSTALL_DRY_RUN': '1'}
    for key in ['BRNRD_ROOT', 'BRNRD_DEV_DIR', 'BRNRD_INSTALL_DEV', 'PYTHONPATH', 'PYTHONHOME']:
        env.pop(key, None)
    return tmp_path, binary, env


def run(room, shell='/bin/sh', args=(), dry=True):
    _, _, env = room
    return subprocess.run([shell, str(SCRIPT), *args], env={**env, 'BRNRD_INSTALL_DRY_RUN': str(int(dry))},
                          text=True, capture_output=True)


def checkout(room, origin=ORIGIN):
    tmp, _, env = room
    source = Path(env['HOME']) / '.brnrd/src'
    source.mkdir(parents=True)
    def git(*args):
        return subprocess.check_output(['git', '-C', str(source), *args], text=True).strip()
    git('init', '-q')
    git('remote', 'add', 'origin', origin)
    git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '--allow-empty', '-qm', 'resident')
    return source, git


@pytest.mark.parametrize('shell', SHELLS)
@pytest.mark.parametrize('args', [(), ('--dev',)])
def test_default_and_dev_prepare_same_checkout_and_separate_prefix(room, shell, args):
    result = run(room, shell, args)
    assert result.returncode == 0, result.stderr
    assert 'git clone --single-branch --branch main' in result.stdout
    assert 'uv venv ' in result.stdout
    assert 'uv pip install --python ' in result.stdout
    assert ' -I -m brr.loom.runtime.install_checkout ' in result.stdout
    assert 'uv tool install' not in result.stdout
    assert not (Path(room[2]['HOME']) / '.brnrd').exists()


@pytest.mark.parametrize('shell', SHELLS)
def test_rerun_preserves_branch_head_and_dirty_edits(room, shell):
    source, git = checkout(room)
    git('checkout', '-qb', 'resident-work')
    (source / 'own.txt').write_text('keep my edits')
    before = git('rev-parse', 'HEAD')
    result = run(room, shell, dry=False)
    assert result.returncode == 0, result.stderr
    assert git('rev-parse', 'HEAD') == before
    assert git('symbolic-ref', '--short', 'HEAD') == 'resident-work'
    assert (source / 'own.txt').read_text() == 'keep my edits'
    trace = (room[0] / 'trace').read_text()
    assert 'candidate -I -m brr.loom.runtime.install_checkout ' in trace
    assert 'tool install' not in trace and 'tool upgrade' not in trace


@pytest.mark.parametrize('kind', ['foreign', 'nongit'])
def test_invalid_existing_directory_refused_before_writes(room, kind):
    if kind == 'foreign':
        source, _ = checkout(room, 'https://example.com/foreign')
    else:
        source = Path(room[2]['HOME']) / '.brnrd/src'
        source.mkdir(parents=True)
    result = run(room, dry=False)
    assert result.returncode != 0
    assert not (source.parent / 'envs').exists()
    assert not (room[0] / 'trace').exists()


@pytest.mark.parametrize('kind', ['npm', 'pipx'])
def test_collision_fails_before_bootstrap_clone_or_environment(room, kind):
    tmp, binary, env = room
    if kind == 'pipx':
        tool(binary, 'pipx', 'printf "brnrd 1.2.3\\n"\n')
    else:
        npmroot = tmp / 'global_modules'
        (npmroot / 'brnrd').mkdir(parents=True)
        (npmroot / 'brnrd/package.json').write_text('{}')
        tool(binary, 'npm', f'printf "%s\\n" "{npmroot}"\n')
    result = run(room, dry=False)
    assert result.returncode != 0
    assert f'{kind} uninstall' in result.stderr
    assert not (Path(env['HOME']) / '.brnrd').exists()
    assert not (tmp / 'trace').exists()


def test_candidate_failure_keeps_old_prefix_and_stops(room):
    source, _ = checkout(room)
    old = source.parent / 'envs/old'
    old.mkdir(parents=True)
    (old / 'proof').write_text('retained')
    tool(room[1], 'uv', 'exit 1\n')
    result = run(room, dry=False)
    assert result.returncode != 0
    assert (old / 'proof').read_text() == 'retained'
    assert not (source.parent / 'current').exists()


def test_unknown_option_refuses_without_writes(room):
    result = run(room, args=('--stable',))
    assert result.returncode != 0
    assert 'Unknown option' in result.stderr

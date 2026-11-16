"""Run the public POSIX installer with isolated HOME, tools and checkout."""
import json
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
    for name in ['grep', 'mktemp', 'mkdir', 'rm', 'sh', 'git', 'sed', 'env']:
        (binary / name).symlink_to(shutil.which(name))
    tool(binary, 'claude', 'exit 0\n')
    tool(binary, 'gh', 'exit 0\n')
    tool(binary, 'uv', '''printf 'uv %s\\n' "$*" >> "$TRACE"
case "$1" in
  tool) [ "$2" = list ] && [ -n "${UV_TOOLS:-}" ] && printf '%s\\n' "$UV_TOOLS" ;;
  venv)
    mkdir -p "$2/bin"
    printf '#!/bin/sh\\nprintf "candidate %%s\\\\n" "$*" >> "$TRACE"\\n' > "$2/bin/python"
    chmod +x "$2/bin/python"
    ;;
esac
''')
    # The uv fixture uses chmod; every other host utility remains inaccessible.
    (binary / 'chmod').symlink_to(shutil.which('chmod'))
    # A fresh login shell: system PATH, then whatever the person's profile adds.
    tool(binary, 'loginsh', 'PATH=/usr/bin:/bin\n[ -f "$HOME/.profile" ] && . "$HOME/.profile"\neval "$2"\n')
    env = {**os.environ, 'HOME': str(tmp_path / 'home'), 'PATH': str(binary), 'SHELL': str(binary / 'loginsh'),
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


@pytest.mark.parametrize('step', ['venv', 'pip'])
def test_candidate_failure_keeps_old_prefix_and_removes_the_candidate(room, step):
    source, _ = checkout(room)
    envs = source.parent / 'envs'
    old = envs / 'install-old'
    old.mkdir(parents=True)
    (old / 'proof').write_text('retained')
    (source.parent / 'current').symlink_to(old)
    tool(room[1], 'uv', f'''[ "$1" = {step} ] && exit 1
[ "$1" = venv ] && mkdir -p "$2/bin"
exit 0
''')
    result = run(room, dry=False)
    assert result.returncode != 0
    assert (old / 'proof').read_text() == 'retained'
    assert (source.parent / 'current').resolve() == old.resolve()
    assert sorted(path.name for path in envs.iterdir()) == ['install-old']


def shell_profile(room, *directories):
    home = Path(room[2]['HOME'])
    home.mkdir(parents=True, exist_ok=True)
    (home / '.profile').write_text(''.join(f'PATH="{d}:$PATH"\n' for d in directories))


def test_old_uv_tool_brnrd_ahead_on_path_is_named_and_kept(room):
    tmp, _, env = room
    source, _ = checkout(room)
    home = Path(env['HOME'])
    old = home / '.local/bin'
    old.mkdir(parents=True)
    tool(old, 'brnrd', 'echo packaged\n')
    shell_profile(room, home / '.brnrd/bin', old)  # the uv tool directory wins
    env['UV_TOOLS'] = 'brnrd v0.9.0'
    result = run(room, dry=False)
    assert result.returncode == 0, result.stderr
    assert 'candidate -I -m brr.loom.runtime.install_checkout' in (tmp / 'trace').read_text()
    last = result.stdout.strip().splitlines()[-2:]
    assert str(old / 'brnrd') in last[0] and 'uv tool uninstall brnrd' in last[1]
    assert (old / 'brnrd').exists()
    assert 'tool uninstall' not in (tmp / 'trace').read_text()


def test_shim_first_on_path_needs_no_warning(room):
    _, _, env = room
    checkout(room)
    home = Path(env['HOME'])
    shim = home / '.brnrd/bin'
    shim.mkdir(parents=True)
    tool(shim, 'brnrd', 'exit 0\n')
    old = home / '.local/bin'
    old.mkdir(parents=True)
    tool(old, 'brnrd', 'echo packaged\n')
    shell_profile(room, old, shim)
    env['UV_TOOLS'] = 'brnrd v0.9.0'
    result = run(room, dry=False)
    assert result.returncode == 0, result.stderr
    assert 'uv tool uninstall' not in result.stdout + result.stderr


@pytest.mark.parametrize('shape', ['launcher-source', 'current'])
def test_machine_running_from_another_checkout_is_refused_before_writes(room, shape):
    tmp, _, env = room
    home = Path(env['HOME'])
    other = tmp / 'Projects/brnrd'
    other.mkdir(parents=True)
    for args in (['init', '-q'], ['remote', 'add', 'origin', ORIGIN],
                 ['-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '--allow-empty', '-qm', 'own work']):
        subprocess.run(['git', '-C', str(other), *args], check=True)
    venv = other / '.venv/bin'
    venv.mkdir(parents=True)
    tool(venv, 'python', f'printf "%s\\n" "{other}"\n')
    root = home / '.brnrd'
    root.mkdir(parents=True)
    (root / 'current').symlink_to(other / '.venv')
    if shape == 'launcher-source':
        (root / 'launcher-source').write_text(json.dumps({'source': str(other), 'prefix': str(other / '.venv')}))
    result = run(room, dry=False)
    assert result.returncode != 0
    assert f'BRNRD_DEV_DIR={other}' in result.stderr
    assert sorted(path.name for path in root.iterdir()) == sorted(
        ['current'] + (['launcher-source'] if shape == 'launcher-source' else []))
    assert not (tmp / 'trace').exists()
    # The line it names is the way through, and it keeps that checkout.
    env['BRNRD_DEV_DIR'] = str(other)
    result = run(room, dry=True)
    assert result.returncode == 0, result.stderr
    assert 'git clone' not in result.stdout and f'Keeping checkout HEAD and branch in {other}' in result.stdout


def test_unknown_option_refuses_without_writes(room):
    result = run(room, args=('--stable',))
    assert result.returncode != 0
    assert 'Unknown option' in result.stderr

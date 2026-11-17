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
    for name in ['grep', 'mktemp', 'mkdir', 'rm', 'sh', 'git', 'sed', 'env', 'id']:
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
    tool(binary, 'ps', 'printf "%s" "${FAKE_PS:-}"\n')
    tool(binary, 'loginsh', 'PATH=/usr/bin:/bin\n[ -f "$HOME/.profile" ] && . "$HOME/.profile"\neval "$2"\n')
    env = {**os.environ, 'HOME': str(tmp_path / 'home'), 'PATH': str(binary), 'SHELL': str(binary / 'loginsh'),
           'TRACE': str(tmp_path / 'trace'), 'BRNRD_INSTALL_DRY_RUN': '1'}
    for key in ['BRNRD_ROOT', 'BRNRD_DEV_DIR', 'BRNRD_INSTALL_DEV', 'PYTHONPATH', 'PYTHONHOME', 'XDG_CONFIG_HOME']:
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
    tmp, binary, env = room
    real = shutil.which('git')
    (binary / 'git').unlink()
    # A clone without the network: record it, make a checkout of main with the given origin.
    tool(binary, 'git', f'''if [ "$1" = clone ]; then
  printf 'git %s\\n' "$*" >> "$TRACE"
  eval "dest=\\${{$#}}"; eval "url=\\${{$(($# - 1))}}"
  {real} init -q -b main "$dest" && {real} -C "$dest" remote add origin "$url" &&
  exec {real} -C "$dest" -c user.name=t -c user.email=t@t commit -q --allow-empty -m upstream
fi
exec {real} "$@"
''')
    result = run(room, shell, args, dry=False)
    assert result.returncode == 0, result.stderr
    home = Path(env['HOME'])
    source = home / '.brnrd/src'
    git = lambda *a: subprocess.check_output([real, '-C', str(source), *a], text=True).strip()
    assert git('remote', 'get-url', 'origin') == ORIGIN
    assert git('symbolic-ref', '--short', 'HEAD') == 'main'
    trace = (tmp / 'trace').read_text().splitlines()
    assert f'git clone --single-branch --branch main {ORIGIN} {source}' in trace
    candidates = sorted((home / '.brnrd/envs').glob('install-*'))
    assert len(candidates) == 1 and (candidates[0] / 'bin/python').is_file()
    assert f'uv venv {candidates[0]} --python >=3.10' in trace  # never the first python3 on PATH (stock Mac: 3.9)
    assert f'uv pip install --python {candidates[0]}/bin/python --editable {source}' in trace
    assert f'candidate -I -m brr.loom.runtime.install_checkout {source}' in trace
    assert not any('tool install' in line for line in trace)


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


def fake_clone(binary):
    """A clone without the network: record it, make a checkout of the asked branch with the given origin."""
    real = shutil.which('git')
    (binary / 'git').unlink()
    tool(binary, 'git', f'''if [ "$1" = clone ]; then
  printf 'git %s\\n' "$*" >> "$TRACE"
  eval "dest=\\${{$#}}"; eval "url=\\${{$(($# - 1))}}"
  {real} init -q -b main "$dest" && {real} -C "$dest" remote add origin "$url" &&
  exec {real} -C "$dest" -c user.name=t -c user.email=t@t commit -q --allow-empty -m upstream
fi
case " $* " in *" fetch "*" +refs/heads/main:refs/remotes/origin/main "*)
  # upstream main, without the network: the clone's commit, where a release tag sits on main
  printf 'git %s\\n' "$*" >> "$TRACE"
  exec {real} -C "$2" update-ref refs/remotes/origin/main HEAD ;;
esac
exec {real} "$@"
''')
    return real


def test_a_release_ref_clones_that_tag_on_a_branch_and_follows_releases(room):
    tmp, binary, env = room
    real = fake_clone(binary)
    env['BRNRD_REF'] = 'v0.7.1'
    result = run(room, dry=False)
    assert result.returncode == 0, result.stderr
    source = Path(env['HOME']) / '.brnrd/src'
    git = lambda *a: subprocess.check_output([real, '-C', str(source), *a], text=True).strip()
    assert f'git clone --single-branch --branch v0.7.1 {ORIGIN} {source}' in (tmp / 'trace').read_text()
    assert git('symbolic-ref', '--short', 'HEAD') == 'stable'
    assert git('config', '--get', 'brnrd.update') == 'stable'
    # activation and the board take upstream as the merge base with origin/main, which a
    # single-branch tag clone lacks: without it install_checkout refuses the checkout.
    assert git('merge-base', 'HEAD', 'refs/remotes/origin/main') == git('rev-parse', 'HEAD')


def test_main_ref_sets_no_following_and_a_rerun_never_sets_it(room):
    tmp, binary, env = room
    real = fake_clone(binary)
    assert run(room, dry=False).returncode == 0
    source = Path(env['HOME']) / '.brnrd/src'
    unset = lambda: subprocess.run([real, '-C', str(source), 'config', '--get', 'brnrd.update']).returncode == 1
    assert unset()
    env['BRNRD_REF'] = 'v0.7.1'  # a release ref on an existing checkout: HEAD and its following stay
    assert run(room, dry=False).returncode == 0
    assert unset() and subprocess.check_output([real, '-C', str(source), 'symbolic-ref', '--short', 'HEAD'],
                                               text=True).strip() == 'main'


@pytest.mark.parametrize('ref', ['v0.8.0rc1', 'feature', 'v1'])
def test_a_ref_that_is_not_main_or_a_release_refuses_without_writes(room, ref):
    room[2]['BRNRD_REF'] = ref
    result = run(room, dry=False)
    assert result.returncode != 0 and 'BRNRD_REF' in result.stderr
    assert not (Path(room[2]['HOME']) / '.brnrd').exists() and not (room[0] / 'trace').exists()


def npm_launcher(room, ships_installer):
    """A global npm brnrd: the launcher that ships install.sh, or an older one that installs from PyPI."""
    tmp, binary, _ = room
    prefix = tmp / 'npm'
    package = prefix / 'lib/node_modules/brnrd'
    (package / 'bin').mkdir(parents=True)
    (package / 'package.json').write_text('{}')
    if ships_installer:
        (package / 'install.sh').write_text('')
    tool(package / 'bin', 'brnrd.js', 'exec "$HOME/.brnrd/bin/brnrd" "$@"\n')
    (prefix / 'bin').mkdir()
    (prefix / 'bin/brnrd').symlink_to(package / 'bin/brnrd.js')
    tool(binary, 'npm', f'case "$1" in root) echo "{package.parent}" ;; prefix) echo "{prefix}" ;; esac\n')
    return prefix / 'bin'


def test_the_npm_launcher_that_runs_this_installer_is_no_collision_nor_a_warning(room):
    tmp, binary, env = room
    fake_clone(binary)
    shell_profile(room, npm_launcher(room, ships_installer=True))  # npm's bin first on a login PATH
    result = run(room, dry=False)
    assert result.returncode == 0, result.stderr
    assert 'candidate -I -m brr.loom.runtime.install_checkout' in (tmp / 'trace').read_text()
    assert 'a new login shell runs' not in result.stdout


def test_an_older_npm_launcher_still_collides(room):
    npm_launcher(room, ships_installer=False)
    result = run(room, dry=False)
    assert result.returncode != 0 and 'npm uninstall -g brnrd' in result.stderr
    assert not (Path(room[2]['HOME']) / '.brnrd').exists()


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


def warnings(result):
    return [line for line in result.stderr.splitlines() if line.startswith('warning: ')]


def test_pythonpath_warns_and_does_not_stop_the_install(room):
    room[2]['PYTHONPATH'] = '/elsewhere/src'
    result = run(room)
    assert result.returncode == 0, result.stderr
    assert any('PYTHONPATH is set (/elsewhere/src)' in line for line in warnings(result))
    assert 'uv venv ' in result.stdout


@pytest.mark.parametrize('gh', ['missing', 'signed-out'])
def test_gh_missing_or_signed_out_warns_and_continues(room, gh):
    binary = room[1]
    (binary / 'gh').unlink()
    if gh == 'signed-out':
        tool(binary, 'gh', 'exit 1\n')
    result = run(room)
    assert result.returncode == 0, result.stderr
    assert [line for line in warnings(result) if 'gh' in line] == [
        'warning: gh is not installed, and following main reads CI through it; install the GitHub CLI and run gh auth login.'
        if gh == 'missing' else
        'warning: gh auth status fails, and following main reads CI through it; run gh auth login.']


def test_no_body_cli_on_path_warns(room):
    (room[1] / 'claude').unlink()
    result = run(room)
    assert result.returncode == 0, result.stderr
    assert any('neither claude nor codex is on PATH' in line for line in warnings(result))


@pytest.mark.parametrize('unit', ['Library/LaunchAgents/dev.brnrd.brr.plist', '.config/systemd/user/brr.service',
                                  'xdg/systemd/user/brr.service'])
def test_an_old_daemon_service_warns(room, unit):
    home = Path(room[2]['HOME'])
    if unit.startswith('xdg/'):
        room[2]['XDG_CONFIG_HOME'] = str(home / 'xdg')
    (home / unit).parent.mkdir(parents=True)
    (home / unit).write_text('old daemon')
    result = run(room)
    assert result.returncode == 0, result.stderr
    assert [line for line in warnings(result) if 'old brnrd daemon' in line] == [
        f'warning: an old brnrd daemon is installed ({home / unit}) and runs beside the loom; '
        'remove it with brnrd daemon uninstall.']


def test_warns_about_a_loom_running_from_another_install_only(room):
    home = Path(room[2]['HOME'])
    ours = home / '.brnrd/current/bin/python'
    room[2]['FAKE_PS'] = (f'  101 /opt/py/bin/python -m brr.loom.runtime loom --home /h\n'
                          f'  102 {ours} -m brr.loom.runtime loom --home /h\n'
                          '  103 /bin/zsh\n')
    result = run(room)
    assert result.returncode == 0, result.stderr
    assert [line for line in warnings(result) if 'loom from another install' in line] == [
        'warning: a loom from another install is running (pid 101: /opt/py/bin/python); the installer moves the '
        "loom service, but a loom started by hand keeps running beside it: stop it with that install's brnrd "
        'loom stop, or kill 101.']


def test_shellcheck_sh_when_installed():
    shellcheck = shutil.which('shellcheck')
    if shellcheck is None:
        pytest.skip('shellcheck is not installed')
    result = subprocess.run([shellcheck, '-s', 'sh', str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_readme_and_install_page_lead_with_the_curl_line():
    root = Path(__file__).parents[1]
    line = 'curl -fsSL https://brnrd.dev/install | sh'
    install = (root / 'docs/src/content/docs/getting-started/install.md').read_text()
    assert install.split('```bash\n', 1)[1].splitlines()[0] == line
    readme = (root / 'README.md').read_text()
    start = readme.index('## ✦ Install')
    window = readme[start:readme.index('\n## ', start + 1)]
    assert window.split('```bash\n', 1)[1].splitlines()[0] == line

"""Exercise the media guard against real git trees, including awkward filenames."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

spec = importlib.util.spec_from_file_location('repo_media', Path(__file__).parents[1] / 'scripts/check_repo_media.py')
media = importlib.util.module_from_spec(spec)
spec.loader.exec_module(media)


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, 'init', '-q', '-b', 'main')
    git(tmp_path, 'config', 'user.email', 'test@example.invalid')
    git(tmp_path, 'config', 'user.name', 'Test')
    (tmp_path / 'legacy.png').write_bytes(b'old')
    (tmp_path / 'legacy.bin').write_bytes(b'\0' * 20000)
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-qm', 'base')
    return tmp_path


def check(repo):
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'change', '--allow-empty')
    return media.violations(repo, 'HEAD^', 'HEAD')


@pytest.mark.parametrize('path,data,refused', [
    ('nested/image.PNG', b'x', True),
    ('movie.webm', b'x', True),
    ('font.woff2', b'x', True),
    ('vector.svg', b'<svg/>', False),
    ('large.svg', b'x' * 16385, True),
    ('limit.svg', b'x' * 16384, False),
    ('a space\nand newline.bin', b'\0' * 16385, True),
    ('unknown.dat', b'\xff' * 16385, True),
    ('small.bin', b'\0' * 16384, False),
    ('source.py', b'x' * 20000, False),
    ('unicode.json', ('é' * 20000).encode(), False),
    ('large.json', ('é' * (media.FILE_LIMIT // 2 + 1)).encode(), True),
    ('daemon.py', b'x' * 900000, False),
    ('limit.txt', b'x' * media.FILE_LIMIT, False),
    ('legacy.png', b'new', True),
    ('legacy.png', b'x', False),
    ('legacy.png', b'grow', True),
    ('legacy.bin', b'\0' * 20001, True),
    ('legacy.bin', b'\xff' * 20000, True),
], ids=lambda value: f'{len(value)}-bytes' if isinstance(value, bytes) else None)
def test_new_assets_and_legacy_growth(repo, path, data, refused):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    assert bool(check(repo)) == refused


def test_deletion_and_unchanged_legacy_are_allowed(repo):
    (repo / 'legacy.png').unlink()
    assert check(repo) == []


def test_rename_is_a_new_path(repo):
    (repo / 'legacy.png').rename(repo / 'renamed.png')
    assert 'renamed.png' in check(repo)[0]


@pytest.mark.parametrize('cap,expected', [(3, False), (2, True)])
def test_exact_path_exception_with_cap(repo, cap, expected):
    (repo / 'new.png').write_bytes(b'abc')
    (repo / '.github').mkdir()
    (repo / media.ALLOWLIST).write_text(json.dumps({'new.png': {'max_bytes': cap, 'reason': 'decoder fixture'}}))
    assert bool(check(repo)) == expected


def test_exception_is_not_a_glob(repo):
    (repo / 'new.png').write_bytes(b'x')
    (repo / '.github').mkdir()
    (repo / media.ALLOWLIST).write_text(json.dumps({'*.png': {'max_bytes': 100, 'reason': 'not a glob'}}))
    assert check(repo)


@pytest.mark.parametrize('rule', [{}, {'max_bytes': True, 'reason': 'x'}, {'max_bytes': 1, 'reason': ''}])
def test_malformed_exception_fails(repo, rule):
    (repo / '.github').mkdir()
    (repo / media.ALLOWLIST).write_text(json.dumps({'new.png': rule}))
    with pytest.raises(ValueError):
        check(repo)


def test_missing_base_fails(repo):
    with pytest.raises(subprocess.CalledProcessError):
        media.violations(repo, 'missing', 'HEAD')


def test_cli_compares_pr_head_from_environment_and_reports_route(repo):
    import os
    import sys

    script = repo / 'scripts' / 'check_repo_media.py'
    script.parent.mkdir()
    script.write_text((Path(__file__).parents[1] / 'scripts/check_repo_media.py').read_text())
    base = git(repo, 'rev-parse', 'HEAD')
    (repo / 'new.png').write_bytes(b'x')
    check(repo)
    env = {**os.environ, 'MEDIA_BASE': base, 'MEDIA_HEAD': 'HEAD'}
    result = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert 'hugimuni-labs/animations' in result.stderr
    assert media.ALLOWLIST in result.stderr
    assert 'new.png' in result.stderr
    result = subprocess.run([sys.executable, str(script), '--head', base],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0
    result = subprocess.run([sys.executable, str(script), '--base', 'missing'],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert 'could not compare' in result.stderr


@pytest.mark.parametrize('size,refused', [(media.FILE_LIMIT + 1, False), (media.FILE_LIMIT + 3, True)])
def test_oversized_legacy_text_may_shrink_but_not_grow(repo, size, refused):
    (repo / 'large.txt').write_bytes(b'x' * (media.FILE_LIMIT + 2))
    check(repo)
    (repo / 'large.txt').write_bytes(b'y' * size)
    assert bool(check(repo)) == refused


def test_large_text_exact_path_exception(repo):
    (repo / 'large.json').write_bytes(b'x' * (media.FILE_LIMIT + 1))
    (repo / '.github').mkdir()
    (repo / media.ALLOWLIST).write_text(json.dumps({
        'large.json': {'max_bytes': media.FILE_LIMIT + 1, 'reason': 'required fixture'}}))
    assert check(repo) == []

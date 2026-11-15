"""Exercise policy, the apply caller, and actual git deletion leases offline."""
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import subprocess
from urllib.parse import parse_qs, urlsplit

import pytest

spec = importlib.util.spec_from_file_location(
    'cleanup_closed_branches', Path(__file__).parents[1] / 'scripts/cleanup_closed_branches.py')
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)
NOW = datetime(2026, 11, 15, tzinfo=timezone.utc)
SHA = 'a' * 40
REPO = 'example/project'


def pr(number=1, **changes):
    result = dict(number=number, created_at=f'2026-09-{number:02d}T00:00:00Z',
                  state='closed', closed_at='2026-10-01T00:00:00Z', merged_at=None,
                  head=dict(ref='feature/topic', sha=SHA, repo={'full_name': REPO}))
    result.update(changes)
    return result


def eligible(prs, branch='feature/topic', default='main'):
    return cleanup.candidate(prs, REPO, branch, default, NOW)


@pytest.mark.parametrize('branch', ['main', 'shots', 'shots/nested', 'trunk'])
def test_protected(branch):
    p = pr()
    p['head']['ref'] = branch
    assert eligible([p], branch, 'trunk') is None


@pytest.mark.parametrize('changes', [
    {'state': 'open'}, {'merged_at': '2026-10-01T00:00:00Z'},
    {'closed_at': None}, {'closed_at': '2026-11-14T00:00:00Z'},
    {'closed_at': (NOW - timedelta(days=30)).isoformat()},
])
def test_ineligible(changes):
    assert eligible([pr(**changes)]) is None


def test_strict_age_and_empty_history():
    assert eligible([]) is None
    assert eligible([pr(closed_at=(NOW - timedelta(days=30, seconds=1)).isoformat())])


def test_newest_is_created_order_not_closed_or_input_order():
    old = pr(closed_at='2026-11-01T00:00:00Z')
    new = pr(2)
    assert eligible([new, old]) == new
    assert eligible([old, new]) == new
    assert eligible([old, pr(2, merged_at='2026-10-01T00:00:00Z')]) is None
    assert eligible([pr(state='open'), new]) is None


def test_fork_same_name_is_not_this_branch():
    fork = pr(2, state='open')
    fork['head']['repo']['full_name'] = 'fork/project'
    assert eligible([pr(), fork])['number'] == 1
    assert eligible([fork]) is None
    fork['head']['repo'] = None
    assert eligible([fork]) is None


class Fake(cleanup.GitHub):
    def __init__(self):
        super().__init__(REPO)
        self.histories = [[pr()], [pr()]]
        self.refs = [True, True]
        self.deleted = []

    def default_branch(self):
        return 'main'

    def branches(self):
        return ['feature/topic']

    def prs(self, branch):
        return copy.deepcopy(self.histories.pop(0))

    def retained(self, branch, p):
        return self.refs.pop(0)

    def delete(self, branch, sha):
        self.deleted.append((branch, sha))


def test_dry_run_default(capsys):
    github = Fake()
    cleanup.cleanup(github, clock=lambda: NOW)
    assert github.deleted == []
    assert 'DRY-RUN would delete' in capsys.readouterr().out
    assert len(github.histories) == 1


def test_apply_rechecks_and_logs(capsys):
    github = Fake()
    cleanup.cleanup(github, apply=True, clock=lambda: NOW)
    assert github.deleted == [('feature/topic', SHA)]
    assert not github.histories and not github.refs
    assert "DELETED branch='feature/topic' PR=#1" in capsys.readouterr().out


@pytest.mark.parametrize('fresh', [[], [pr(state='open')],
    [pr(), pr(2)], [pr(merged_at='2026-10-01T00:00:00Z')],
    [pr(closed_at='2026-11-14T00:00:00Z')]])
def test_changed_eligibility_blocks_deletion(fresh):
    github = Fake()
    github.histories[1] = fresh
    cleanup.cleanup(github, apply=True, clock=lambda: NOW)
    assert github.deleted == []


def test_changed_head_blocks_deletion():
    github = Fake()
    github.histories[1][0]['head']['sha'] = 'b' * 40
    cleanup.cleanup(github, apply=True, clock=lambda: NOW)
    assert github.deleted == []


@pytest.mark.parametrize('refs', [[False], [True, False]])
def test_missing_or_changed_refs_block_deletion(refs):
    github = Fake()
    github.refs = refs
    cleanup.cleanup(github, apply=True, clock=lambda: NOW)
    assert github.deleted == []


def test_new_default_branch_is_protected():
    github = Fake()
    defaults = iter(['main', 'feature/topic'])
    github.default_branch = lambda: next(defaults)
    cleanup.cleanup(github, apply=True, clock=lambda: NOW)
    assert github.deleted == []


def test_delete_failure_surfaces_without_retry():
    github = Fake()
    def fail(*args):
        raise subprocess.CalledProcessError(1, 'git push')
    github.delete = fail
    with pytest.raises(subprocess.CalledProcessError):
        cleanup.cleanup(github, apply=True, clock=lambda: NOW)


def test_paginated_api(monkeypatch):
    calls = []
    def run(*args):
        calls.append(args)
        return '[[{"number":1}],[{"number":2}]]'
    monkeypatch.setattr(cleanup, 'run', run)
    assert cleanup.api('repos/example/project/pulls', pages=True) == [{'number': 1}, {'number': 2}]
    assert calls == [('gh', 'api', 'repos/example/project/pulls', '--paginate', '--slurp')]


def test_branch_query_is_encoded_and_all_pages_requested(monkeypatch):
    calls = []
    def api(path, **kwargs):
        calls.append((path, kwargs))
        return []
    monkeypatch.setattr(cleanup, 'api', api)
    cleanup.GitHub(REPO).prs('feature/a+b&c')
    path, kwargs = calls[0]
    query = parse_qs(urlsplit(path).query)
    assert query['head'] == ['example:feature/a+b&c']
    assert query['state'] == ['all'] and kwargs == {'pages': True}


@pytest.mark.parametrize('branch_sha,pull_sha', [(SHA, SHA), ('b'*40, SHA), (SHA, 'b'*40), (SHA, None), (None, SHA)])
def test_retained_requires_both_exact_refs(monkeypatch, branch_sha, pull_sha):
    def run(*args):
        assert args[:3] == ('git', 'ls-remote', '--refs')
        return ''.join(f'{sha}\t{ref}\n' for sha, ref in [
            (branch_sha, 'refs/heads/feature/topic'), (pull_sha, 'refs/pull/1/head')] if sha)
    monkeypatch.setattr(cleanup, 'run', run)
    assert cleanup.GitHub(REPO).retained('feature/topic', pr()) == (branch_sha == pull_sha == SHA)


def test_cli_defaults_to_dry_run(monkeypatch):
    calls = []
    monkeypatch.setattr(cleanup, 'cleanup', lambda github, **kwargs: calls.append(kwargs))
    cleanup.main(['--repo', REPO])
    cleanup.main(['--repo', REPO, '--apply'])
    assert calls == [{'apply': False}, {'apply': True}]


def test_real_git_lease_rejects_concurrent_push_and_preserves_pull_ref(tmp_path):
    # Only local throwaway repositories: never a live GitHub deletion.
    remote = tmp_path / 'remote.git'
    work = tmp_path / 'work'
    def git(*args):
        return subprocess.run(['git', *map(str, args)], check=True, capture_output=True, text=True).stdout.strip()
    git('init', '--bare', remote)
    git('init', work)
    git('-C', work, 'config', 'user.name', 'Cleanup test')
    git('-C', work, 'config', 'user.email', 'cleanup@example.invalid')
    git('-C', work, 'commit', '--allow-empty', '-m', 'old')
    old = git('-C', work, 'rev-parse', 'HEAD')
    git('-C', work, 'push', remote, 'HEAD:refs/heads/feature/topic', 'HEAD:refs/pull/1/head')
    github = cleanup.GitHub(REPO)
    github.remote = str(remote)
    p = pr()
    p['head']['sha'] = old
    assert github.retained('feature/topic', p)
    git('-C', work, 'commit', '--allow-empty', '-m', 'concurrent push')
    new = git('-C', work, 'rev-parse', 'HEAD')
    git('-C', work, 'push', remote, 'HEAD:refs/heads/feature/topic')
    with pytest.raises(subprocess.CalledProcessError):
        github.delete('feature/topic', old)
    assert git('--git-dir', remote, 'rev-parse', 'refs/heads/feature/topic') == new
    # Matching explicit lease succeeds, and the pull head still retains old.
    github.delete('feature/topic', new)
    assert git('ls-remote', remote, 'refs/heads/feature/topic') == ''
    assert git('--git-dir', remote, 'rev-parse', 'refs/pull/1/head') == old


@pytest.mark.parametrize('apply', ['false', 'true'])
def test_workflow_shell_honors_apply(tmp_path, apply):
    import os
    import yaml
    workflow = yaml.safe_load((Path(__file__).parents[1] /
        '.github/workflows/cleanup-closed-branches.yml').read_text())
    step = workflow['jobs']['cleanup']['steps'][-1]
    # Execute the actual workflow shell with a harmless executable in place of Python.
    fake_python = tmp_path / 'python'
    fake_python.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    fake_python.chmod(0o755)
    env = dict(os.environ, PATH=f'{tmp_path}:{os.environ["PATH"]}',
               APPLY=apply, REPOSITORY=REPO)
    result = subprocess.run(['bash', '-e', '-c', step['run']], env=env,
                            check=True, capture_output=True, text=True)
    assert result.stdout.splitlines() == [
        'scripts/cleanup_closed_branches.py', '--repo', REPO,
        *(['--apply'] if apply == 'true' else [])]

"""Project kb mounts through the daemon, CLI and wake's real readers."""

import json
from dataclasses import replace


from brr import account, daemon
from _helpers import init_git_repo


def _setup(tmp_path):
    repo = tmp_path / 'repo'
    init_git_repo(repo)
    home = tmp_path / 'home'
    init_git_repo(home)
    tree = home / 'knowledge'
    init_git_repo(tree)
    cfg = {'repo.label': 'org/project', 'home.kind': 'account',
           'home.path': str(home), 'account.id': 'acct-1'}
    return repo, home, cfg


def _snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes()
            for p in root.rglob('*') if p.is_file()}


def test_daemon_mounts_registered_repos_only_after_consolidation(tmp_path, capsys):
    repo, home, cfg = _setup(tmp_path)
    second = tmp_path / 'second'
    init_git_repo(second)
    (second / 'kb').mkdir()
    (second / 'kb/index.md').write_text('committed knowledge')
    ctx = account.resolve_context(repo, cfg, create=False)
    registry = home / 'account/repos.json'
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(json.dumps({'repos': [
        {'kind': 'repo', 'label': 'org/project', 'path': str(repo)},
        {'kind': 'repo', 'label': 'org/second', 'path': str(second)},
        {'kind': 'home', 'label': 'home', 'path': str(home)},
    ]}))
    before = _snapshot(tmp_path)
    daemon._mount_home_knowledge(ctx)
    assert _snapshot(tmp_path) == before
    assert not (repo / 'kb').exists()
    (home / 'dominion').mkdir()
    before = _snapshot(tmp_path)
    daemon._mount_home_knowledge(replace(ctx, enabled=False))
    assert _snapshot(tmp_path) == before
    assert not (repo / 'kb').exists()
    for label in ('org__project', 'org__second'):
        (home / 'knowledge/repos' / label).mkdir(parents=True)
    daemon._mount_home_knowledge(ctx)
    output = capsys.readouterr().out
    assert '[brnrd] kb mount: mounted:' in output
    assert 'real kb/, left alone:' in output
    assert (repo / 'kb').is_symlink()
    assert (home / 'dominion/places/project/kb').resolve() == (repo / 'kb').resolve()
    assert (second / 'kb/index.md').read_text() == 'committed knowledge'
    assert not (home / 'kb').exists()
    assert not (home / 'knowledge/repos/org__second/index.md').exists()
    before = _snapshot(tmp_path)
    daemon._mount_home_knowledge(ctx)
    assert _snapshot(tmp_path) == before


def test_daemon_missing_knowledge_scope_is_reported_without_seeding(tmp_path, capsys):
    repo, home, cfg = _setup(tmp_path)
    ctx = account.resolve_context(repo, cfg, create=False)
    (home / 'dominion').mkdir()
    registry = home / 'account/repos.json'
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(json.dumps({'repos': [
        {'kind': 'repo', 'label': 'org/project', 'path': str(repo)},
    ]}))
    knowledge_before = _snapshot(home / 'knowledge')
    project_before = _snapshot(repo)

    daemon._mount_home_knowledge(ctx)

    assert ('skipped: no kb for org/project yet — brnrd kb mount --apply seeds it'
            in capsys.readouterr().out)
    assert not (repo / 'kb').is_symlink()
    assert not (home / 'knowledge/repos/org__project').exists()
    assert _snapshot(home / 'knowledge') == knowledge_before
    assert _snapshot(repo) == project_before

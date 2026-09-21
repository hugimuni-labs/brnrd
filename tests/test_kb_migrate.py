"""Migration receipts against real temporary Git repositories."""
from pathlib import Path
import subprocess

import pytest

from brr import cli, config, gitops, kb_migrate
from _helpers import commit_files, init_git_repo


def git(root, *args):
    return subprocess.run(['git', *args], cwd=root, env=gitops.explicit_repo_env(),
                          capture_output=True, check=True, text=True).stdout.strip()


@pytest.fixture
def migration(tmp_path):
    repo, home = tmp_path / 'project', tmp_path / 'home'
    init_git_repo(repo)
    init_git_repo(home)
    commit_files(home, {'.gitignore': '/knowledge/\n', 'README.md': 'home'})
    tree = home / 'knowledge'
    init_git_repo(tree)
    commit_files(tree, {'global/index.md': '# Global'})
    git(repo, 'remote', 'add', 'origin', 'git@github.com:org/project.git')
    commit_files(repo, {
        'kb/index.md': '[nested](notes/page.md)\n[code](../src/file.py#L2)\n',
        'kb/notes/page.md': '[home](../index.md)\n![image](../image.svg)\n',
        'kb/image.svg': '<svg/>', 'src/file.py': '# code\n',
    })
    return repo, home, tree


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*')
            if p.is_file()}


def test_plan_is_read_only_and_keeps_layout(migration):
    repo, home, tree = migration
    before = snapshot(repo), snapshot(home)
    plan = kb_migrate.migrate(repo, home, 'org/project')
    assert not plan.conflicts
    assert plan.destination == tree / 'repos/org__project'
    assert set(plan.files) == {'index.md', 'notes/page.md', 'image.svg'}
    assert len(plan.rewrites) == 1
    assert plan.files['notes/page.md'] == (repo / 'kb/notes/page.md').read_bytes()
    assert plan.rewrites[0][2] == f'https://github.com/org/project/blob/{git(repo, "rev-parse", "HEAD")}/src/file.py#L2'
    assert 'links rewritten: 1' in plan.lines()
    assert (snapshot(repo), snapshot(home)) == before


def test_link_rewriting_preserves_internal_links_and_code():
    text = '''[page](../index.md#part)
[code](../../src/f(x).py?raw=1#L2 "title")
![asset](<../../public/an image.svg>)
[reference]: ../../README.md#intro "title"
[web](https://example.org/a) [anchor](#local)
`[example](../../src/no.py)`
```md
[example](../../src/no.py)
```
'''
    actual, changes = kb_migrate._rewrite(text, 'notes/page.md', 'https://github.com/o/r.git', 'abc123')
    assert len(changes) == 3
    assert '[page](../index.md#part)' in actual
    assert '/blob/abc123/src/f%28x%29.py?raw=1#L2 "title")' in actual
    assert '<https://github.com/o/r/blob/abc123/public/an%20image.svg>' in actual
    assert '[reference]: https://github.com/o/r/blob/abc123/README.md#intro "title"' in actual
    assert actual.count('[example](../../src/no.py)') == 2
    assert '[web](https://example.org/a) [anchor](#local)' in actual


@pytest.mark.parametrize('apply', [False, True])
def test_existing_scope_refuses_whole_plan(migration, apply):
    repo, home, tree = migration
    destination = tree / 'repos/org__project'
    destination.mkdir(parents=True)
    before = snapshot(home)
    report = kb_migrate.migrate(repo, home, 'org/project', apply=apply)
    assert any('destination exists' in s for s in report.conflicts)
    assert not (destination / 'index.md').exists()
    assert snapshot(home) == before


@pytest.mark.parametrize('staged', [False, True])
def test_dirty_knowledge_refuses_without_copy(migration, staged):
    repo, home, tree = migration
    (tree / 'global/index.md').write_text('unfinished')
    if staged:
        git(tree, 'add', '.')
    before = snapshot(home)
    report = kb_migrate.migrate(repo, home, 'org/project', apply=True)
    assert any('uncommitted changes under knowledge/' in s for s in report.conflicts)
    assert snapshot(home) == before


def test_untracked_knowledge_refuses(migration):
    repo, home, tree = migration
    (tree / 'draft.md').write_text('unfinished')
    report = kb_migrate.migrate(repo, home, 'org/project', apply=True)
    assert report.conflicts and not (tree / 'repos').exists()


def test_apply_copies_commits_and_never_touches_source(migration, monkeypatch):
    repo, home, tree = migration
    source_before = snapshot(repo)
    tree_head = git(tree, 'rev-parse', 'HEAD')
    # Every git read and write must ignore a strand's inherited repository pin.
    monkeypatch.setenv('GIT_DIR', str(repo / '.git'))
    monkeypatch.setenv('GIT_WORK_TREE', str(repo))
    report = kb_migrate.migrate(repo, home, 'org/project', apply=True)
    assert not report.conflicts
    assert report.commit == git(tree, 'rev-parse', 'HEAD') != tree_head
    assert report.source_commit in git(tree, 'log', '-1', '--format=%B')
    assert git(tree, 'log', '-1', '--format=%an') == gitops.BOT_NAME
    assert git(tree, 'status', '--porcelain') == ''
    assert git(tree, 'diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD').splitlines() == [
        'repos/org__project/image.svg', 'repos/org__project/index.md', 'repos/org__project/notes/page.md']
    assert snapshot(repo) == source_before
    assert not (repo / 'kb').is_symlink()
    assert 'brnrd kb mount --apply' in '\n'.join(report.lines())
    for name, body in report.files.items():
        assert (report.destination / name).read_bytes() == body


def test_missing_tree_and_non_checkout_refuse(migration, tmp_path):
    repo, home, _ = migration
    missing = tmp_path / 'missing-home'
    report = kb_migrate.migrate(repo, missing, 'org/project', apply=True)
    assert 'home knowledge tree does not exist' in report.conflicts
    assert not missing.exists()
    other = tmp_path / 'not-git'
    other.mkdir()
    assert kb_migrate.migrate(other, home, 'org/project', apply=True).conflicts


def test_source_changes_and_symlinks_refuse(migration):
    repo, home, _ = migration
    (repo / 'kb/index.md').write_text('not committed')
    report = kb_migrate.migrate(repo, home, 'org/project', apply=True)
    assert any('source kb/ has uncommitted changes' in c for c in report.conflicts)
    git(repo, 'add', 'kb/index.md')
    git(repo, 'commit', '-m', 'change')
    (repo / 'kb/link.md').symlink_to('../src/file.py')
    git(repo, 'add', 'kb/link.md')
    git(repo, 'commit', '-m', 'symlink')
    report = kb_migrate.migrate(repo, home, 'org/project', apply=True)
    assert any('unsupported source entry' in c for c in report.conflicts)


def test_unresolvable_links_refuse(migration):
    repo, home, _ = migration
    commit_files(repo, {'kb/outside.md': '[other](../../elsewhere.md)'})
    report = kb_migrate.migrate(repo, home, 'org/project', apply=True)
    assert any('link leaves the project' in c for c in report.conflicts)


def test_cli_resolves_explicit_source_not_cwd(migration, monkeypatch, capsys):
    repo, home, tree = migration
    def load(root):
        assert root == repo
        return {'home.path': str(home), 'home.kind': 'account', 'account.id': 'test'}
    monkeypatch.setattr(config, 'load_config', load)
    monkeypatch.setattr(cli, '_repo_root', lambda: pytest.fail('must use explicit source'))
    args = cli.build_parser().parse_args(['kb', 'migrate', str(repo)])
    assert cli.cmd_kb(args) == 0
    assert 'dry-run: no files changed' in capsys.readouterr().out
    assert not (tree / 'repos').exists()
    args = cli.build_parser().parse_args(['kb', 'migrate', str(repo), '--apply'])
    assert cli.cmd_kb(args) == 0
    assert 'copied and committed:' in capsys.readouterr().out
    assert (tree / 'repos/org__project/index.md').exists()

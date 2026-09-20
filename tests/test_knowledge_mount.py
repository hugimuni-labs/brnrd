"""Project kb mounts through the daemon, CLI and wake's real readers."""

import json
import subprocess
from dataclasses import replace

import pytest

from brr import account, cli, config, daemon, kb_preflight, knowledge, prompts
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


def test_mount_creates_empty_scope_and_ignores_symlink_once(tmp_path):
    repo, home, _ = _setup(tmp_path)
    before_ignore = (repo / '.gitignore').read_bytes() if (repo / '.gitignore').exists() else None
    report = knowledge.ensure_mount(repo, home, 'org/project', apply=True)
    kb = repo / 'kb'
    assert kb.is_symlink()
    assert kb.resolve() == home / 'knowledge/repos/org__project'
    assert (kb / 'index.md').read_text() == '# Knowledge base\n\nNo pages yet.\n'
    assert report.seed and report.ignore
    assert 'empty kb' in '\n'.join(report.lines(applying=True))
    assert subprocess.run(['git', 'check-ignore', 'kb'], cwd=repo,
                          capture_output=True, text=True, check=True).stdout.strip() == 'kb'
    before = _snapshot(tmp_path)
    second = knowledge.ensure_mount(repo, home, 'org/project', apply=True)
    assert second.status == 'unchanged'
    assert not second.seed and not second.ignore
    assert _snapshot(tmp_path) == before
    assert (repo / '.git/info/exclude').read_text().splitlines().count('/kb') == 1
    assert ((repo / '.gitignore').read_bytes() if (repo / '.gitignore').exists() else None) == before_ignore


def test_dry_run_writes_nothing(tmp_path):
    repo, home, _ = _setup(tmp_path)
    before = _snapshot(tmp_path)
    report = knowledge.ensure_mount(repo, home, 'org/project', apply=False)
    assert report.status == 'created' and report.seed and report.ignore
    assert report.lines(applying=False)[0].startswith('would mount:')
    assert _snapshot(tmp_path) == before
    assert not (repo / 'kb').is_symlink()
    assert not (home / 'knowledge/repos').exists()


@pytest.mark.parametrize('directory', [True, False])
def test_real_kb_is_left_alone_without_seeding_or_ignoring(tmp_path, directory):
    repo, home, _ = _setup(tmp_path)
    kb = repo / 'kb'
    if directory:
        kb.mkdir()
        (kb / 'index.md').write_text('authored pages')
    else:
        kb.write_text('authored file')
    before = _snapshot(tmp_path)
    report = knowledge.ensure_mount(repo, home, 'org/project', apply=True)
    assert report.status == 'blocked'
    assert report.lines(applying=True) == [f'real kb/, left alone: {kb}']
    assert _snapshot(tmp_path) == before
    assert not (home / 'knowledge/repos').exists()


def test_mount_requires_existing_home_knowledge(tmp_path):
    repo = tmp_path / 'repo'
    init_git_repo(repo)
    home = tmp_path / 'missing-home'
    report = knowledge.ensure_mount(repo, home, 'org/project', apply=True)
    assert report.refusal
    assert not home.exists() and not (repo / 'kb').is_symlink()


def test_repairs_stale_symlink_without_touching_its_content(tmp_path):
    repo, home, _ = _setup(tmp_path)
    old = tmp_path / 'old-kb'
    old.mkdir()
    (old / 'index.md').write_text('keep me')
    (repo / 'kb').symlink_to(old)
    report = knowledge.ensure_mount(repo, home, 'org/project', apply=True)
    assert report.status == 'repaired'
    assert (old / 'index.md').read_text() == 'keep me'
    assert (repo / 'kb').resolve() == home / 'knowledge/repos/org__project'


def test_mount_and_exclude_address_linked_worktree_despite_inherited_pin(tmp_path, monkeypatch):
    repo, home, _ = _setup(tmp_path)
    worktree = tmp_path / 'worktree'
    subprocess.run(['git', 'worktree', 'add', '-b', 'mount-test', str(worktree)],
                   cwd=repo, check=True, capture_output=True)
    other = tmp_path / 'other'
    init_git_repo(other)
    other_before = _snapshot(other)
    monkeypatch.setenv('GIT_DIR', str(other / '.git'))
    monkeypatch.setenv('GIT_WORK_TREE', str(other))
    knowledge.ensure_mount(worktree, home, 'org/project', apply=True)
    assert (worktree / 'kb').is_symlink()
    assert '/kb' in (repo / '.git/info/exclude').read_text().splitlines()
    assert _snapshot(other) == other_before


def test_mount_readers_prefer_live_pages_and_legacy_fallback_survives(tmp_path, monkeypatch, capsys):
    repo, home, cfg = _setup(tmp_path)
    # Positive control: before the mount, search still sees the legacy clone.
    clone = repo / knowledge.CHECKOUT_DIRNAME
    init_git_repo(clone)
    subprocess.run(['git', 'remote', 'add', 'origin', str(tmp_path / 'wrong-home')],
                   cwd=clone, check=True)
    (clone / 'index.md').write_text('stale-only needle')
    assert knowledge.search(repo, 'stale-only', cfg)
    assert any(s.kind == 'checkout' for s in knowledge.sources(repo, cfg))
    assert knowledge.mirror_state(repo, cfg).status == knowledge.MIRROR_ELSEWHERE
    knowledge.ensure_mount(repo, home, 'org/project', apply=True)
    kb = repo / 'kb'
    (kb / 'index.md').write_text('# Live needle\n\n[missing](missing.md)\n')
    global_kb = home / 'knowledge/global'
    global_kb.mkdir()
    (global_kb / 'index.md').write_text('global needle')
    monkeypatch.setattr(config, 'load_config', lambda *_: cfg)
    monkeypatch.setattr(cli, '_repo_root', lambda: repo)
    assert knowledge.active_kb_dir(repo, cfg) == kb
    assert [h.source for h in knowledge.search(repo, 'needle', cfg)] == [
        'repo KB (mount)', 'home knowledge (account)']
    injection = prompts._build_knowledge_sources_block(repo)
    assert 'authored pages ⇒ `kb/`' in injection
    assert injection.count('Live needle') == 1
    assert 'stale-only' not in injection
    health = prompts._build_kb_health_block(repo)
    assert 'missing.md' in health  # real preflight, not a stubbed scan
    assert knowledge.mirror_state(repo, cfg).status == knowledge.MIRROR_ABSENT
    before = _snapshot(tmp_path)
    assert cli.main(['kb', 'needle']) == 0
    output = capsys.readouterr().out
    assert 'Live needle' in output and 'stale-only' not in output
    assert cli.main(['kb']) == 0
    assert f'graph for: {kb}' in capsys.readouterr().out
    assert _snapshot(tmp_path) == before  # reading must not refresh/migrate a clone
    kb.unlink()
    assert knowledge.active_kb_dir(repo, cfg) == home / 'knowledge/repos/org__project'
    assert knowledge.search(repo, 'stale-only', cfg)


def test_mount_forge_reader_uses_live_scope_despite_clone_and_git_pin(tmp_path, monkeypatch):
    repo, home, cfg = _setup(tmp_path)
    knowledge.ensure_mount(repo, home, 'org/project', apply=True)
    tree = home / 'knowledge'
    subprocess.run(['git', 'add', '.'], cwd=tree, check=True)
    subprocess.run(['git', 'commit', '-m', 'seed knowledge'], cwd=tree, check=True, capture_output=True)
    subprocess.run(['git', 'remote', 'add', 'origin', 'https://github.com/org/knowledge.git'], cwd=tree, check=True)
    subprocess.run(['git', 'update-ref', 'refs/remotes/origin/main', 'HEAD'], cwd=tree, check=True)
    (repo / knowledge.CHECKOUT_DIRNAME).mkdir()
    monkeypatch.setenv('GIT_DIR', str(repo / '.git'))
    monkeypatch.setenv('GIT_WORK_TREE', str(repo))
    assert knowledge.kb_page_url(repo, 'kb/index.md', cfg) == (
        'https://github.com/org/knowledge/blob/main/repos/org__project/index.md')


def test_mounted_health_retains_legacy_code_link_checks(tmp_path):
    repo, home, cfg = _setup(tmp_path)
    knowledge.ensure_mount(repo, home, 'org/project', apply=True)
    (repo / 'src').mkdir()
    (repo / 'src/present.py').write_text('# existing code\n')
    (repo / 'kb/index.md').write_text(
        '# Index\n\n[present](../src/present.py)\n[missing](../src/missing.py)\n')
    findings = kb_preflight.scan(repo, knowledge.active_kb_dir(repo, cfg))
    broken = [f.target for f in findings if f.type == 'broken-link']
    assert any('missing.py' in text for text in broken)
    assert not any('present.py' in text for text in broken)


def test_cli_mount_defaults_to_dry_run_and_reports_real_path(tmp_path, monkeypatch, capsys):
    repo, home, cfg = _setup(tmp_path)
    monkeypatch.setattr(config, 'load_config', lambda *_: cfg)
    monkeypatch.setattr(cli, '_repo_root', lambda: repo)
    before = _snapshot(tmp_path)
    assert cli.main(['kb', 'mount']) == 0
    assert 'would mount:' in capsys.readouterr().out
    assert _snapshot(tmp_path) == before
    assert cli.main(['kb', 'mount', '--apply']) == 0
    assert 'mounted:' in capsys.readouterr().out
    assert cli.main(['kb', 'mount']) == 0
    assert 'unchanged:' in capsys.readouterr().out
    (repo / 'kb').unlink()
    (repo / 'kb').mkdir()
    assert cli.main(['kb', 'mount', '--apply']) == 0
    assert 'real kb/, left alone:' in capsys.readouterr().out
    assert cli.main(['kb', 'needle', '--apply']) == 1


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

"""The operator's argv view, with real room clones and local project sources."""

import shutil
import subprocess
import sys

import pytest

from brr.daemon2.facts import Fact
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import append, read_facts
from brr.loom.runtime.loom import _prepare, tick_once
from brr.loom.runtime.project import sender_threads
from brr.loom.runtime.repos import prepare_repos
from brr.loom.runtime.selfrepo import _commit, git, init_self, room as clone_room

from _step import write_thread
from test_project_repos import _source


@pytest.fixture
def rooms(tmp_path):
    home = Home(tmp_path / 'home with spaces', install='0001')
    init_self(home.root, person='test')
    source, _ = _source(tmp_path)
    for thread in ('top', 'work', 'clean'):
        opened = 'opened-by: thread:top\n' if thread == 'work' else ''
        write_thread(home.root, thread,
                     f'---\nid: {thread}\nstatus: open\ntense: plan\n{opened}---\n# {thread}',
                     'quit-now', wait='0')
        (home.thread_dir(thread) / 'seat').write_text(f'repo: {source}\n')
    git(home.root / 'self', 'add', 'threads')
    _commit(home.root / 'self', 'Seats.\n\nLoom-Label: taint=0; audience=self\n')
    for thread in ('top', 'work', 'clean'):
        _lease(home, thread, f'0001-{thread}', 1)
        clone_room(home.root, f'0001-{thread}')
        prepare_repos(home, f'0001-{thread}', thread, [('widget', str(source))], None)
    return home, home.room('0001-work'), source


def _lease(home, thread, strand, gen, parent=None):
    data = dict(thread=thread, strand=strand, gen=gen)
    if parent:
        data['parent'] = parent
    append(home, Fact(kind='lease', by='router:0001', data=data, id=f'lease:{strand}'))


def _note(repo, name):
    (repo / name).write_text(name)
    git(repo, 'add', name)
    _commit(repo, name + '\n\nLoom-Label: taint=0; audience=self\n')
    return git(repo, 'rev-parse', 'HEAD').stdout.strip()


def _threads(home, *args):
    proc = subprocess.run([sys.executable, '-m', 'brr', 'threads', '--home', str(home.root), *args],
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _rows(text):
    lines = [line.split(' · ') for line in text.splitlines()]
    return {row[0]: dict(zip(lines[0], row)) for row in lines[1:]}


def test_threads_self_commit_and_current_main(rooms):
    home, room, _ = rooms
    sha = _note(room / 'self', 'self-work')
    assert _rows(_threads(home, '--left'))['work']['left'] == '1'
    detail = _threads(home, '--left', 'work')
    assert str(room / 'self') in detail and sha in detail and 'self-work' in detail
    assert _rows(_threads(home, '--left'))['clean']['left'] == '—'
    # Main moves after the clone's birth. No cached origin ref can prove this.
    git(home.root / 'self', 'fetch', str(room / 'self'), 'HEAD')
    git(home.root / 'self', 'merge', '--ff-only', 'FETCH_HEAD')
    assert _threads(home, '--left', 'work') == ''


def test_threads_project_commit_and_current_source(rooms):
    home, room, source = rooms
    sha = _note(room / 'widget', 'project-work')
    assert _rows(_threads(home, '--left'))['work']['left'] == '1'
    detail = _threads(home, '--left', 'work')
    assert str(room / 'widget') in detail and sha in detail and 'project-work' in detail
    git(source, 'fetch', str(room / 'widget'), 'HEAD:refs/heads/landed')
    assert _threads(home, '--left', 'work') == ''


@pytest.mark.parametrize('repo', ['self', 'widget'])
def test_threads_uncommitted_edits(rooms, repo):
    home, room, _ = rooms
    checkout = room / repo
    (checkout / 'README.md').write_text('dirty tracked file')
    (checkout / 'untracked file').write_text('new')
    assert _rows(_threads(home, '--left'))['work']['left'] == '2'
    detail = _threads(home, '--left', 'work')
    assert str(checkout) in detail and "edit 'README.md'" in detail and "edit 'untracked file'" in detail


def test_threads_publish_from_moves_the_commit_to_the_opener(rooms):
    home, room, _ = rooms
    sha = _note(room / 'widget', 'copied-work')
    # Positive control before the real copy verb; no branch.published fact.
    assert sha in _threads(home, '--left', 'work')
    top = home.room('0001-top')
    proc = subprocess.run([sys.executable, '-m', 'brr', 'publish', 'widget', '--from', 'work',
                           '--room', str(top)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert _threads(home, '--left', 'work') == ''
    assert sha in _threads(home, '--left', 'top')
    assert _rows(_threads(home, '--left'))['top']['left'] == '1'
    # A later worker commit is still only in the worker.
    late = _note(room / 'widget', 'late-work')
    assert late in _threads(home, '--left', 'work')
    assert sha not in _threads(home, '--left', 'work')


def test_threads_molt_successor_inherits_both_repos_but_not_edits(rooms):
    home, room, source = rooms
    own = _note(room / 'self', 'self-inherited')
    project = _note(room / 'widget', 'project-inherited')
    (room / 'self' / 'not-inherited').write_text('dirty')
    assert _rows(_threads(home, '--left'))['work']['left'] == '3'
    successor = '0001-successor'
    _lease(home, 'work', successor, 2, parent=room.name)
    facts = read_facts(home)
    _prepare(home, successor, 'work', 2, [], sender_threads(facts), facts, 'hand',
             repos=[('widget', str(source))])
    detail = _threads(home, '--left', 'work')
    assert detail.count(own) == detail.count(project) == 1
    assert str(home.room(successor) / 'self') in detail
    predecessor = detail.split(str(room / 'self'), 1)[1].split(str(home.room(successor)), 1)[0]
    assert own not in predecessor and project not in predecessor
    assert 'not-inherited' in predecessor
    assert _rows(_threads(home, '--left'))['work']['left'] == '3'


def test_threads_clean_and_gone_rooms_have_no_count(rooms):
    home, room, _ = rooms
    assert 'left' not in _rows(_threads(home, '--left'))['work']
    assert _threads(home, '--left', 'work') == ''
    shutil.rmtree(room)
    assert 'left' not in _rows(_threads(home, '--left'))['work']
    assert _threads(home, '--left', 'work') == ''


def test_threads_failed_inspection_is_visible_and_nonempty(rooms):
    home, room, _ = rooms
    git(room / 'widget', 'config', 'remote.origin.url', '/does/not/exist')
    # The mutable origin is irrelevant: the declared source still reads clean.
    assert _threads(home, '--left', 'work') == ''
    shutil.rmtree(home.root.parent / 'sources')
    assert _rows(_threads(home, '--left'))['work']['left'] == '?'
    assert 'inspection failed:' in _threads(home, '--left', 'work')


def test_neither_tick_nor_plain_table_inspects_rooms_but_left_does(rooms, monkeypatch):
    import brr.loom.runtime.leftovers as inventory
    from brr.cli import main
    home, _, _ = rooms

    def forbidden(*args):
        raise AssertionError('leftovers called')

    monkeypatch.setattr(inventory, 'leftovers', forbidden)
    tick_once(home, {}, 'fake', 'haiku')
    main(['threads', '--home', str(home.root)])
    # Proves the patched function is the one used by real CLI argv.
    with pytest.raises(AssertionError, match='leftovers called'):
        main(['threads', '--home', str(home.root), '--left'])


def test_thread_opened_between_reads_of_main_waits_a_tick(tmp_path, monkeypatch):
    from brr.loom.runtime import threads as threads_mod
    home = Home(tmp_path / 'home', install='0001')
    init_self(home.root, person='test')

    def open_thread(thread):
        home.thread_dir(thread).mkdir(parents=True)
        (home.thread_dir(thread) / 'README.md').write_text(
            f'---\nid: {thread}\nstatus: open\ntense: plan\n---\n# {thread}\n')
        git(home.root / 'self', 'add', 'threads')
        _commit(home.root / 'self', f'{thread}\n\nLoom-Label: taint=0; audience=self\n')

    open_thread('old')
    snapshot = threads_mod.main_snapshot

    def landing(*args):
        main = snapshot(*args)
        if 'new' not in main:
            open_thread('new')
        return main

    # A commit lands right after the axes read main: this tick shows the
    # old tip whole, the next one shows the new thread.
    monkeypatch.setattr(threads_mod, 'main_snapshot', landing)
    before = {row['thread'] for row in threads_mod.thread_table(home, read_facts(home))}
    assert 'old' in before and 'new' not in before
    after = {row['thread'] for row in threads_mod.thread_table(home, read_facts(home))}
    assert after == before | {'new'}


def test_a_linked_worktree_or_a_link_is_never_counted_or_fetched_into(rooms, tmp_path):
    from brr.loom.runtime.leftovers import leftovers
    home, room, _ = rooms
    # A person's repo with its own branches; the room holds a worktree of it and a link to it.
    _, person = _source(tmp_path / 'person')
    for name in ('one', 'two'):
        git(person, 'checkout', '-q', '-b', name)
        _note(person, name)
    git(person, 'checkout', '-q', 'main')
    shutil.rmtree(room / 'widget')
    git(person, 'worktree', 'add', '-q', '-b', 'room-work', str(room / 'widget'))
    (room / 'kb').symlink_to(person)
    left = leftovers(home, '0001-work')
    assert [(item.path.name, item.commits, item.error) for item in left] == [
        ('widget', (), f'a linked worktree of {person.resolve() / ".git"}')]
    assert git(person, 'for-each-ref', 'refs/leftovers').stdout == ''


def test_a_loom_read_of_an_https_source_carries_the_homes_token_to_that_host_only(tmp_path):
    from brr.loom.runtime.publish import read_command
    root = tmp_path / 'home'
    (root / 'loom').mkdir(parents=True)
    source = 'https://forge.example/o/private.git'
    assert read_command(root, source, ['fetch']) == (['fetch'], None)
    (root / 'loom' / 'config.toml').write_text('forge_token = "env: TEST_FORGE_TOKEN"\n')
    assert read_command(root, '/a/local/source', ['fetch']) == (['fetch'], None)

    def fill(host, monkeypatch=pytest.MonkeyPatch()):
        monkeypatch.setenv('TEST_FORGE_TOKEN', 'the-token')
        args, env = read_command(root, source, ['credential', 'fill'])
        monkeypatch.undo()
        env['GIT_TERMINAL_PROMPT'] = '0'
        return subprocess.run(['git', *args], input=f'protocol=https\nhost={host}\n\n', env=env,
                              capture_output=True, text=True, cwd=tmp_path).stdout

    assert 'password=the-token' in fill('forge.example')
    assert 'the-token' not in fill('elsewhere.example')

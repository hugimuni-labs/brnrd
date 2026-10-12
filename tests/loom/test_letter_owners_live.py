"""Ownership regressions through ports, process exits and the operator CLI."""
import threading
import pytest

from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import inject_letter
from brr.loom.runtime.lifecycle import main_snapshot
from brr.loom.runtime.owners import letter_owners
from brr.loom.runtime.verbs_loom import _attention
from _step import Loom, wait_until, write_thread


def test_two_failed_starts_are_visible_to_operator_and_a_note_retires_them(tmp_path, monkeypatch, capsys):
    root = tmp_path / 'home'
    write_thread(root, 'work', 'Over-budget wake', 'answer-fast', wait='0')
    monkeypatch.setenv('LOOM_BUDGET_BYTES', '1')
    ident = inject_letter(root, to='thread:work', body='Please answer')
    loom = Loom(root)
    loom.start()
    try:
        def table():
            return letter_owners(loom.facts(), main_snapshot(Home(root)))
        wait_until(lambda: table()[ident].owner == 'operator', 10, loom.dump)
        ends = [f for f in loom.facts() if f.kind == 'attempt.ended']
        assert len(ends) == 2
        assert _attention(['--home', str(root)]) == 0
        assert ident in capsys.readouterr().out
        assert _attention(['--home', str(root), '--note', ident, '--why', 'repaired by hand']) == 0
        assert table()[ident].done
    finally:
        loom.halt()


def test_unhandled_bounces_walk_to_top_then_operator(tmp_path, capsys):
    from test_lifecycle import bodies
    root = tmp_path / 'home'
    write_thread(root, 'top', 'Top', 'custom', wait='0')
    for name, opener in [('middle', 'top'), ('worker', 'middle')]:
        write_thread(root, name, f'---\nid: {name}\nstatus: open\ntense: plan\n'
                     f'opened-by: thread:{opener}\n---\n# {name}\n', 'custom', wait='0')
    bodies(root, r'''import sys, time
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx, _own_thread
ctx = Ctx(Path(sys.argv[1]))
own = _own_thread(ctx.wake())
letters = ctx.owed()
if any('begin' == l.body for l in letters):
    for l in letters:
        ctx.send('thread:top', note='dispatched', re=l.id)
    ctx.send('thread:middle', 'Do the work')
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if any('body exited' in l.body for l in ctx.jack('post').letters):
            sys.exit(7)
        time.sleep(.03)
    sys.exit(8)
if own == 'middle':
    for l in letters:
        ctx.send('thread:middle', note='delegated', re=l.id)
    ctx.send('thread:worker', 'Do the work')
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if any('body exited' in l.body for l in ctx.jack('post').letters):
            sys.exit(7)
        time.sleep(.03)
    sys.exit(8)
sys.exit(7)
''')
    inject_letter(root, to='thread:top', body='begin')
    loom = Loom(root)
    loom.start()
    try:
        def table():
            return letter_owners(loom.facts(), main_snapshot(loom.home))
        wait_until(lambda: any(f.owner == 'operator' and 'body exited' in f.letter.data['body']
                              for f in table().values()), 20, loom.dump)
        notices = [f.letter for f in table().values() if f.letter.data.get('from') == 'loom']
        assert [l.data['to'] for l in notices] == ['thread:middle', 'thread:top']
        debt = next(f for f in table().values() if f.owner == 'operator')
        _attention(['--home', str(root)])
        assert debt.letter.id in capsys.readouterr().out
        _attention(['--home', str(root), '--note', debt.letter.id, '--why', 'taken over'])
        assert table()[debt.letter.id].done
    finally:
        loom.halt()
    assert not loom.errors


@pytest.mark.parametrize('stale', [False, True], ids=['refused', 'stale'])
def test_unsent_reply_returns_to_its_body_with_visible_refusal(tmp_path, monkeypatch, stale):
    from test_lifecycle import bodies
    from brr.loom.runtime import router as routing
    root = tmp_path / 'home'
    write_thread(root, 'work', 'Reply', 'custom', wait='0')
    ready, resume = root / 'refusal-read', root / 'resume'
    if stale:
        # Simulate the real takeover seam: the draft carries an old router grant.
        original = routing.Router.speak_out
        def takeover(self, facts):
            from dataclasses import replace
            changed = [replace(f, data=dict(f.data, router_gen=0))
                       if f.kind == 'letter' and f.data.get('to') == 'channel:telegram/42'
                       else f for f in facts]
            return original(self, changed)
        monkeypatch.setattr(routing.Router, 'speak_out', takeover)
    bodies(root, f'''import sys, time
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx
ctx = Ctx(Path(sys.argv[1]))
letters = ctx.owed()
assert len(letters) == 1
ask = letters[0]
ctx.send('channel:telegram/42', 'answer', re=ask.id)
deadline = time.monotonic() + 15
while time.monotonic() < deadline:
    seen = ctx.jack('post').letters
    notices = [l for l in seen if 'was refused' in l.body or 'was not sent' in l.body]
    if notices:
        Path({str(ready)!r}).write_text(notices[0].body)
        while not Path({str(resume)!r}).exists():
            time.sleep(.02)
        ctx.send('thread:work', note='handled refusal', re=ask.id)
        for l in notices:
            ctx.send('thread:work', note='read refusal', re=l.id)
        sys.exit(0)
    time.sleep(.03)
raise SystemExit('refusal not delivered')
''')
    ident = inject_letter(root, to='thread:work', body='Please answer')
    loom = Loom(root)
    loom.start()
    try:
        wait_until(ready.exists, 12, loom.dump)
        table = letter_owners(loom.facts(), main_snapshot(loom.home))
        assert table[ident].owner == 'body'
        assert 'speech' in table[ident].reason
        assert any(not f.done and 'was ' in f.letter.data['body'] for f in table.values())
        assert not (root / 'loom/channel-fake.jsonl').exists()
        resume.touch()
        wait_until(lambda: letter_owners(loom.facts(), main_snapshot(loom.home))[ident].done,
                   5, loom.dump)
    finally:
        resume.touch()
        loom.halt()
    assert not loom.errors


def test_climb_waits_for_ended_and_drained_then_opener_reads_it(tmp_path, monkeypatch):
    from test_lifecycle import bodies
    from brr.loom.runtime import loom as runtime
    root = tmp_path / 'home'
    from brr.loom.runtime.selfrepo import init_self
    init_self(root, person='test')
    write_thread(root, 'top', 'Top', 'custom', wait='0')
    write_thread(root, 'work', '---\nid: work\nstatus: open\ntense: plan\n'
                 'opened-by: thread:top\n---\n# Work\n', 'custom', wait='0')
    ready = root / 'worker-ready'
    bodies(root, f'''import sys, subprocess, time
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx, _own_thread
ctx = Ctx(Path(sys.argv[1]))
if _own_thread(ctx.wake()) == 'top':
    for l in ctx.owed():
        if 'climbed here' in l.body:
            ctx.send('thread:top', note='read climbed ask', re=l.id)
            sys.exit(0)
        ctx.send('thread:top', note='dispatch', re=l.id)
    ctx.send('thread:work', 'Read after closure')
    deadline = time.monotonic() + 12
    while not Path({str(ready)!r}).exists():
        assert time.monotonic() < deadline
        time.sleep(.03)
    p = subprocess.run([sys.executable, '-m', 'brr.loom.runtime', 'settle', 'work',
                        '--why', 'closed by opener', '--room', str(ctx.room)])
    assert p.returncode == 0
else:
    ctx.owed()
    Path({str(ready)!r}).touch()
    deadline = time.monotonic() + 12
    while 'settled:' not in ctx.jack('post').raw:
        assert time.monotonic() < deadline
        time.sleep(.03)
''')
    ended, release = threading.Event(), threading.Event()
    drain = runtime._drained
    def paused_drain(home, facts, strand, thread, gen):
        if thread == 'work':
            ended.set()
            assert release.wait(10)
        return drain(home, facts, strand, thread, gen)
    monkeypatch.setattr(runtime, '_drained', paused_drain)
    inject_letter(root, to='thread:top', body='Begin')
    loom = Loom(root)
    loom.start()
    try:
        wait_until(ended.is_set, 12, loom.dump)
        table = letter_owners(loom.facts(), main_snapshot(loom.home))
        ident = next(k for k, f in table.items() if f.letter.data['body'] == 'Read after closure')
        assert table[ident].owner == 'loom'
        assert table[ident].reason == 'awaiting port drain'
        assert not any(f.data.get('about') == ident for f in loom.facts())
        release.set()
        wait_until(lambda: any(f.kind == 'note' and f.data.get('why') == 'read climbed ask'
                              for f in loom.facts()), 8, loom.dump)
        assert letter_owners(loom.facts(), main_snapshot(loom.home))[ident].done
    finally:
        release.set()
        loom.halt()
    assert not loom.errors


def test_routing_ignores_working_checkout_status_for_and_inventory(tmp_path):
    root = tmp_path / 'home'
    write_thread(root, 'work', 'Work on main', 'answer-fast', wait='0')
    readme = Home(root).thread_dir('work') / 'README.md'
    readme.write_text(readme.read_text().replace('status: open', 'status: settled\nfor: someone-else'))
    ghost = Home(root).thread_dir('ghost')
    ghost.mkdir()
    (ghost / 'README.md').write_text('---\nid: ghost\nstatus: open\ntense: plan\n---\n# Unlanded\n')
    ident = inject_letter(root, to='thread:work', body='Answer using main')
    loom = Loom(root)
    loom.start()
    try:
        wait_until(lambda: letter_owners(loom.facts(), main_snapshot(loom.home))[ident].done,
                   10, loom.dump)
        assert 'ghost' not in main_snapshot(loom.home)
        assert any(f.kind == 'body.started' for f in loom.facts())
        assert not any(f.data.get('thread') == 'ghost' for f in loom.facts())
    finally:
        loom.halt()
    assert not loom.errors


def test_settling_worker_disposes_its_reply_without_an_extra_note(tmp_path):
    from test_lifecycle import bodies
    from brr.loom.runtime.selfrepo import init_self
    root = tmp_path / 'home'
    init_self(root, person='test')
    write_thread(root, 'top', 'Top', 'custom', wait='0')
    write_thread(root, 'worker', '---\nid: worker\nstatus: open\ntense: plan\n'
                 'opened-by: thread:top\n---\n# Worker\n', 'custom', wait='0')
    accepted = root / 'accepted'
    bodies(root, f'''import sys, subprocess, time
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx, _own_thread
ctx = Ctx(Path(sys.argv[1]))
if _own_thread(ctx.wake()) == 'worker':
    for l in ctx.owed():
        ctx.send(l.reply, 'WORKED', re=l.id)
        ctx.send(l.reply, 'New proposal')
else:
    for l in ctx.owed():
        ctx.send('thread:top', note='dispatched', re=l.id)
    ctx.send('thread:worker', 'Work')
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        seen = ctx.jack('post').letters
        answers = [l for l in seen if l.body == 'WORKED']
        proposals = [l for l in seen if l.body == 'New proposal']
        if answers and proposals:
            answer = answers[0]
            p = subprocess.run([sys.executable, '-m', 'brr.loom.runtime', 'settle', 'worker',
                                '--why', 'accepted', '--room', str(ctx.room)])
            assert p.returncode == 0
            while any(l.id == answer.id for l in ctx.jack('post').letters):
                assert time.monotonic() < deadline
                time.sleep(.03)
            assert any(l.id == proposals[0].id for l in ctx.jack('post').letters)
            ctx.send('thread:top', note='considered new proposal', re=proposals[0].id)
            Path({str(accepted)!r}).write_text(answer.id)
            sys.exit(0)
        time.sleep(.03)
    raise SystemExit('no worker reply')
''')
    inject_letter(root, to='thread:top', body='Begin')
    loom = Loom(root)
    loom.start()
    try:
        wait_until(lambda: accepted.is_file() and accepted.read_text().strip(), 15, loom.dump)
        ident = accepted.read_text().strip()
        assert letter_owners(loom.facts(), main_snapshot(loom.home))[ident].done
        assert not any(f.kind == 'note' and f.data.get('re') == ident for f in loom.facts())
    finally:
        loom.halt()
    assert not loom.errors

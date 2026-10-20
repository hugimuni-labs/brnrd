from _step import mail
"""A room opens work by sending; the same table tells its opener what is live."""

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from brr.loom.runtime.ledger import inject_letter
from brr.loom.runtime.selfrepo import git, init_self, _env_without_pin
from brr.loom.runtime.project import holder
from brr.loom.runtime.wishes import wishes

from _step import Loom, wait_until, write_thread, kill_bodies
from test_lifecycle import bodies


def command(*args):
    env = os.environ.copy()
    return subprocess.run([sys.executable, '-m', 'brr.loom.runtime', *args],
                          text=True, capture_output=True, env=env, timeout=15)


def table(root):
    proc = subprocess.run([sys.executable, '-m', 'brr', 'threads', '--home', str(root)],
                          text=True, capture_output=True, timeout=15)
    assert proc.returncode == 0, proc.stderr
    lines = [line.split(' · ') for line in proc.stdout.splitlines()]
    assert lines[0] == ['thread', 'seat', 'now', 'owed', 'unanswered', 'opened-by', 'opened-for', 'plan', 'owed-by', 'restart', 'control']
    return {row[0]: dict(zip(lines[0], row)) for row in lines[1:]}


def test_send_opens_lands_routes_and_shows_working_idle_settled(tmp_path):
    root = tmp_path / 'home'
    init_self(root, person='test')
    write_thread(root, 'opener', 'Opener', 'opener', wait='0')
    write_thread(root, 'idle', 'Idle\n\n## Plan\n- [x] First\n- [ ] Second', 'idle', wait='0')
    old = (datetime.now(timezone.utc) - timedelta(hours=3, minutes=10)).isoformat()
    env = _env_without_pin()
    env['GIT_COMMITTER_DATE'] = old
    git(root / 'self', 'commit', '--amend', '--no-edit', '--date', old, env=env)
    write_thread(root, 'settled', '---\nid: settled\nstatus: settled\ntense: plan\n'
                 'opened-by: thread:opener\n---\n# Accepted\n\n## Plan\n- [x] Done', 'settled')
    ready, release, reply, opened, finish = [root / name for name in
                                            ('ready', 'release', 'reply', 'opened', 'finish')]
    bodies(root, f'''import json, subprocess, sys, time
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx, _own_thread
ctx = Ctx(Path(sys.argv[1]))
own = _own_thread(ctx.wake())
if own == "opener":
    for letter in ctx.owed():
        ctx.send("thread:opener", note="begin", re=letter.id)
    proc = subprocess.run([sys.executable, "-m", "brr.loom.runtime", "send", "--room", str(ctx.room),
                           "--to", "thread:new-work", "--wish", "new:New work", "--body", "worker/test-core", "--repo", "https://example/a.git", "New work\\nDetails"],
                          text=True, capture_output=True)
    assert proc.returncode == 0, proc.stderr
    while not Path({str(root / "stop-opener")!r}).exists():
        view = ctx.jack("post")
        context = json.loads(view.raw)["hookSpecificOutput"]["additionalContext"] if view.raw else ""
        if "opened: new-work working · owed 1" in context:
            if not Path({str(opened)!r}).exists():
                again = ctx.jack("post")
                assert again.raw == "", again.raw
                Path({str(opened)!r}).write_text(view.raw)
        # No mail is expected only before the test releases the reply.
        # A holding worker can still send a letter to its opener.
        if "opened: new-work holding · owed 0" in context and not Path({str(root / "changed")!r}).exists():
            assert not view.letters
            Path({str(root / "changed")!r}).write_text(view.raw)
        for letter in view.letters:
            if letter.body == "work answered":
                assert "opened: new-work holding · owed 0" in context, context
                ctx.send("thread:opener", note="read answer", re=letter.id)
                Path({str(reply)!r}).write_text(letter.body)
        time.sleep(0.03)
else:
    letters = ctx.owed()
    assert own == "new-work" and [l.body.split("\\n", 1)[0] for l in letters] == ["New work"]
    assert "New work\\nDetails" in ctx.wake()
    Path({str(ready)!r}).write_text("ready")
    while not Path({str(root / "clear-owed")!r}).exists():
        ctx.jack("post")
        time.sleep(0.03)
    for letter in letters:
        ctx.send("thread:new-work", note="work complete", re=letter.id)
    while not Path({str(release)!r}).exists():
        ctx.jack("post")
        time.sleep(0.03)
    for letter in letters:
        ctx.send(letter.reply, "work answered", re=letter.id)
    # Make reply delivery precede worker exit on every machine: holding
    # describes the worker's debt, not the opener's incoming mail.
    while not Path({str(finish)!r}).exists():
        ctx.jack("post")
        time.sleep(0.03)
''')
    shutil.copytree(root / 'loom/bodies/fake', root / 'loom/bodies/worker')
    inject_letter(root, to='thread:opener', body='begin')
    loom = Loom(root)
    loom.start()
    try:
        wait_until(ready.exists, 15, loom.dump)
        wait_until(opened.exists, 30, loom.dump)
        readme = git(root / 'self', 'show', 'main:threads/new-work/README.md').stdout
        behind = next(w for w in wishes(loom.facts()).values() if w.line == 'New work')
        assert (behind.thread, behind.origin) == ('new-work', 'derived')
        assert readme == ('---\nid: new-work\nstatus: open\ntense: plan\n'
                          f'opened-by: thread:opener\nopened-for: [{behind.id}]\n---\n# New work\n')
        assert git(root / 'self', 'show', 'main:threads/new-work/seat').stdout == 'body: worker/test-core\nrepo: https://example/a.git\n'
        assert git(root / 'self', 'log', '-1', '--format=%H', '--', 'threads/new-work/README.md').stdout == git(
            root / 'self', 'log', '-1', '--format=%H', '--', 'threads/new-work/seat').stdout
        assert next(f for f in loom.facts() if f.kind == 'lease' and f.data['thread'] == 'new-work').data['repos'] == [['a', 'https://example/a.git']]
        assert [f.data['body'] for f in mail(root, loom.facts(), 'new-work')] == ['New work\nDetails']
        rows = table(root)
        assert rows['new-work'] == {'thread': 'new-work', 'seat': 'worker/test-core', 'now': 'working',
                                    'owed': '1', 'unanswered': '0', 'opened-by': 'thread:opener',
                                    'opened-for': f'{behind.id} heard: New work', 'plan': '—',
                                    'owed-by': 'body:new-work', 'restart': 'current body', 'control': '—'}
        assert rows['idle'] == {'thread': 'idle', 'seat': '—', 'now': 'idle 3h', 'owed': '0', 'unanswered': '0',
                                'opened-by': '—', 'opened-for': '—', 'plan': '1/2', 'owed-by': 'nobody',
                                'restart': 'next letter', 'control': '—'}
        date = git(root / 'self', 'log', '-1', '-G', '^status:', '--format=%cs',
                   'main', '--', 'threads/settled/README.md').stdout.strip()
        assert rows['settled'] == {'thread': 'settled', 'seat': '—', 'now': f'settled {date}',
                                   'owed': '0', 'unanswered': '0', 'opened-by': 'thread:opener', 'opened-for': '—', 'plan': '1/1',
                                   'owed-by': 'nobody',
                                   'restart': 'new letter or opener answers', 'control': 'thread:opener'}
        (root / 'clear-owed').write_text('handle without replying yet')
        wait_until((root / 'changed').exists, 30, loom.dump)
        changed = json.loads((root / 'changed').read_text())['hookSpecificOutput']['additionalContext']
        assert 'opened: new-work holding · owed 0' in changed
        release.write_text('answer now')
        wait_until(reply.exists, 30, loom.dump)
        finish.write_text('reply read; worker may exit')
        wait_until(lambda: holder(loom.facts(), 'new-work') is None, 30, loom.dump)
        rows = table(root)
        assert rows['new-work']['now'].startswith('idle ')
        assert rows['new-work']['owed'] == '0'
        opener = holder(loom.facts(), 'opener')[0]
        proc = command('settle', 'new-work', '--room', str(loom.home.room(opener)), '--why', 'accepted')
        assert proc.returncode == 0, proc.stderr
        closed_date = git(root / 'self', 'log', '-1', '-G', '^status:', '--format=%cs',
                          'main', '--', 'threads/new-work/README.md').stdout.strip()
        assert table(root)['new-work']['now'] == f'settled {closed_date}'
        boundary = loom.home.room(opener) / 'port/in/boundary.md'
        wait_until(lambda: 'opened: new-work' not in boundary.read_text(), 30, loom.dump)
        (root / 'stop-opener').write_text('stop')
        wait_until(lambda: holder(loom.facts(), 'opener') is None, 30, loom.dump)
    finally:
        finish.touch()
        loom.halt()
        kill_bodies(loom)
    # Unknown reply ids are legal. A bare --re still has to read the boundary;
    # an existing read failure must refuse before opening anything on main.
    room = loom.home.room(opener)
    boundary = room / 'port/in/boundary.md'
    boundary.write_bytes(b'\xff')
    bad_re = command('send', '--room', str(room), '--to', 'thread:bad-re-open', '--re', 'bad', 'Work')
    assert bad_re.returncode == 1 and 'invalid start byte' in bad_re.stderr
    assert git(root / 'self', 'cat-file', '-e', 'main:threads/bad-re-open/README.md', check=False).returncode != 0
    assert not loom.errors


def test_failed_landing_and_tainted_open_leave_no_letter(tmp_path):
    root = tmp_path / 'home'
    init_self(root, person='test')
    write_thread(root, 'opener', 'Opener', 'opener', wait='0')
    receipt = root / 'receipt'
    bodies(root, f'''import json, subprocess, sys
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx
from brr.loom.runtime.selfrepo import git
ctx = Ctx(Path(sys.argv[1]))
for letter in ctx.owed():
    ctx.send("thread:opener", note="begin", re=letter.id)
def send(thread, *extra):
    return subprocess.run([sys.executable, "-m", "brr.loom.runtime", "send", "--room", str(ctx.room),
                           "--to", "thread:" + thread, "--wish", "new:Work", *extra, "Work"], text=True, capture_output=True)
existing = send("opener", "--body", "fake/test-core")
unsafe = send("../outside")
duplicate = send("duplicate-repos", "--repo", "https://example/a.git", "--repo", "https://other/a.git")
injected = send("injected-repo", "--repo", "https://example/a.git\\nbody: fake/other")
clone = ctx.room / "self"
path = clone / "threads/opener/README.md"
path.write_text(path.read_text().replace("status: open", "status: invalid"))
git(clone, "add", "threads/opener/README.md")
git(clone, "commit", "-m", "invalid status must be refused")
failed = send("failed-open")
git(clone, "reset", "--hard", "origin/main")
# A web receipt not yet ingested still taints; the check must precede writes.
(ctx.room / "port/out/taint-pending.json").write_text('{{"tool":"WebSearch"}}')
tainted = send("tainted-open")
assert not (clone / "threads/tainted-open").exists()
Path({str(receipt)!r}).write_text(json.dumps({{k: [p.returncode, p.stderr] for k, p in
                                            [("existing", existing), ("unsafe", unsafe),
                                             ("failed", failed), ("tainted", tainted), ("duplicate", duplicate), ("injected", injected)]}}))
''')
    inject_letter(root, to='thread:opener', body='begin')
    loom = Loom(root)
    loom.start()
    try:
        wait_until(receipt.exists, 18, loom.dump)
        rows = json.loads(receipt.read_text())
        assert all(code == 1 for code, _ in rows.values()), rows
        assert '--body is only for opening' in rows['existing'][1]
        assert 'declared twice' in rows['duplicate'][1]
        assert 'one seat line' in rows['injected'][1]
        assert not (root / 'self/threads/duplicate-repos').exists()
        assert not (root / 'self/threads/injected-repo').exists()
        assert 'unsafe thread id' in rows['unsafe'][1]
        assert 'pre-receive hook declined' in rows['failed'][1]
        assert 'tainted strand' in rows['tainted'][1] and 'clean letter to brnrd' in rows['tainted'][1]
        assert not (root / 'self/threads/failed-open').exists()
        assert not (root / 'self/threads/tainted-open').exists()
        wait_until(lambda: holder(loom.facts(), 'opener') is None, 30, loom.dump)
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not any(f.kind == 'letter' and f.data.get('to') in {'thread:failed-open', 'thread:tainted-open'}
                   for f in loom.facts())
    assert not loom.errors

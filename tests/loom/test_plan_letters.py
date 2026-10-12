"""A body lands citations and sees unplanned letters at its live boundary."""
import json

from brr.loom.runtime.check import violations
from brr.loom.runtime.ledger import inject_letter
from brr.loom.runtime.lifecycle import main_snapshot
from _step import Loom, wait_until, write_thread
from test_lifecycle import bodies


def test_landed_plan_cites_only_its_letters_and_new_mail_has_no_row(tmp_path):
    root = tmp_path / 'home'
    from brr.loom.runtime.selfrepo import init_self
    init_self(root, person='test')
    write_thread(root, 'work', 'Plan citations', 'custom', wait='0')
    ready, arrived, finish = [root / name for name in ('ready', 'arrived', 'finish')]
    bodies(root, f'''import json, subprocess, sys, time
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx
from brr.loom.runtime.selfrepo import git
ctx = Ctx(Path(sys.argv[1]))
first = ctx.owed()[0]
path = ctx.room / 'self/threads/work/README.md'
# An id in prose or in an unlanded edit cannot answer the letter.
path.write_text(path.read_text() + '\\n## Plan\\n- [ ] Read ' + first.id + '\\n')
git(ctx.room / 'self', 'add', 'threads/work/README.md')
git(ctx.room / 'self', 'commit', '-m', 'Name the work')
subprocess.run([sys.executable, '-m', 'brr.loom.runtime', 'send-self', '--room', str(ctx.room / 'self')], check=True)
deadline = time.monotonic() + 20
boundary = ctx.room / 'port/in/boundary.md'
while 'letters without a row: ' + first.id not in boundary.read_text():
    assert time.monotonic() < deadline
    ctx.jack('post')
    time.sleep(.03)
path.write_text(path.read_text().replace('- [ ] Read ' + first.id,
                                       '- [x] Read · re: ' + first.id))
git(ctx.room / 'self', 'add', 'threads/work/README.md')
git(ctx.room / 'self', 'commit', '-m', 'Cite the ask')
subprocess.run([sys.executable, '-m', 'brr.loom.runtime', 'send-self', '--room', str(ctx.room / 'self')], check=True)
while 'letters without a row:' in boundary.read_text():
    assert time.monotonic() < deadline
    ctx.jack('post')
    time.sleep(.03)
Path({str(ready)!r}).touch()
while True:
    seen = ctx.jack('post').letters
    second = next((l for l in seen if l.id != first.id), None)
    if second and 'letters without a row: ' + second.id in boundary.read_text():
        Path({str(arrived)!r}).write_text(json.dumps({{'unplanned': second.id, 'planned': first.id}}))
        break
    assert time.monotonic() < deadline
    time.sleep(.03)
while not Path({str(finish)!r}).exists():
    time.sleep(.03)
for letter in (first, second):
    ctx.send('thread:work', note='completed', re=letter.id)
''')
    first = inject_letter(root, to='thread:work', body='First work')
    loom = Loom(root)
    loom.start()
    try:
        wait_until(ready.exists, 20, loom.dump)
        second = inject_letter(root, to='thread:work', body='New work')
        wait_until(arrived.exists, 10, loom.dump)
        assert json.loads(arrived.read_text()) == {'unplanned': second, 'planned': first}
        from brr.loom.runtime.owners import letter_owners
        table = letter_owners(loom.facts(), main_snapshot(loom.home))
        assert table[first].plan_rows == (1,)
        assert table[second].plan_rows == ()
        finish.touch()
        wait_until(lambda: all(f.done for f in letter_owners(loom.facts(), main_snapshot(loom.home)).values()),
                   10, loom.dump)
    finally:
        finish.touch()
        # halt stops the loop but deliberately preserves bodies. Kill this
        # test's sessions explicitly so failed assertions cannot leak one.
        import os, signal
        for fact in loom.facts():
            if fact.kind == 'body.started':
                try:
                    os.killpg(fact.data['pid'], signal.SIGKILL)
                except ProcessLookupError:
                    pass
        loom.halt()
    assert not loom.errors
    assert not violations(loom.facts(), main_snapshot(loom.home))

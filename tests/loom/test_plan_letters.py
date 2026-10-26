"""A body lands plain Plan rows that cite letters; the boundary never nags for a row."""
import json

from brr.loom.runtime.check import violations
from brr.loom.runtime.ledger import inject_letter
from brr.loom.runtime.lifecycle import main_snapshot
from _step import Loom, wait_until, write_thread, kill_bodies
from test_lifecycle import bodies


def test_landed_plan_cites_only_its_letters_and_no_row_is_asked(tmp_path):
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
boundary = ctx.room / 'port/in/boundary.md'
# A plain row cites its letter; no checkbox, and no row nag ever.
path.write_text(path.read_text() + '\\n## Plan\\n- Read · re: ' + first.id + '\\n')
git(ctx.room / 'self', 'add', 'threads/work/README.md')
git(ctx.room / 'self', 'commit', '-m', 'Cite the ask')
subprocess.run([sys.executable, '-m', 'brr.loom.runtime', 'send-self', '--room', str(ctx.room / 'self')], check=True)
deadline = time.monotonic() + 20
Path({str(ready)!r}).touch()
while True:
    seen = ctx.jack('post').letters
    second = next((l for l in seen if l.id != first.id), None)
    if second:
        text = boundary.read_text()
        assert 'letters without a row' not in text and ' · plan ' not in text, text
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
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors
    assert not violations(loom.facts(), main_snapshot(loom.home))

"""A recorded reset is visible and an operator can start that debt anyway."""
import json
import sys
import time

from brr.loom.runtime.check import violations
from brr.loom.runtime.ledger import inject_letter
from brr.loom.runtime.lifecycle import main_snapshot
from brr.loom.runtime.owners import letter_owners
from brr.loom.runtime.selfrepo import git
from brr.loom.runtime.verbs_threads import _threads
from _step import Loom, wait_until, write_thread, kill_bodies


def test_paced_row_tells_the_operator_how_to_start_without_consuming_the_letter(tmp_path, capsys):
    root = tmp_path / 'home'
    write_thread(root, 'work', 'Paced work', 'answer-fast', wait='0')
    (root / 'self/threads/work/seat').write_text('body: low/echo\n')
    git(root / 'self', 'add', 'threads')
    git(root / 'self', 'commit', '-m', 'Choose low-fuel body')
    folder = root / 'loom/bodies/low'
    folder.mkdir(parents=True)
    (folder / 'jack').write_text('hand\n')
    run = folder / 'run'
    run.write_text(f'''#!{sys.executable}
import sys
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx
ctx = Ctx(Path(sys.argv[1]))
for letter in ctx.owed():
    ctx.send('thread:work', note='forced start completed work', re=letter.id)
''')
    run.chmod(0o755)
    until = round(time.time()) + 3600
    usage = folder / 'usage'
    usage.write_text(f'''#!{sys.executable}
import json
from datetime import datetime, timezone
print(json.dumps({{'windows': [{{'name': 'week', 'left_pct': 0,
    'resets_at': datetime.fromtimestamp({until}, timezone.utc).isoformat()}}]}}))
''')
    usage.chmod(0o755)
    ident = inject_letter(root, to='thread:work', body='Finish before reset')
    loom = Loom(root)
    loom.start()
    try:
        wait_until(lambda: any(f.kind == 'body.parked' for f in loom.facts()), 15, loom.dump)
        assert _threads(['--home', str(root)]) == 0
        lines = [line.split(' · ') for line in capsys.readouterr().out.splitlines()]
        row = next(dict(zip(lines[0], line)) for line in lines[1:] if line[0] == 'work')
        assert row['now'] == 'paced'
        assert row['owed-by'] == 'loom:work'
        assert float(row['restart'].removeprefix('reset ')) == until
        assert 'operator' in row['control'] and 'threads --force work' in row['control']
        assert not letter_owners(loom.facts(), main_snapshot(loom.home))[ident].done
        assert not any(f.kind == 'attempt.ended' for f in loom.facts())
        assert _threads(['--home', str(root), '--force', 'work']) == 0
        wait_until(lambda: letter_owners(loom.facts(), main_snapshot(loom.home))[ident].done,
                   15, loom.dump)
        assert any(f.kind == 'note' and f.data.get('why') == 'forced start completed work'
                   for f in loom.facts())
        assert len([f for f in loom.facts() if f.kind == 'body.started']) == 1
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors
    assert not violations(loom.facts(), main_snapshot(loom.home))

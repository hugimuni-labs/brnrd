"""A killed body's author can recover the room from the bounce alone."""

import json
import os
import signal
from pathlib import Path

import pytest

from brr.loom.runtime.ledger import inject_letter
from brr.loom.runtime.selfrepo import git, init_self

from _step import Loom, wait_until, write_thread
from test_lifecycle import bodies


@pytest.mark.parametrize('commits, edits', [(2, 0), (0, 1), (12, 12)],
                         ids=['two-commits', 'dirty-readme', 'capped-mixed-room'])
def test_killed_body_bounce_reaches_author_and_recovers_room(tmp_path, commits, edits):
    root = tmp_path / 'home with spaces'
    init_self(root, person='test')
    write_thread(root, 'opener', 'Opener', 'custom', wait='0')
    write_thread(root, 'author', 'Author', 'custom', wait='0')
    write_thread(root, 'worker', '---\nid: worker\nstatus: open\ntense: plan\n'
                 'opened-by: thread:opener\n---\n# Worker\n', 'custom', wait='0')
    ready, recovered = root / 'ready.json', root / 'recovered.json'
    dispatched = root / 'dispatcher-pid'
    received = root / 'author-received.json'
    bodies(root, f'''import json, os, re, shlex, subprocess, sys, time
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx, _own_thread
from brr.loom.runtime.selfrepo import git
ctx = Ctx(Path(sys.argv[1]))
own = _own_thread(ctx.wake())
letters = ctx.owed()
if own == 'worker' and any(l.body == 'Keep this work' for l in letters):
    clone = ctx.room / 'self'
    memory = clone / 'memory' / 'moves'
    memory.mkdir(parents=True, exist_ok=True)
    # The room's local main will still be its birth snapshot after this lands.
    (memory / 'already-landed.md').write_text('already saved\\n')
    git(clone, 'add', 'memory')
    git(clone, 'commit', '-m', 'Already landed before failure')
    p = subprocess.run([sys.executable, '-m', 'brr.loom.runtime', 'send-self',
                        '--room', str(clone)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    hashes = {{}}
    for n in range({commits}):
        (memory / f'kept-{{n}}.md').write_text(f'saved {{n}}\\n')
        git(clone, 'add', 'memory')
        git(clone, 'commit', '-m', f'Keep work {{n}}')
        hashes[f'Keep work {{n}}'] = git(clone, 'rev-parse', '--short', 'HEAD').stdout.strip()
    paths = []
    for n in range({edits}):
        path = clone / ('threads/worker/README.md' if {edits} == 1 else f'memory/moves/dirty-{{n}}.md')
        path.write_text(path.read_text() + '\\nUnsaved memory\\n' if path.exists() else 'Unsaved memory\\n')
        paths.append(str(path.relative_to(clone)))
    Path({str(ready)!r}).write_text(json.dumps({{'pid': os.getpid(), 'room': str(ctx.room),
                                               'hashes': hashes, 'paths': paths}}))
    while True:
        time.sleep(.03)
assert own in {{'author', 'worker'}}, 'bounce went to the opener instead of the author'
if own == 'author' and any(l.body == 'begin' for l in letters):
    for l in letters:
        ctx.send('thread:author', note='dispatched', re=l.id)
    ctx.send('thread:worker', 'Keep this work')
    Path({str(dispatched)!r}).write_text(str(os.getpid()))
    sys.exit(0)
# A letter shown in the wake is cut to its first line at the next boundary.
# Wait until the loom has done that, then read the whole text where a body
# reads it: in the wake.
time.sleep(1)
bounce = next(l for l in ctx.owed() if 'body exited' in l.body)
rest = ctx.wake().split('### ' + bounce.id + ' ', 1)
assert len(rest) == 2, 'the wake does not carry the letter'
kept = []
for line in rest[1].splitlines()[1:]:
    if line.startswith(('## ', '### ')):
        break
    kept.append(line)
text = chr(10).join(kept).rstrip()
if own == 'author':
    Path({str(received)!r}).write_text(json.dumps({{'body': text, 'reader': own, 'pid': os.getpid()}}))
    ctx.send('thread:worker', text)
    ctx.send('thread:author', note='asked next worker to recover its room', re=bounce.id)
    sys.exit(0)
assert 'Already landed before failure' not in text
assert 'Left behind in room ' in text
clone_match = re.search(r'self clone: (.*?)\\):', text)
clone = Path(clone_match.group(1))
assert clone.is_dir()
if {edits}:
    assert 'Uncommitted paths' in text
    if {edits} == 1:
        assert 'threads/worker/README.md' in text
    else:
        assert 'and 2 more paths' in text
    # Save the named edits before executing the landing command.
    git(clone, 'add', '-A')
    git(clone, 'commit', '-m', 'Save recovered edits')
if {commits}:
    if {commits} > 10:
        assert 'and 2 more commits' in text
    else:
        ready = json.loads(Path({str(ready)!r}).read_text())
        for subject, sha in ready['hashes'].items():
            assert sha + ' ' + subject in text
    command = re.search(r'run: `([^`]+)`', text).group(1)
    p = subprocess.run(shlex.split(command), capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
else:
    assert 'Commits on ' not in text
ctx.send('thread:worker', note='recovered failed room', re=bounce.id)
Path({str(recovered)!r}).write_text(json.dumps({{'body': text, 'reader': own, 'pid': os.getpid()}}))
''')
    inject_letter(root, to='thread:author', body='begin')
    loom = Loom(root)
    loom.start()
    pid = None
    try:
        wait_until(ready.exists, 25, loom.dump)
        evidence = json.loads(ready.read_text())
        pid = evidence['pid']
        # The next body on the author thread, not the dispatching process,
        # must read the bounce with no earlier context.
        wait_until(lambda: any(f.kind == 'body.exited' and
                              f.data['strand'] != os.path.basename(evidence['room'])
                              for f in loom.facts()), 8, loom.dump)
        os.kill(pid, signal.SIGKILL)
        pid = None
        wait_until(recovered.exists, 25, loom.dump)
        result = json.loads(recovered.read_text())
        assert result['reader'] == 'worker'
        author = json.loads(received.read_text())
        assert author['reader'] == 'author'
        assert evidence['room'] in result['body']
        if commits:
            for n in range(commits):
                assert git(root / 'self', 'show', f'main:memory/moves/kept-{n}.md').stdout == f'saved {n}\n'
        if edits:
            for path in sorted(evidence['paths'])[:10]:
                assert path in result['body']
            assert 'Unsaved memory' in Path(evidence['room'], 'self', evidence['paths'][0]).read_text()
        assert result['pid'] != evidence['pid']
        assert author['pid'] != int(dispatched.read_text())
    finally:
        if pid is not None:
            os.kill(pid, signal.SIGKILL)
        loom.halt()
    assert not loom.errors

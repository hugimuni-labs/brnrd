"""A loom upgrade preserves work; a body's local plan changes reach its boundary."""

import json
import os
import signal
from pathlib import Path

import pytest

from brr.loom.runtime.ledger import inject_letter
from brr.loom.runtime.loom import body_alive
from brr.loom.runtime.project import fold, holder, owed
from brr.loom.runtime.selfrepo import git

from _step import Loom, wait_until, write_thread
from test_lifecycle import bodies
from test_router import Proc, _config, _dump, _facts, _stop


@pytest.mark.parametrize('stop_signal', [signal.SIGKILL, signal.SIGTERM])
def test_restart_inherits_mid_letter_body_and_records_its_eventual_exit(tmp_path, stop_signal):
    root = tmp_path / 'home'
    write_thread(root, 'work', 'Work', 'work', wait='0')
    _config(root, router_ttl=2, max_skew=0, margin=0.1)
    ready, resume = root / 'ready', root / 'resume'
    bodies(root, f'''import json, os, sys, time
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx
ctx = Ctx(Path(sys.argv[1]))
letters = ctx.owed()
Path({str(ready)!r}).write_text(str(os.getpid()))
deadline = time.monotonic() + 20
while not Path({str(resume)!r}).exists():
    assert time.monotonic() < deadline
    time.sleep(0.03)
print("output after loom restart", flush=True)
for letter in letters:
    ctx.send("channel:fake", json.dumps({{"answer": letter.body, "pid": os.getpid()}}), re=letter.id)
''')
    letter = inject_letter(root, to='thread:work', body='finish this once')
    first = Proc(root, 'aaaa')
    procs = [first]
    try:
        wait_until(lambda: ready.exists() and any(letter in ids for ids in fold(_facts(root)).shown.values()),
                   10, lambda: _dump(root, procs))
        pid = int(ready.read_text())
        assert letter in [f.id for f in owed(_facts(root), 'work')]
        held = holder(_facts(root), 'work')[0]
        os.kill(first.proc.pid, stop_signal)
        first.proc.wait(timeout=5)
        assert body_alive(root / 'rooms' / held)
        # An uncommitted edit proves that the new loop is refreshing the old room.
        readme = root / 'rooms' / held / 'self/threads/work/README.md'
        readme.write_text(readme.read_text() + '\n## Plan\n- [ ] Survive the upgrade\n')
        second = Proc(root, 'aaaa')
        procs.append(second)
        boundary = root / 'rooms' / held / 'port/in/boundary.md'
        wait_until(lambda: 'next: Survive the upgrade' in boundary.read_text(),
                   10, lambda: _dump(root, procs))
        assert body_alive(root / 'rooms' / held)
        assert len([f for f in _facts(root) if f.kind == 'body.started']) == 1
        assert not any(f.kind == 'body.exited' for f in _facts(root))
        resume.touch()
        sent = root / 'loom/channel-fake.jsonl'
        wait_until(lambda: sent.exists() and not owed(_facts(root), 'work')
                   and holder(_facts(root), 'work') is None, 10, lambda: _dump(root, procs))
        messages = [json.loads(row) for row in sent.read_text().splitlines()]
        assert [json.loads(m['text']) for m in messages] == [{'answer': 'finish this once', 'pid': pid}]
        assert len([f for f in _facts(root) if f.kind == 'body.started']) == 1
        assert len([f for f in _facts(root) if f.kind == 'body.exited']) == 1
        assert 'output after loom restart' in (root / 'rooms' / held / 'port/body.log').read_text()
    finally:
        _stop(root, procs)


def test_restart_records_body_that_ended_while_loom_was_down(tmp_path):
    root = tmp_path / 'home'
    write_thread(root, 'work', 'Work', 'work', wait='0')
    _config(root, router_ttl=2, max_skew=0, margin=0.1)
    ready, resume = root / 'ready', root / 'resume'
    bodies(root, f'''import os, sys, time
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx
ctx = Ctx(Path(sys.argv[1]))
letters = ctx.owed()
Path({str(ready)!r}).write_text(str(os.getpid()))
while not Path({str(resume)!r}).exists():
    time.sleep(0.03)
for letter in letters:
    ctx.send("channel:fake", "finished while loom was down", re=letter.id)
''')
    inject_letter(root, to='thread:work', body='finish')
    first = Proc(root, 'aaaa')
    procs = [first]
    try:
        wait_until(ready.exists, 10, lambda: _dump(root, procs))
        strand = holder(_facts(root), 'work')[0]
        first.kill()
        resume.touch()
        wait_until(lambda: not body_alive(root / 'rooms' / strand), 5, lambda: _dump(root, procs))
        assert not any(f.kind == 'body.exited' for f in _facts(root))
        second = Proc(root, 'aaaa')
        procs.append(second)
        sent = root / 'loom/channel-fake.jsonl'
        wait_until(lambda: sent.exists() and holder(_facts(root), 'work') is None,
                   10, lambda: _dump(root, procs))
        assert [json.loads(row)['text'] for row in sent.read_text().splitlines()] == ['finished while loom was down']
        assert len([f for f in _facts(root) if f.kind == 'body.started']) == 1
        assert len([f for f in _facts(root) if f.kind == 'body.exited']) == 1
    finally:
        _stop(root, procs)


def test_room_plan_is_live_stale_on_new_mail_and_delivered_once_per_edit(tmp_path):
    root = tmp_path / 'home'
    write_thread(root, 'work', 'Work\n\n## Plan\n- [x] First\n- [ ] Second', 'work', wait='0')
    ready, complete, finished = [root / name for name in ('ready', 'complete', 'finished')]
    bodies(root, f'''import json, os, sys, time
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx
ctx = Ctx(Path(sys.argv[1]))
readme = ctx.room / "self/threads/work/README.md"
first = ctx.jack("start")
assert "plan 1/2 · next: Second" in first.raw, first.raw
for letter in first.letters:
    ctx.send("thread:work", note="begin", re=letter.id)
deadline = time.monotonic() + 15
while ctx.jack("post").letters:
    assert time.monotonic() < deadline
    time.sleep(0.03)
assert ctx.jack("post").raw == ""
# Work changes locally, without committing or landing the README.
readme.write_text(readme.read_text().replace("[ ] Second", "[x] Second") + "- [ ] Third " + "long " * 20 + "\\n")
while True:
    view = ctx.jack("post")
    if "plan 2/3" in view.raw:
        break
    assert time.monotonic() < deadline
    time.sleep(0.03)
text = json.loads(view.raw)["hookSpecificOutput"]["additionalContext"]
next_row = text.split(" · next: ")[1].split("\\n")[0]
assert len(next_row) == 60, next_row
assert "Third" in next_row
assert ctx.jack("post").raw == ""
Path({str(ready)!r}).write_text(view.raw)
while True:
    view = ctx.jack("post")
    if "plan older than 1 letter(s)" in view.raw:
        Path({str(complete)!r}).write_text(view.raw)
        for letter in view.letters:
            ctx.send("thread:work", note="followup read", re=letter.id)
        break
    assert time.monotonic() < deadline
    time.sleep(0.03)
while ctx.jack("post").letters:
    time.sleep(0.03)
while "plan older than" in (ctx.room / "port/in/boundary.md").read_text():
    time.sleep(0.03)
ctx.jack("post")  # the freshness change is delivered once too
assert ctx.jack("post").raw == ""
# Removing the section is itself a change, then silent again.
readme.write_text(readme.read_text().split("\\n## Plan")[0] + "\\n")
while True:
    view = ctx.jack("post")
    if view.raw:
        assert " · plan " not in view.raw, view.raw
        assert ctx.jack("post").raw == ""
        break
    assert time.monotonic() < deadline
    time.sleep(0.03)
Path({str(finished)!r}).write_text("all boundary assertions reached")
''')
    inject_letter(root, to='thread:work', body='begin')
    loom = Loom(root)
    loom.start()
    try:
        wait_until(ready.exists, 12, loom.dump)
        assert 'plan 2/3' in ready.read_text()
        # Main still has its original plan. The boundary followed the working copy.
        assert '- [ ] Second' in git(root / 'self', 'show', 'main:threads/work/README.md').stdout
        inject_letter(root, to='thread:work', body='a later ask')
        wait_until(complete.exists, 8, loom.dump)
        assert 'plan 2/3 · plan older than 1 letter(s)' in json.loads(complete.read_text())['hookSpecificOutput']['additionalContext']
        wait_until(lambda: finished.exists() and holder(loom.facts(), 'work') is None, 5, loom.dump)
        assert not any(f.id.startswith('loom/bounce-') or f.id.startswith('attention:wasted:') for f in loom.facts())
    finally:
        loom.halt()
    assert not loom.errors

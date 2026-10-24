"""A loom upgrade preserves work; landed plans reach a body's live boundary."""

import json
import os
import signal
import subprocess
import sys

import pytest

from brr.loom.runtime.ledger import inject_letter
from brr.loom.runtime.loom import body_alive
from brr.loom.runtime.project import fold, holder
from brr.loom.runtime.selfrepo import init_self

from _step import Loom, kill_bodies, mail, wait_until, write_thread
from test_lifecycle import bodies
from test_router import Proc, _config, _dump, _facts, _stop


@pytest.mark.parametrize('stop_signal', [signal.SIGKILL, signal.SIGTERM])
def test_restart_inherits_mid_letter_body_and_records_its_eventual_exit(tmp_path, stop_signal):
    root = tmp_path / 'home'
    init_self(root, person='test')
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
        assert letter in [f.id for f in mail(root, _facts(root), 'work')]
        held = holder(_facts(root), 'work')[0]
        os.kill(first.proc.pid, stop_signal)
        first.proc.wait(timeout=5)
        assert body_alive(root / 'rooms' / held)
        # A wish stamped while no loop runs proves the new loop refreshes the inherited room.
        subprocess.run([sys.executable, '-m', 'brr.loom.runtime', 'wish', 'new', '--line', 'Survive the upgrade',
                        '--say', letter, '--room', str(root / 'rooms' / held)], check=True, capture_output=True)
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
        wait_until(lambda: sent.exists() and not mail(root, _facts(root), 'work')
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


def test_wish_progress_reaches_live_body_and_is_delivered_once_per_change(tmp_path):
    root = tmp_path / 'home'
    init_self(root, person='test')
    # Ticked rows count for nothing: the plan is the thread's wishes.
    write_thread(root, 'work', 'Work\n\n## Plan\n- [x] First\n- [ ] Second', 'work', wait='0')
    ready, complete, finished = [root / name for name in ('ready', 'complete', 'finished')]
    bodies(root, f"""import json, subprocess, sys, time
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx
ctx = Ctx(Path(sys.argv[1]))
def wish(*args):
    proc = subprocess.run([sys.executable, "-m", "brr.loom.runtime", "wish", *args,
                           "--room", str(ctx.room)], capture_output=True, text=True, check=True,
                          env=ctx.env, cwd=ctx.room)
    return proc.stdout.strip()
def until(word):
    while True:
        view = ctx.jack("post")
        if word in view.raw:
            return view
        assert time.monotonic() < deadline, view.raw
        time.sleep(0.03)
first = ctx.jack("start")
assert " · plan " not in first.raw and "letters without a row" not in first.raw, first.raw
letter = first.letters[0].id
one = wish("new", "--line", "First", "--say", letter)
two = wish("new", "--line", "Second " + "long " * 20, "--say", letter)
ctx.send("thread:work", note="begin", re=letter)
deadline = time.monotonic() + 30
until("plan 0/2")
assert ctx.jack("post").raw == ""
wish(one, "--result", "pr:https://example.com/1", "--line", "First done", "--deliver")
view = until("plan 1/2")
text = json.loads(view.raw)["hookSpecificOutput"]["additionalContext"]
next_row = text.split(" · next: ")[1].split("\\n")[0]
assert len(next_row) == 60 and next_row.startswith("Second"), next_row
assert ctx.jack("post").raw == ""
Path({str(ready)!r}).write_text(view.raw)
view = ctx.jack("post")
while not view.letters:
    view = ctx.jack("post")
    assert time.monotonic() < deadline
    time.sleep(0.03)
Path({str(complete)!r}).write_text(view.raw)
for later in view.letters:
    ctx.send("thread:work", note="followup read", re=later.id)
wish(two, "--result", "pr:https://example.com/2", "--line", "Second done", "--deliver")
view = until("plan 2/2")
assert " · next: " not in view.raw, view.raw
while ctx.jack("post").raw:
    assert time.monotonic() < deadline
    time.sleep(0.03)
Path({str(finished)!r}).write_text("all boundary assertions reached")
""")
    inject_letter(root, to='thread:work', body='begin')
    loom = Loom(root)
    loom.start()
    try:
        wait_until(ready.exists, 12, loom.dump)
        assert 'plan 1/2' in ready.read_text()
        later = inject_letter(root, to='thread:work', body='a later ask')
        wait_until(complete.exists, 8, loom.dump)
        text = json.loads(complete.read_text())['hookSpecificOutput']['additionalContext']
        assert later in text and 'letters without a row' not in text, text
        wait_until(finished.exists, 30, loom.dump)
        wait_until(lambda: holder(loom.facts(), 'work') is None, 5, loom.dump)
        assert not any(f.id.startswith('loom/bounce-') or f.id.startswith('attention:wasted:') for f in loom.facts())
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors

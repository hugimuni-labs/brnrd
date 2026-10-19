"""A body schedules its own next letter; only its due observation wakes a reader."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from brr.loom.runtime import timers
from brr.loom.runtime.ledger import inject_letter
from brr.loom.runtime.lifecycle import main_snapshot
from brr.loom.runtime.owners import letter_owners
from brr.loom.runtime.project import holder
from brr.loom.runtime.selfrepo import git, init_self

from _step import Loom, kill_bodies, mail, wait_until, write_thread
from test_lifecycle import bodies


def _clock(monkeypatch):
    class Clock(datetime):
        ticks = 0
        instant = datetime.now(timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.instant

    monkeypatch.setattr(timers, 'datetime', Clock)
    from brr.loom.runtime import loom
    original = loom.tick_once

    def tick(*args, **kwargs):
        original(*args, **kwargs)
        Clock.ticks += 1

    monkeypatch.setattr(loom, 'tick_once', tick)
    return Clock


def _starts(loom, thread):
    writers = {f.data['strand']: f.data['thread'] for f in loom.facts() if f.kind == 'lease'}
    return [f for f in loom.facts() if f.kind == 'body.started'
            and writers.get(f.data['strand']) == thread]


def _command(*args):
    return subprocess.run([sys.executable, '-m', 'brr', *args],
                          text=True, capture_output=True, timeout=15)


def _schedule(root, at, *, cancel=False, fail=False, opener=False):
    if opener:
        write_thread(root, 'opener', 'Opener', 'answer-fast', wait='0')
    readme = ('---\nid: work\nstatus: open\ntense: plan\n'
              + ('opened-by: thread:opener\n' if opener else '') + '---\n# Work\n')
    write_thread(root, 'work', readme, 'timer', wait='0')
    bodies(root, f'''import json, subprocess, sys
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx, _own_thread
from brr.loom.runtime.home import atomic_write
ctx = Ctx(Path(sys.argv[1]))
own = _own_thread(ctx.wake())
letters = ctx.owed()
if any(l.body == "begin" for l in letters):
    for letter in letters:
        ctx.send("thread:work", note="scheduled next move", re=letter.id)
    def send(*args):
        return subprocess.run([sys.executable, "-m", "brr.loom.runtime", "send", "--room", str(ctx.room),
                               "--to", "thread:work", *args], capture_output=True, text=True)
    proc = send("--at", {at!r}, "next move\\nMore detail")
    assert proc.returncode == 0, proc.stderr
    ident = proc.stdout.strip()
    if {cancel!r}:
        # send queues a port file; wait for the scheduled letter to be accepted
        # before exercising cancellation of an existing waiting letter.
        from brr.loom.runtime.ledger import read_facts
        from brr.loom.runtime.home import Home
        import time
        deadline = time.monotonic() + 10
        home = Home(ctx.room.parent.parent)
        while not any(f.id == ident for f in read_facts(home)):
            assert time.monotonic() < deadline, "scheduled letter was not ingested"
            time.sleep(0.01)
        cancelled = send("--re", ident, "--note", "cancelled before its time")
        assert cancelled.returncode == 0, cancelled.stderr
    atomic_write(Path({str(root / 'scheduled')!r}), json.dumps({{"id": ident, "room": str(ctx.room)}}))
else:
    if {fail!r} and own == "work":
        print("scheduled body failed", flush=True)
        sys.exit(7)
    atomic_write(Path({str(root / 'received')!r}), ctx.wake())
    for letter in letters:
        ctx.send("thread:" + own, note="next move completed", re=letter.id)
''')
    inject_letter(root, to='thread:work', body='begin')
    loom = Loom(root)
    loom.start()
    try:
        wait_until((root / 'scheduled').exists, 15, loom.dump)
        receipt = json.loads((root / 'scheduled').read_text())
        wait_until(lambda: holder(loom.facts(), 'work') is None, 15, loom.dump)
        return loom, receipt
    except BaseException:
        loom.halt()
        kill_bodies(loom)
        raise


def test_self_letter_waits_then_wakes_once_across_restart(tmp_path, monkeypatch):
    clock = _clock(monkeypatch)
    due = clock.instant + timedelta(minutes=1)
    at = due.strftime('%Y-%m-%dT%H:%M:%SZ')
    root = tmp_path / 'home'
    init_self(root, person='test')
    loom, receipt = _schedule(root, at)
    ident = receipt['id']
    try:
        view = _command('threads', '--home', str(root), '--due')
        assert view.returncode == 0, view.stderr
        assert f'{at} · {ident} · work · work · next move' in view.stdout
        fate = letter_owners(loom.facts(), main_snapshot(loom.home))[ident]
        assert fate.owner == 'clock' and fate.action == 'wait'
        assert not mail(root, loom.facts(), 'work')
        row = _command('threads', '--home', str(root)).stdout
        assert 'when' not in row and 'next letter' in row and 'clock' not in row
        assert len(_starts(loom, 'work')) == 1
        assert not (root / 'received').exists()
        # Stop before it is due: the first tick after restart must still find it.
        loom.halt()
        clock.instant = due + timedelta(seconds=1)
        loom = Loom(root)
        loom.start()
        wait_until((root / 'received').exists, 15, loom.dump)
        assert 'next move\n' in (root / 'received').read_text()
        wait_until(lambda: holder(loom.facts(), 'work') is None, 15, loom.dump)
        assert len([f for f in loom.facts() if f.kind == 'letter.due' and f.id == 'due:' + ident]) == 1
        assert len(_starts(loom, 'work')) == 2
        assert ident not in _command('threads', '--home', str(root), '--due').stdout
        # More real ticks, including a fresh router, never start it twice.
        loom.halt()
        loom = Loom(root)
        ticks = clock.ticks
        loom.start()
        wait_until(lambda: clock.ticks >= ticks + 2, 5, loom.dump)
        assert not mail(root, loom.facts(), 'work')
        assert len(_starts(loom, 'work')) == 2
        assert len([f for f in loom.facts() if f.kind == 'letter.due']) == 1
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors


def test_note_cancels_waiting_letter_before_time(tmp_path, monkeypatch):
    clock = _clock(monkeypatch)
    root = tmp_path / 'home'
    init_self(root, person='test')
    loom, receipt = _schedule(root, '+10m', cancel=True)
    try:
        ident = receipt['id']
        assert ident not in _command('threads', '--home', str(root), '--due').stdout
        assert letter_owners(loom.facts(), main_snapshot(loom.home))[ident].done
        clock.instant += timedelta(days=1)
        # Restart drives later ticks after the cancellation was already ingested.
        loom.halt()
        loom = Loom(root)
        ticks = clock.ticks
        loom.start()
        wait_until(lambda: clock.ticks >= ticks + 2, 5, loom.dump)
        assert not mail(root, loom.facts(), 'work')
        assert not (root / 'received').exists()
        assert len(_starts(loom, 'work')) == 1
        assert not any(f.kind == 'letter.due' for f in loom.facts())
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors


def test_send_argv_keeps_own_thread_refusal_and_checks_due_forms(tmp_path):
    root = tmp_path / 'home'
    init_self(root, person='test')
    write_thread(root, 'work', 'Work', 'argv', wait='0')
    bodies(root, f'''import json, subprocess, sys
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx
from brr.loom.runtime.home import atomic_write
ctx = Ctx(Path(sys.argv[1]))
for letter in ctx.owed():
    ctx.send("thread:work", note="check send argv", re=letter.id)
def send(*args):
    return subprocess.run([sys.executable, "-m", "brr.loom.runtime", "send", "--room", str(ctx.room),
                           *args], text=True, capture_output=True)
checks = {{}}
for name, args in [
    ("self", ["--to", "thread:work", "loop"]),
    ("bad", ["--to", "thread:work", "--at", "tomorrow", "loop"]),
    ("channel", ["--to", "channel:fake", "--at", "+10m", "loop"]),
    ("note", ["--to", "thread:work", "--at", "+10m", "--re", "x", "--note", "cancel"]),
    ("reopen", ["--to", "thread:work", "--at", "+10m", "--reopen", "loop"]),
    ("clear", ["--to", "thread:work", "--at", "clear", "loop"]),
]:
    p = send(*args)
    checks[name] = [p.returncode, p.stderr]
for value in ["+10m", "+2h", "+3d"]:
    p = send("--to", "thread:work", "--at", value, value)
    assert p.returncode == 0, p.stderr
atomic_write(Path({str(root / 'argv')!r}), json.dumps(checks))
''')
    inject_letter(root, to='thread:work', body='begin')
    loom = Loom(root)
    loom.start()
    try:
        wait_until((root / 'argv').exists, 15, loom.dump)
        results = json.loads((root / 'argv').read_text())
        assert all(code == 1 for code, _ in results.values()), results
        assert 'own thread' in results['self'][1] and '--at' in results['self'][1]
        assert 'YYYY-MM-DDTHH:MM:SSZ' in results['bad'][1]
        wait_until(lambda: '+3d' in _command('threads', '--home', str(root), '--due').stdout, 5, loom.dump)
        view = _command('threads', '--home', str(root), '--due').stdout
        assert all(value in view for value in ('+10m', '+2h', '+3d'))
        for line in view.splitlines()[1:]:
            assert datetime.strptime(line.split(' · ')[0], '%Y-%m-%dT%H:%M:%SZ')
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors


def test_readme_when_lands_and_starts_nothing(tmp_path, monkeypatch):
    clock = _clock(monkeypatch)
    root = tmp_path / 'home'
    init_self(root, person='test')
    write_thread(root, 'work', 'Work', 'readme', wait='0')
    write_thread(root, 'idle', 'Idle', 'quit-now', wait='0')
    bodies(root, f'''import subprocess, sys
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx
from brr.loom.runtime.selfrepo import git
from brr.loom.runtime.home import atomic_write
ctx = Ctx(Path(sys.argv[1]))
for letter in ctx.owed():
    ctx.send("thread:work", note="land old key", re=letter.id)
clone = ctx.room / "self"
p = clone / "threads/idle/README.md"
p.write_text(p.read_text().replace("tense: plan", "tense: plan\\nwhen: tomorrow"))
git(clone, "add", "threads/idle/README.md")
git(clone, "commit", "-m", "An ignored key is still legal frontmatter")
p = subprocess.run([sys.executable, "-m", "brr.loom.runtime", "send-self", "--room", str(clone)],
                   capture_output=True, text=True)
assert p.returncode == 0, p.stderr
atomic_write(Path({str(root / 'landed')!r}), p.stdout)
''')
    inject_letter(root, to='thread:work', body='begin')
    loom = Loom(root)
    loom.start()
    try:
        wait_until((root / 'landed').exists, 15, loom.dump)
        assert 'when: tomorrow' in git(root / 'self', 'show', 'main:threads/idle/README.md').stdout
        clock.instant += timedelta(days=2)
        loom.halt()
        loom = Loom(root)
        ticks = clock.ticks
        loom.start()
        wait_until(lambda: clock.ticks >= ticks + 2, 5, loom.dump)
        assert _starts(loom, 'idle') == []
        assert not any(f.kind == 'letter.due' for f in loom.facts())
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors


@pytest.mark.parametrize('opener', [False, True])
def test_failed_self_timer_reaches_opener_or_operator(tmp_path, monkeypatch, opener):
    clock = _clock(monkeypatch)
    root = tmp_path / 'home'
    init_self(root, person='test')
    loom, receipt = _schedule(root, '+10m', fail=True, opener=opener)
    try:
        clock.instant += timedelta(hours=1)
        if opener:
            wait_until((root / 'received').exists, 15, loom.dump)
            wake = (root / 'received').read_text()
            assert receipt['id'] in wake and 'body exited 7' in wake
            wait_until(lambda: not mail(root, loom.facts(), 'opener'), 5, loom.dump)
        else:
            wait_until(lambda: receipt['id'] in _command('threads', '--home', str(root)).stdout, 15, loom.dump)
            fate = letter_owners(loom.facts(), main_snapshot(loom.home))[receipt['id']]
            assert fate.owner == 'operator'
        assert len(_starts(loom, 'work')) == 2
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors

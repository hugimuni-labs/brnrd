from _step import mail
"""A wasted start reaches the author; only a person's letter gets a second start."""

import json
import subprocess
import sys
import threading
import time

import pytest

from connector_support import pull_once
from brr.loom.runtime.ledger import inject_letter

from brr.loom.runtime.selfrepo import init_self

from _step import Loom, wait_until, write_thread


def bodies(root, program):
    folder = root / "loom/bodies/fake"
    folder.mkdir(parents=True)
    script = folder / "body.py"
    script.write_text(program)
    (folder / "jack").write_text("hand\n")
    run = folder / "run"
    run.write_text(f"#!{sys.executable}\nimport os, sys\nos.execv(sys.executable, [sys.executable, {str(script)!r}, *sys.argv[1:]])\n")
    run.chmod(0o755)


def test_worker_bounces_once_and_authors_resend_is_answered(tmp_path):
    root = tmp_path / "home"
    write_thread(root, "author", "Author", "author", wait="0")
    write_thread(root, "worker", "Worker", "worker", wait="0")
    received = root / "received.json"
    starts = root / "worker-starts"
    bodies(root, f'''import json, sys, time
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx, _own_thread
ctx = Ctx(Path(sys.argv[1]))
own = _own_thread(ctx.wake())
if own == "worker":
    with Path({str(starts)!r}).open("a") as out:
        out.write("start\\n")
    letters = ctx.owed()
    if any(l.body == "first try" for l in letters):
        print("failure log tail", flush=True)
        sys.exit(7)
    for letter in letters:
        ctx.send(letter.reply, "answered the resend", re=letter.id)
    sys.exit(0)
else:
    for letter in ctx.owed():
        ctx.send("thread:author", note="opener", re=letter.id)
    original = ctx.send("thread:worker", "first try")
    seen = set()
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        for letter in ctx.jack("post").letters:
            if letter.id in seen:
                continue
            seen.add(letter.id)
            if "body exited 7" in letter.body:
                assert original in letter.body and "failure log tail" in letter.body
                assert "README changed since start: False" in letter.body
                assert "fuel" in letter.body and "send again, or leave it" in letter.body
                assert "Left behind in room" not in letter.body
                assert Path({str(starts)!r}).read_text().splitlines() == ["start"]
                ctx.send("thread:author", note="read bounce", re=letter.id)
                ctx.send("thread:worker", "try again")
            elif letter.body == "answered the resend":
                Path({str(received)!r}).write_text(json.dumps({{"answer": letter.body, "bounce_seen": True}}))
                ctx.send("thread:author", note="read answer", re=letter.id)
                sys.exit(0)
        time.sleep(0.03)
raise SystemExit("no reply")
''')
    inject_letter(root, to="thread:author", body="begin")
    loom = Loom(root)
    loom.start()
    try:
        wait_until(received.exists, 15, loom.dump)
        assert json.loads(received.read_text()) == {"answer": "answered the resend", "bounce_seen": True}
        wait_until(lambda: not mail(root, loom.facts(), "worker"), 3, loom.dump)
        time.sleep(0.3)
        assert starts.read_text().splitlines() == ["start", "start"]
    finally:
        loom.halt()
    assert not loom.errors


@pytest.mark.parametrize('delivery', ['sent', 'refused', 'no-channel'])
def test_persons_two_failed_starts_deliver_failure_or_leave_operator_debt(tmp_path, monkeypatch, capsys, delivery):
    root = tmp_path / "home"
    init_self(root, person="ada")
    person = root / "self/people/ada/channels.md"
    person.write_text("relay:telegram:42\n")
    write_thread(root, "inbox", "Inbox", "die-now", wait="0")
    starts = root / "starts"
    bodies(root, f'''import sys
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx
ctx = Ctx(Path(sys.argv[1]))
ctx.owed()
with Path({str(starts)!r}).open("a") as out:
    out.write("start\\n")
print("person failure tail", flush=True)
sys.exit(2)
''')
    class Client:
        def pull(self, cursor):
            return {"cursor": 1, "events": [{"event_id": "crash", "body": "please help\nextra", "attachments": [],
                    "reply_to": {"platform": "telegram", "user_id": 42, "chat_id": 42}}]}
    pull_once(Loom(root).home, Client(), 0)
    sent, attempted = [], []
    sending, release = threading.Event(), threading.Event()
    def effect(home, channel, key, text, context):
        attempted.append(key)
        sending.set()
        assert release.wait(10)
        if delivery == 'refused':
            raise ValueError('channel refused the failure message')
        sent.append((channel, text, context))
        return {}
    real_run = subprocess.run
    def run_out(args, **kw):
        if str(args[0]).endswith('/relay/out'):
            from brr.loom.runtime.port import parse_frontmatter
            from pathlib import Path
            letter = parse_frontmatter(Path(args[1]).read_text())
            effect(Loom(root).home, letter['to'], letter['id'], letter['body'], letter)
            return subprocess.CompletedProcess(args, 0, '{"ids": []}', '')
        return real_run(args, **kw)
    monkeypatch.setattr(subprocess, 'run', run_out)
    if delivery == 'no-channel':
        person.unlink()
    from brr.loom.runtime.check import run as check
    assert check(['--home', str(root)]) == 0
    assert 'loom · waiting for start · letter:relay:telegram:42:crash' in capsys.readouterr().out
    assert check(['--home', str(root), '--quiet-end']) == 1
    assert 'quiet end still owed by loom' in capsys.readouterr().out
    loom = Loom(root)
    loom.start()
    try:
        from brr.loom.runtime.owners import letter_owners
        from brr.loom.runtime.lifecycle import main_snapshot
        from brr.loom.runtime.verbs_loom import _attention
        from brr.loom.runtime.threads import thread_table
        ident = "letter:relay:telegram:42:crash"
        def table():
            return letter_owners(loom.facts(), main_snapshot(loom.home))
        if delivery != 'no-channel':
            wait_until(sending.is_set, 10, loom.dump)
            # The failed ask and its channel successor have no append gap.
            assert table()[ident].done
            assert any(f.owner == 'channel' and 'person failure tail' in f.letter.data['body']
                       for f in table().values())
            assert check(['--home', str(root)]) == 0
            assert 'channel · accepted send awaiting receipt · loom/failed-message-' in capsys.readouterr().out
            assert check(['--home', str(root), '--quiet-end']) == 1
            assert 'quiet end still owed by channel' in capsys.readouterr().out
            release.set()
        if delivery == 'sent':
            wait_until(lambda: all(f.done for f in table().values()), 10, loom.dump)
            assert len(sent) == 1
            channel, text, context = sent[0]
            assert channel == 'telegram:42'  # the folder receives only its native chat
            assert 'could not handle your letter: please help' in text
            assert 'person failure tail' in text
            assert context['re'] == 'telegram:42:crash'
        else:
            wait_until(lambda: any(f.owner == 'operator' for f in table().values()), 10, loom.dump)
            debt = next(k for k, f in table().items() if f.owner == 'operator')
            assert not sent
            assert any(row['thread'] == 'operator' and debt in row['now']
                       for row in thread_table(loom.home, loom.facts()))
            _attention(['--home', str(root)])
            assert debt in capsys.readouterr().out
            assert check(['--home', str(root), '--quiet-end']) == 0
            capsys.readouterr()
            _attention(['--home', str(root), '--note', debt, '--why', 'handled by operator'])
            assert all(f.done for f in table().values())
        assert check(['--home', str(root), '--quiet-end']) == 0
        time.sleep(0.5)
        assert starts.read_text().splitlines() == ['start', 'start']
        assert len(attempted) == (0 if delivery == 'no-channel' else 1)
    finally:
        release.set()
        loom.halt()
    assert not loom.errors

"""A wasted start reaches the author; only a person's letter gets a second start."""

import json
import sys
import time

from brr.loom.runtime import speak
from brr.loom.runtime.channels.relay import pull_once
from brr.loom.runtime.ledger import inject_letter
from brr.loom.runtime.project import owed
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
        wait_until(lambda: not owed(loom.facts(), "worker"), 3, loom.dump)
        time.sleep(0.3)
        assert starts.read_text().splitlines() == ["start", "start"]
    finally:
        loom.halt()
    assert not loom.errors


def test_persons_letter_gets_two_starts_one_message_and_no_third(tmp_path, monkeypatch):
    root = tmp_path / "home"
    init_self(root, person="ada")
    person = root / "self/people/ada/channels.md"
    person.write_text("telegram:42\n")
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
    sent = []
    monkeypatch.setitem(speak.EFFECTS, "relay", lambda h, c, k, text, context: sent.append((c, text, context)))
    loom = Loom(root)
    loom.start()
    try:
        wait_until(lambda: sent, 10, loom.dump)
        wait_until(lambda: not owed(loom.facts(), "inbox"), 3, loom.dump)
        time.sleep(0.5)
        assert starts.read_text().splitlines() == ["start", "start"]
        assert len(sent) == 1
        channel, text, context = sent[0]
        assert channel == "channel:telegram/42"
        assert "thread inbox" in text and "please help" in text and "person failure tail" in text
        assert context["event_id"] == "crash"
    finally:
        loom.halt()
    assert not loom.errors

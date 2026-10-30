from _step import mail
"""A wasted start reaches the author; only a person's letter gets a second start."""

import json
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from connector_support import drop_once, pull_once
from brr.loom.runtime.ledger import inject_letter

from brr.loom.runtime.selfrepo import init_self

from _step import Loom, kill_bodies, wait_until, write_thread


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
from brr.loom.runtime.home import atomic_write
ctx = Ctx(Path(sys.argv[1]))
own = _own_thread(ctx.wake())
if own == "worker":
    with Path({str(starts)!r}).open("a") as out:
        out.write("start\\n")
    letters = ctx.owed()
    if any(l.body.startswith("first try") for l in letters):
        print("failure log tail", flush=True)
        sys.exit(7)
    for letter in letters:
        ctx.send(letter.reply, "answered the resend", re=letter.id)
    sys.exit(0)
else:
    for letter in ctx.owed():
        ctx.send("thread:author", note="opener", re=letter.id)
    original = ctx.send("thread:worker", "first try", cite=letter.id)
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
                ctx.send("thread:worker", "try again", cite=letter.id)
            elif letter.body == "answered the resend":
                atomic_write(Path({str(received)!r}), json.dumps({{"answer": letter.body, "bounce_seen": True}}))
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
    person = root / "self/people/ada/channels.toml"
    person.write_text('channels = ["relay:telegram:42"]\n')
    write_thread(root, "inbox", "Brnrd", "die-now", wait="0")
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
            assert 'could not take your letter' in text and 'please help' in text
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


class _Person:
    """Ada on telegram:42: each letter is one relay pull."""

    def __init__(self, home):
        self.home, self.cursor = home, 0

    def says(self, event_id, body):
        class Client:
            def pull(_, cursor):
                return {"cursor": cursor + 1, "events": [{"event_id": event_id, "body": body, "attachments": [],
                        "reply_to": {"platform": "telegram", "user_id": 42, "chat_id": 42}}]}
        self.cursor = drop_once(self.home, Client(), self.cursor)


def _parked_home(tmp_path, monkeypatch, *, run="raise SystemExit('must park before starting')", left="2"):
    """A home whose inbox thread is seated on claude/echo with 2% session fuel.

    The claude gauge reads ``fuel``, so a test can refill it. A second shell,
    ``spare``, has a gauge at 70%; the packaged shells are shadowed by folders
    with no gauge, so they are not enabled and no real probe runs.
    """
    from datetime import datetime, timedelta, timezone
    from pathlib import Path
    from brr.loom.runtime import usage
    from brr.loom.runtime.port import parse_frontmatter
    from brr.loom.runtime.selfrepo import git
    root = tmp_path / "home"
    init_self(root, person="ada")
    (root / "self/people/ada/channels.toml").write_text('channels = ["relay:telegram:42"]\n')
    write_thread(root, "inbox", "Brnrd", "answer-fast", wait="0")
    (root / "self/threads/inbox/seat").write_text("body: claude/echo\n")
    git(root / "self", "add", "threads")
    git(root / "self", "commit", "-m", "Choose gauge body.")
    reset = datetime.now(timezone.utc) + timedelta(hours=2)
    fuel = root / "fuel"
    fuel.write_text(left)
    starts = root / "starts"
    for shell, sources in {
        "claude": {"run": run, "usage": "import json, pathlib; print(json.dumps({'windows': [{'name': 'session', "
                   f"'left_pct': float(pathlib.Path({str(fuel)!r}).read_text()), 'resets_at': {reset.isoformat()!r}}}]}}))"},
        "spare": {"run": f"import pathlib, sys, time\nwith pathlib.Path({str(starts)!r}).open('a') as out:\n"
                         "    out.write('spare\\n')\ntime.sleep(30)",
                  "usage": "import json; print(json.dumps({'windows': [{'name': 'session', 'left_pct': 70}]}))"},
        "codex": {"run": "raise SystemExit(1)"}, "grok": {"run": "raise SystemExit(1)"},
        "vibe": {"run": "raise SystemExit(1)"},
    }.items():
        folder = root / "loom/bodies" / shell
        folder.mkdir(parents=True)
        (folder / "jack").write_text("hooks\n")
        for name, source in sources.items():
            script = folder / name
            script.write_text(f"#!{sys.executable}\n{source}\n")
            script.chmod(0o755)
    # The gauge cache outlives a test; the cadence is the same path, faster.
    monkeypatch.setattr(usage, "CACHE_TTL", 0.3)
    monkeypatch.setattr(usage, "PROBE_INTERVAL", 0.1)
    person = _Person(Loom(root).home)
    person.says("park", "please help")
    sent = []
    real_run = subprocess.run
    def run_out(args, **kw):
        if str(args[0]).endswith("/relay/out"):
            sent.append(parse_frontmatter(Path(args[1]).read_text()))
            return subprocess.CompletedProcess(args, 0, '{"ids": []}', '')
        return real_run(args, **kw)
    monkeypatch.setattr(subprocess, "run", run_out)
    return SimpleNamespace(root=root, person=person, sent=sent, fuel=fuel, starts=starts,
                           local_time=reset.astimezone().strftime("%H:%M"))


def _started(loom):
    return [f for f in loom.facts() if f.kind == "body.started"]


def _leases(loom):
    return [(f.data["gen"], f.data["shell"], f.data["core"]) for f in loom.facts() if f.kind == "lease"]


def test_person_is_told_once_when_fuel_parks_their_letter(tmp_path, monkeypatch):
    home = _parked_home(tmp_path, monkeypatch)
    root, sent = home.root, home.sent
    loom = Loom(root)
    loom.start()
    try:
        wait_until(lambda: len(sent) == 1, 10, loom.dump)
        assert sent[0]["to"] == "telegram:42"
        assert (f"Out of claude session fuel (2% left) until about {home.local_time}. "
                "Your letter is kept on thread inbox and is answered when it refills.") in sent[0]["body"]
        assert sent[0]["re"] == "telegram:42:park"
        wait_until(lambda: any(f.kind == "body.parked" for f in loom.facts()), 5, loom.dump)
        time.sleep(0.3)  # several more parked ticks
        assert len(sent) == 1
        assert mail(root, loom.facts(), "inbox")  # the notice does not settle the ask
    finally:
        loom.halt()
    assert not loom.errors


def _start_lines(starts):
    # the fake body opens before it writes: an existing file is not yet a start
    text = starts.read_text() if starts.is_file() else ""
    return text if text.endswith("\n") else ""


def _claude_starts(starts):
    return f"import pathlib, time\nwith pathlib.Path({str(starts)!r}).open('a') as out:\n    out.write('claude\\n')\ntime.sleep(30)"


def test_parked_body_starts_when_its_gauge_refills_before_until(tmp_path, monkeypatch):
    starts = tmp_path / "home/starts"
    home = _parked_home(tmp_path, monkeypatch, run=_claude_starts(starts))
    loom = Loom(home.root)
    loom.start()
    try:
        wait_until(lambda: any(f.kind == "body.parked" for f in loom.facts()), 10, loom.dump)
        time.sleep(0.5)  # parked ticks read the gauge and stay parked
        assert not _started(loom)
        home.fuel.write_text("100")  # the plan was upgraded; the reset is hours away
        wait_until(lambda: _start_lines(starts), 10, loom.dump)
        assert starts.read_text() == "claude\n"
        assert _leases(loom) == [(1, "claude", "echo")]
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors


def test_letter_during_park_is_told_and_wake_starts_the_leased_body(tmp_path, monkeypatch):
    starts = tmp_path / "home/starts"
    home = _parked_home(tmp_path, monkeypatch, run=_claude_starts(starts))
    loom = Loom(home.root)
    loom.start()
    try:
        wait_until(lambda: any(f.kind == "body.parked" for f in loom.facts()), 10, loom.dump)
        wait_until(lambda: (home.root / "loom/usage/spare.json").is_file(), 10, loom.dump)  # spare's gauge has read
        home.person.says("again", "are you there?")
        wait_until(lambda: any(m["re"] == "telegram:42:again" for m in home.sent), 10, loom.dump)
        told = next(m for m in home.sent if m["re"] == "telegram:42:again")
        assert "Out of claude session fuel (2% left)" in told["body"]
        assert "Reply with one of:\nwake: retry claude/echo (session 2%, resets " in told["body"]
        assert "\nwake spare/<core>: spare session 70%" in told["body"]
        assert "codex" not in told["body"]  # a shell with no gauge is not enabled
        time.sleep(0.3)
        assert [m["re"] for m in home.sent] == ["telegram:42:park", "telegram:42:again"]
        home.person.says("wake", "  /WAKE ")
        wait_until(lambda: _start_lines(starts), 10, loom.dump)
        assert starts.read_text() == "claude\n"
        assert _leases(loom) == [(1, "claude", "echo")]
        owed = {str(letter.data["id"]) for letter in mail(home.root, loom.facts(), "inbox")}
        assert {"letter:relay:telegram:42:park", "letter:relay:telegram:42:again"} <= owed
        assert "letter:relay:telegram:42:wake" not in owed  # the word is settled, not handed over as work
        assert not any(m["re"] == "telegram:42:wake" for m in home.sent)
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors


def test_wake_naming_another_body_moves_the_parked_thread_there(tmp_path, monkeypatch):
    home = _parked_home(tmp_path, monkeypatch)
    loom = Loom(home.root)
    loom.start()
    try:
        wait_until(lambda: any(f.kind == "body.parked" for f in loom.facts()), 10, loom.dump)
        home.person.says("move", "wake spare/x")
        wait_until(lambda: _start_lines(home.starts), 10, loom.dump)
        assert home.starts.read_text() == "spare\n"
        assert _leases(loom) == [(1, "claude", "echo"), (2, "spare", "x")]
        ended = [f for f in loom.facts() if f.kind == "attempt.ended"]
        assert [(f.data["gen"], f.data.get("cause")) for f in ended] == [(1, "molted")]
        assert "letter:relay:telegram:42:move" not in {str(l.data["id"]) for l in mail(home.root, loom.facts(), "inbox")}
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors


def test_wake_naming_no_enabled_body_is_refused_with_the_list(tmp_path, monkeypatch):
    home = _parked_home(tmp_path, monkeypatch)
    loom = Loom(home.root)
    loom.start()
    try:
        wait_until(lambda: any(f.kind == "body.parked" for f in loom.facts()), 10, loom.dump)
        home.person.says("nope", "wake codex")
        wait_until(lambda: any(m["re"] == "telegram:42:nope" for m in home.sent), 10, loom.dump)
        refused = next(m for m in home.sent if m["re"] == "telegram:42:nope")
        assert "No body 'codex' here; bodies are claude, spare. Nothing was started." in refused["body"]
        assert "wake spare/<core>: spare session 70%" in refused["body"]
        time.sleep(0.5)
        assert not _started(loom)
        assert _leases(loom) == [(1, "claude", "echo")]
        assert "letter:relay:telegram:42:nope" not in {str(l.data["id"]) for l in mail(home.root, loom.facts(), "inbox")}
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors


def _held_body(log):
    """A claude body that answers what it woke with, then sits in the real stop hook.

    Each turn the hook gives it is a line in ``log``; it leaves when the hook lets it.
    """
    return f"""import pathlib, sys
from brr.loom.runtime.fakebody import Ctx
ctx = Ctx(pathlib.Path(sys.argv[1]))
def say(line):
    with pathlib.Path({str(log)!r}).open("a") as out:
        out.write(line + "\\n")
for letter in ctx.owed():
    ctx.send(letter.reply, "on it", re=letter.id)
say("woke")
while True:
    view = ctx.jack("stop")
    if view.kind != "block":
        say("left")
        break
    if "cache keepalive" not in view.raw:
        say("turn " + " ".join(letter.id for letter in view.letters))
        for letter in view.letters:
            ctx.send(letter.reply, "back", re=letter.id)
"""


def _held_dry(home, loom):
    """Start the inbox thread on claude with fuel, then drain it while the body holds.

    Returns the turn log and its lines before the hold: with fuel, the hook may
    still show the first letter once before the loom has read the answer.
    """
    log = home.root / "turns"
    wait_until(lambda: log.is_file() and "woke" in log.read_text(), 15, loom.dump)
    home.fuel.write_text("2")
    gauge = home.root / "loom/usage/claude.json"
    wait_until(lambda: gauge.is_file() and json.loads(gauge.read_text())["windows"][0]["left_pct"] == 2,
               10, loom.dump)

    def dry():
        return any(json.loads(p.read_text()).get("dry") for p in home.root.rglob("port/jack-state.json"))
    wait_until(dry, 10, loom.dump)  # the stop hook read the same gauge and sleeps
    return log, len(log.read_text().splitlines())


def test_body_held_dry_in_its_stop_hook_tells_the_person_of_each_letter(tmp_path, monkeypatch):
    home = _parked_home(tmp_path, monkeypatch, run=_held_body(tmp_path / "home/turns"), left="100")
    loom = Loom(home.root)
    loom.start()
    try:
        log, before = _held_dry(home, loom)
        wait_until(lambda: (home.root / "loom/usage/spare.json").is_file(), 10, loom.dump)
        home.person.says("again", "are you there?")
        wait_until(lambda: any(m["re"] == "telegram:42:again" for m in home.sent), 10, loom.dump)
        told = next(m for m in home.sent if m["re"] == "telegram:42:again")
        assert "Out of claude session fuel (2% left)" in told["body"]
        assert "Your letter is kept on thread inbox" in told["body"]
        assert ("Reply with one of:\nwake: go on with claude/echo now, the turn may fail on fuel "
                "(session 2%, resets ") in told["body"]
        assert "\nwake spare/<core>: spare session 70%" in told["body"]
        home.person.says("more", "still?")
        wait_until(lambda: any(m["re"] == "telegram:42:more" for m in home.sent), 10, loom.dump)
        time.sleep(0.5)
        # One notice a letter; the first letter was answered before the hold, so none for it.
        assert [m["re"] for m in home.sent if "Out of claude" in m["body"]] == ["telegram:42:again", "telegram:42:more"]
        assert log.read_text().splitlines()[before:] == []  # no turn taken without fuel
        home.person.says("wake", "wake")
        wait_until(lambda: log.read_text().splitlines()[before:], 10, loom.dump)
        assert log.read_text().splitlines()[before].split()[1:] == ["letter:relay:telegram:42:again",
                                                                "letter:relay:telegram:42:more"]
        assert "letter:relay:telegram:42:wake" not in {str(l.data["id"]) for l in mail(home.root, loom.facts(), "inbox")}
        assert _leases(loom) == [(1, "claude", "echo")]
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors


def test_body_held_dry_woken_on_another_body_ends_molted_and_the_next_gen_runs_there(tmp_path, monkeypatch):
    home = _parked_home(tmp_path, monkeypatch, run=_held_body(tmp_path / "home/turns"), left="100")
    loom = Loom(home.root)
    loom.start()
    try:
        log, before = _held_dry(home, loom)
        home.person.says("again", "are you there?")
        wait_until(lambda: any(m["re"] == "telegram:42:again" for m in home.sent), 10, loom.dump)
        home.person.says("move", "wake spare/x")
        wait_until(lambda: _start_lines(home.starts), 15, loom.dump)
        assert home.starts.read_text() == "spare\n"
        assert log.read_text().splitlines()[before:] == ["left"]
        assert _leases(loom) == [(1, "claude", "echo"), (2, "spare", "x")]
        ended = [f for f in loom.facts() if f.kind == "attempt.ended"]
        assert [(f.data["gen"], f.data.get("cause")) for f in ended] == [(1, "molted")]
        owed = {str(l.data["id"]) for l in mail(home.root, loom.facts(), "inbox")}
        assert "letter:relay:telegram:42:again" in owed and "letter:relay:telegram:42:move" not in owed
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors

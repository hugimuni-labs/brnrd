"""An asker's acceptance releases a child; late work climbs and reopening answers."""

import json

from brr.loom.runtime.ledger import inject_letter
from brr.loom.runtime.selfrepo import init_self
from brr.loom.runtime.project import owed

from _step import Loom, wait_until, write_thread
from test_lifecycle import bodies


def test_settle_climb_and_reopen_through_the_live_loop(tmp_path):
    root = tmp_path / "home"
    init_self(root, person="test")
    write_thread(root, "opener", "Opener", "opener", wait="0")
    write_thread(root, "child", "---\nid: child\nstatus: open\ntense: plan\n"
                 "opened-by: thread:opener\n---\n# Child\n\nPlan: original", "child", wait="60")
    ready, ended, climbed, answer = [root / name for name in ("ready", "ended", "climbed", "answer")]
    bodies(root, f'''import json, sys, time, subprocess
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx, _own_thread
from brr.loom.runtime.selfrepo import git
ctx = Ctx(Path(sys.argv[1]))
own = _own_thread(ctx.wake())
def verb(*args):
    proc = subprocess.run([sys.executable, "-m", "brr.loom.runtime", *args,
                           "--room", str(ctx.room / "self" if args[0] == "send-self" else ctx.room)], text=True, capture_output=True)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    return proc
if own == "child":
    letters = ctx.owed()
    reopened = any(l.body == "second work" for l in letters)
    for letter in letters:
        ctx.send("thread:opener", "second answer" if reopened else "first answer", re=letter.id)
    deadline = time.monotonic() + 8
    while ctx.jack("post").letters:
        assert time.monotonic() < deadline
        time.sleep(0.03)
    if reopened:
        sys.exit(0)
    # Keep a plan edit unlanded while the opener changes this README on main.
    path = ctx.room / "self/threads/child/README.md"
    path.write_text(path.read_text().replace("Plan: original", "Plan: completed"))
    git(ctx.room / "self", "add", "threads/child/README.md")
    git(ctx.room / "self", "commit", "-m", "child plan")
    Path({str(ready)!r}).write_text("holding")
    assert ctx.jack("stop").raw == ""
    boundary = ctx.jack("post").raw
    assert "settled: accepted result" in boundary
    assert ctx.jack("post").raw == ""
    verb("send-self")
    Path({str(ended)!r}).write_text(boundary)
    sys.exit(0)
else:
    for letter in ctx.owed():
        ctx.send("thread:opener", note="begin", re=letter.id)
    ctx.send("thread:child", "first work")
    before_close = git(ctx.room / "self", "rev-parse", "HEAD").stdout.strip()
    seen = set()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        for letter in ctx.jack("post").letters:
            if letter.id in seen:
                continue
            seen.add(letter.id)
            if letter.body == "settle now":
                ctx.send("thread:opener", note="accepting", re=letter.id)
                verb("settle", "child", "--why", "accepted result")
            elif "closed thread child climbed here" in letter.body:
                assert "late work" in letter.body
                Path({str(climbed)!r}).write_text(letter.body)
                ctx.send("thread:opener", note="read late work", re=letter.id)
                # A room that predates acceptance must still be able to reopen.
                git(ctx.room / "self", "reset", "--hard", before_close)
                verb("send", "--to", "thread:child", "--reopen", "second work")
            elif letter.body == "second answer":
                ctx.send("thread:opener", note="accepted second answer", re=letter.id)
                Path({str(answer)!r}).write_text(letter.body)
                sys.exit(0)
            else:
                ctx.send("thread:opener", note="read", re=letter.id)
        time.sleep(0.03)
raise SystemExit("no second answer")
''')
    inject_letter(root, to="thread:opener", body="begin")
    loom = Loom(root)
    loom.start()
    try:
        wait_until(ready.exists, 12, loom.dump)
        inject_letter(root, to="thread:opener", body="settle now")
        wait_until(ended.exists, 30, loom.dump)
        assert "settled: accepted result" in ended.read_text()
        readme = root / "self/threads/child/README.md"
        assert "status: settled" in readme.read_text()
        assert "Plan: completed" in readme.read_text()
        assert readme.read_text().count("status:") == 1
        later = inject_letter(root, to="thread:child", body="late work")
        wait_until(answer.exists, 30, loom.dump)
        assert "child" in climbed.read_text() and later in climbed.read_text()
        assert answer.read_text() == "second answer"
        assert "status: open" in readme.read_text()
        wait_until(lambda: not owed(loom.facts(), "child"), 4, loom.dump)
    finally:
        loom.halt()
    assert not loom.errors

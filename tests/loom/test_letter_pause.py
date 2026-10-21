"""A letter landing mid-call pauses the body's tool call; resume lets it finish (4538)."""

import json
import subprocess
import time

from brr.loom.runtime import pausing
from brr.loom.runtime.ledger import inject_letter

from _step import Loom, wait_until, write_thread
from test_lifecycle import bodies


def _stat(pid):
    return subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()


def test_letter_mid_call_pauses_it_and_resume_finishes_it(tmp_path, monkeypatch):
    monkeypatch.setattr(pausing, "MIN_AGE", 0.5)
    root = tmp_path / "home"
    write_thread(root, "work", "Worker", "work", wait="30")
    seen, started, go, finish = (root / name for name in ("seen.json", "started", "go", "finish"))
    work = root / "work.py"
    work.write_text(f'''import time
from pathlib import Path
n = 0
while not Path({str(finish)!r}).exists():
    print("tick", n, flush=True)
    n += 1
    time.sleep(0.05)
print("finished", flush=True)
''')
    # Claude Code's shape: the call is a wrapper shell it waits on, output in a
    # task file it unlinks when the call ends, the end reported as a failure.
    bodies(root, f'''import json, subprocess, sys, time
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx, _own_thread
ctx = Ctx(Path(sys.argv[1]))
own = _own_thread(ctx.wake())
for letter in ctx.owed():
    ctx.send(f"thread:{{own}}", note="on it", re=letter.id)
while ctx.jack("post").letters:
    time.sleep(0.03)
time.sleep(0.2)  # lstart's second would read this call as begun before the boundary
out = ctx.room / "task.output"
with out.open("w") as handle:
    call = subprocess.Popen(["/bin/sh", "-c", f": fake-tool-wrapper; {{sys.executable}} {str(work)!r}; echo done"],
                            stdout=handle, stderr=handle)
Path({str(started)!r}).write_text("1")
code = call.wait()
out.unlink()
view = ctx.jack("post", {{"hook_event_name": "PostToolUseFailure", "tool_name": "Bash", "error": "Exit code 1"}})
for letter in view.letters:
    ctx.send(f"thread:{{own}}", note="read", re=letter.id)
paused = json.loads((ctx.room / "port/.paused.json").read_text())
Path({str(seen)!r}).write_text(json.dumps({{"code": code, "raw": view.raw, "paused": paused,
    "letters": [letter.body for letter in view.letters]}}))
while not Path({str(go)!r}).exists():
    time.sleep(0.05)
resumed = subprocess.run([sys.executable, "-m", "brr.loom.runtime", "resume"], capture_output=True, text=True)
Path({str(go)!r}).write_text(resumed.stdout)
''')
    (root / "loom/bodies/fake/jack").write_text("hooks\n")
    (root / "loom/bodies/fake/pause").write_text("fake-tool-wrapper\n")
    inject_letter(root, to="thread:work", body="run the long check")
    loom = Loom(root)
    loom.start()
    try:
        wait_until(started.exists, 15, loom.dump)
        time.sleep(0.3)
        inject_letter(root, to="thread:work", body="stop: wrong branch")
        wait_until(seen.exists, 15, loom.dump)
        got = json.loads(seen.read_text())
        [record] = got["paused"]
        pid, kept = record["pid"], record["output"]
        assert "work.py" in record["argv"] and kept.endswith(f"/port/paused/{pid}.out")
        assert _stat(pid).startswith("T")
        before = open(kept).read()
        time.sleep(0.3)
        assert open(kept).read() == before and before.startswith("tick 0\n")
        go.write_text("")
        wait_until(lambda: go.read_text(), 10, loom.dump)
        finish.write_text("")
        wait_until(lambda: open(kept).read().endswith("finished\n"), 10, loom.dump)
    finally:
        finish.write_text("")
        loom.halt()
    assert not loom.errors
    # The call returned at once, its wrapper killed; the work was not.
    assert got["code"] == -9
    context = json.loads(got["raw"])["hookSpecificOutput"]
    assert context["hookEventName"] == "PostToolUseFailure"
    assert context["additionalContext"].startswith(f"paused: {record['argv'].split()[0].rsplit('/', 1)[-1]} ")
    assert kept in context["additionalContext"]
    assert got["letters"] == ["stop: wrong branch"]
    assert go.read_text().startswith("resume: ")
    assert len(open(kept).read().splitlines()) > len(before.splitlines())


def test_call_begun_before_the_last_boundary_is_left_running(tmp_path, monkeypatch):
    """A background task returned at once; the boundary after it is the body's, and so is the letter."""
    monkeypatch.setattr(pausing, "MIN_AGE", 0.5)
    root = tmp_path / "home"
    write_thread(root, "work", "Worker", "work", wait="30")
    seen, started = root / "seen.json", root / "started"
    bodies(root, f'''import json, subprocess, sys, time
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx, _own_thread
ctx = Ctx(Path(sys.argv[1]))
own = _own_thread(ctx.wake())
for letter in ctx.owed():
    ctx.send(f"thread:{{own}}", note="on it", re=letter.id)
call = subprocess.Popen(["/bin/sh", "-c", ": fake-tool-wrapper; sleep 30; echo done"])
time.sleep(1.1)
while ctx.jack("post").letters:
    time.sleep(0.03)
Path({str(started)!r}).write_text("1")
deadline = time.monotonic() + 15
while not (view := ctx.jack("post")).letters and time.monotonic() < deadline:
    time.sleep(1.5)  # a long generation: no boundary for a while
alive = call.poll() is None
call.kill()
Path({str(seen)!r}).write_text(json.dumps({{"alive": alive, "letters": [letter.body for letter in view.letters],
    "paused": (ctx.room / "port/.paused.json").exists()}}))
''')
    (root / "loom/bodies/fake/jack").write_text("hooks\n")
    (root / "loom/bodies/fake/pause").write_text("fake-tool-wrapper\n")
    inject_letter(root, to="thread:work", body="start the background job")
    loom = Loom(root)
    loom.start()
    try:
        wait_until(started.exists, 15, loom.dump)
        inject_letter(root, to="thread:work", body="how is it going?")
        wait_until(seen.exists, 20, loom.dump)
    finally:
        loom.halt()
    assert not loom.errors
    assert json.loads(seen.read_text()) == {"alive": True, "letters": ["how is it going?"], "paused": False}


def test_call_while_a_delegate_works_is_left_running(tmp_path, monkeypatch):
    """A delegate's calls are the body's children too, and its failure hook stays silent."""
    monkeypatch.setattr(pausing, "MIN_AGE", 0.5)
    root = tmp_path / "home"
    write_thread(root, "work", "Worker", "work", wait="30")
    seen, started = root / "seen.json", root / "started"
    bodies(root, f'''import json, subprocess, sys, time
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx, _own_thread
ctx = Ctx(Path(sys.argv[1]))
own = _own_thread(ctx.wake())
for letter in ctx.owed():
    ctx.send(f"thread:{{own}}", note="delegated", re=letter.id)
while ctx.jack("post").letters:
    time.sleep(0.03)
ctx.jack("post", {{"agent_id": "d1", "agent_type": "general-purpose", "tool_name": "Bash"}})
time.sleep(1.1)
call = subprocess.Popen(["/bin/sh", "-c", ": fake-tool-wrapper; sleep 30; echo done"])
Path({str(started)!r}).write_text("1")
time.sleep(3)
alive = call.poll() is None
call.kill()
view = ctx.jack("post")
Path({str(seen)!r}).write_text(json.dumps({{"alive": alive, "letters": [letter.body for letter in view.letters],
    "paused": (ctx.room / "port/.paused.json").exists()}}))
''')
    (root / "loom/bodies/fake/jack").write_text("hooks\n")
    (root / "loom/bodies/fake/pause").write_text("fake-tool-wrapper\n")
    inject_letter(root, to="thread:work", body="hand it to a delegate")
    loom = Loom(root)
    loom.start()
    try:
        wait_until(started.exists, 15, loom.dump)
        inject_letter(root, to="thread:work", body="any news?")
        wait_until(seen.exists, 20, loom.dump)
    finally:
        loom.halt()
    assert not loom.errors
    assert json.loads(seen.read_text()) == {"alive": True, "letters": ["any news?"], "paused": False}

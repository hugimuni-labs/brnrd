"""A settled thread's body is gone even when it never reaches another boundary."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from brr.daemon2.facts import Fact
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import append, inject_letter, read_facts
from brr.loom.runtime.selfrepo import init_self

from _step import Loom, kill_bodies, wait_until, write_thread
from test_lifecycle import bodies
from test_router import Proc, _config, _dump, _stop


# Shorter than the production default so the assertion window stays inside
# grace + 2s. The default stays long enough for a body that does leave.
GRACE = 0.5


def _home(tmp_path: Path) -> Path:
    root = tmp_path / "home"
    init_self(root, person="test")
    write_thread(root, "opener", "Opener", "hold", wait="0")
    write_thread(
        root, "child",
        "---\nid: child\nstatus: open\ntense: plan\n"
        "opened-by: thread:opener\n---\n# Child\n",
        "hold", wait="0",
    )
    _config(root, stop_grace=GRACE)
    bodies(root, _script(root))
    inject_letter(root, to="thread:opener", body="begin")
    inject_letter(root, to="thread:child", body="work")
    return root


def _script(root: Path) -> str:
    """The child sleeps forever. The opener settles it through the verb's argv."""
    pid_path = str(root / "child.pid")
    go_path = str(root / "go")
    out_path = str(root / "settle-out")
    return f"""import os, subprocess, sys, time
from pathlib import Path
from brr.loom.runtime.fakebody import Ctx, _own_thread
ctx = Ctx(Path(sys.argv[1]))
own = _own_thread(ctx.wake())
if own == "child":
    (ctx.room / "self" / "left-behind.md").write_text("kept\\n")
    Path({pid_path!r}).write_text(str(os.getpid()))
    while True:
        time.sleep(0.2)
deadline = time.monotonic() + 30
while not Path({go_path!r}).exists():
    if time.monotonic() > deadline:
        raise SystemExit("opener never got go")
    time.sleep(0.05)
proc = subprocess.run(
    [sys.executable, "-m", "brr.loom.runtime", "settle", "child",
     "--why", "stop the worker", "--room", str(ctx.room)],
    text=True, capture_output=True,
)
Path({out_path!r}).write_text((proc.stdout or "") + (proc.stderr or "") + "\\nrc=%s\\n" % proc.returncode)
while True:
    time.sleep(0.2)
"""


def _facts(root: Path):
    return read_facts(Home(root))


def _running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _child_pid(root: Path) -> int | None:
    path = root / "child.pid"
    if not path.is_file():
        return None
    text = path.read_text().strip()
    if not text.isdigit():
        return None
    return int(text)


def _child_starts(facts) -> list:
    strands = {fact.data["strand"] for fact in facts
               if fact.kind == "lease" and fact.data.get("thread") == "child"}
    return [fact for fact in facts if fact.kind == "body.started" and fact.data.get("strand") in strands]


def _ready(root: Path, facts) -> int | None:
    pid = _child_pid(root)
    if pid is None or not _running(pid):
        return None
    if any(fact.kind == "body.started" and int(fact.data.get("pid") or 0) == pid for fact in facts):
        return pid
    return None


def _threads_now(root: Path, thread: str) -> str:
    proc = subprocess.run(
        [sys.executable, "-m", "brr.loom.runtime", "threads", "--home", str(root)],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)
    for line in proc.stdout.splitlines():
        cells = [part.strip() for part in line.split(" · ")]
        if cells and cells[0] == thread:
            return cells[2]
    raise AssertionError(proc.stdout)


def _assert_stopped(root: Path, pid: int, facts) -> None:
    ended = [fact for fact in facts if fact.kind == "attempt.ended" and fact.data.get("thread") == "child"]
    assert len(ended) == 1, ended
    assert ended[0].data.get("cause") == "stopped"
    assert ended[0].data["evidence"].get("why") == "stop the worker"
    assert any("left-behind.md" in line for line in ended[0].data["evidence"].get("kept", []))
    # The opener's own start is not a replacement. The child keeps the one body.
    starts = [fact for fact in _child_starts(facts) if ":foreign:" not in fact.id]
    assert len(starts) == 1, starts
    assert int(starts[0].data["pid"]) == pid
    assert not _running(pid)
    assert _threads_now(root, "child") == "stopped"
    room = root / "rooms" / starts[0].data["strand"]
    assert room.is_dir()
    assert (room / "self" / "left-behind.md").is_file()


def test_settled_sleeping_body_is_stopped(tmp_path):
    """An opener settles a child stuck in sleep: the process is gone, the room stays."""
    root = _home(tmp_path)
    loom = Loom(root)
    loom.start()
    try:
        wait_until(lambda: _ready(root, loom.facts()), 12, loom.dump)
        pid = _child_pid(root)
        (root / "go").write_text("go")
        wait_until((root / "settle-out").exists, 12, loom.dump)
        assert "rc=0" in (root / "settle-out").read_text(), (root / "settle-out").read_text()

        def stopped():
            if loom.errors:
                raise AssertionError(loom.dump())
            facts = loom.facts()
            marked = any(fact.kind == "body.stopped" and int(fact.data.get("pid") or 0) == pid for fact in facts)
            ended = any(
                fact.kind == "attempt.ended" and fact.data.get("thread") == "child"
                and fact.data.get("cause") == "stopped" for fact in facts
            )
            return (not _running(pid)) and marked and ended

        wait_until(stopped, GRACE + 2, loom.dump)
        _assert_stopped(root, pid, loom.facts())
    finally:
        loom.halt()
        kill_bodies(loom)
    assert not loom.errors


def test_adopted_body_is_stopped_after_the_loop_restarts(tmp_path):
    """The loop dies and another starts. The body, now reparented, is still stopped."""
    root = _home(tmp_path)
    procs: list[Proc] = []
    try:
        first = Proc(root, "aaaa")
        procs.append(first)
        wait_until(lambda: _ready(root, _facts(root)), 12, lambda: _dump(root, procs))
        pid = _child_pid(root)
        assert pid is not None
        os.kill(first.proc.pid, signal.SIGTERM)
        first.proc.wait(timeout=5)
        assert _running(pid)
        ppid = int(subprocess.check_output(["ps", "-o", "ppid=", "-p", str(pid)], text=True).strip())
        assert ppid == 1, ppid
        second = Proc(root, "aaaa")
        procs.append(second)
        (root / "go").write_text("go")
        wait_until((root / "settle-out").exists, 12, lambda: _dump(root, procs))
        assert "rc=0" in (root / "settle-out").read_text(), (root / "settle-out").read_text()

        def stopped():
            if second.proc.poll() not in (None, 0):
                raise AssertionError(_dump(root, procs))
            facts = _facts(root)
            marked = any(fact.kind == "body.stopped" and int(fact.data.get("pid") or 0) == pid for fact in facts)
            ended = any(
                fact.kind == "attempt.ended" and fact.data.get("thread") == "child"
                and fact.data.get("cause") == "stopped" for fact in facts
            )
            return (not _running(pid)) and marked and ended

        wait_until(stopped, GRACE + 2, lambda: _dump(root, procs))
        _assert_stopped(root, pid, _facts(root))
    finally:
        _stop(root, procs)


def test_a_recorded_pid_for_a_different_process_is_not_signalled(tmp_path):
    """A pid written over the body's own fact is named, and both processes stay."""
    root = _home(tmp_path)
    sleeper = subprocess.Popen(["sleep", "60"], start_new_session=True)
    loom = Loom(root)
    loom.start()
    try:
        wait_until(lambda: _ready(root, loom.facts()), 12, loom.dump)
        pid = _child_pid(root)
        assert pid is not None
        real = next(fact for fact in loom.facts() if fact.kind == "body.started" and int(fact.data["pid"]) == pid)
        foreign = f"body.started:{real.data['strand']}:foreign:{sleeper.pid}"
        append(Home(root), Fact(
            kind="body.started", by=real.by,
            data={**real.data, "pid": sleeper.pid},
            id=foreign,
        ))
        (root / "go").write_text("go")
        wait_until((root / "settle-out").exists, 12, loom.dump)
        assert "rc=0" in (root / "settle-out").read_text(), (root / "settle-out").read_text()

        def refused():
            if sleeper.poll() is not None:
                raise AssertionError(f"sleeper died rc={sleeper.returncode}\n{loom.dump()}")
            if not _running(pid):
                raise AssertionError(f"real body died\n{loom.dump()}")
            return any(
                fact.kind == "body.unidentified" and int(fact.data.get("pid") or -1) == sleeper.pid
                for fact in loom.facts()
            )

        wait_until(refused, GRACE + 2, loom.dump)
        time.sleep(0.3)
        assert sleeper.poll() is None
        assert _running(pid)
        facts = loom.facts()
        assert not any(fact.kind == "body.stopped" for fact in facts)
        child_starts = _child_starts(facts)
        assert {fact.id for fact in child_starts} == {real.id, foreign}
        assert _threads_now(root, "child") == f"unidentified {sleeper.pid}"
        strand = real.data["strand"]
        assert (root / "rooms" / strand).is_dir()
        assert (root / "rooms" / strand / "self" / "left-behind.md").is_file()
    finally:
        loom.halt()
        if sleeper.poll() is None:
            os.kill(sleeper.pid, signal.SIGKILL)
            sleeper.wait(timeout=2)
        kill_bodies(loom)
    assert not loom.errors


def test_a_command_line_naming_the_room_is_not_proof_without_a_start_time(tmp_path):
    """No ``lstart`` on record: only the process holding ``.body`` is the body."""
    import fcntl
    from brr.loom.runtime.loom import _proven_body

    room = tmp_path / "room"
    room.mkdir()
    lock = os.open(room / ".body", os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    argv = [sys.executable, "-c", "import time; time.sleep(60)", str(room)]
    bystander = subprocess.Popen(argv, start_new_session=True)
    holder = subprocess.Popen(argv, start_new_session=True, pass_fds=(lock,))
    try:
        time.sleep(0.3)
        assert not _proven_body(room, bystander.pid, "", None)
        assert _proven_body(room, holder.pid, "", None)
        assert not _proven_body(room, holder.pid, "Thu Jan  1 00:00:00 1970", None)
    finally:
        for proc in (bystander, holder):
            proc.kill()
            proc.wait()
        os.close(lock)

"""Two loom loops on one home, one letter.

Measured before the flock in ``loom.run``: both processes share the home's
install id, so each renews the other's router lease and neither stands
down. One letter started a new body every generation, and one loop deleted
the other's port file during ingest. The flock on ``loom/loop.lock`` is
what this assertion now depends on. ``answer-fast`` settles with a note.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import inject_letter, read_facts
from brr.loom.runtime.loom import body_alive

from _step import write_thread


_LOOP = textwrap.dedent(
    """\
    import sys
    import threading
    import time
    from pathlib import Path

    from brr.loom.runtime.loom import run

    root = Path(sys.argv[1])
    stop_file = Path(sys.argv[2])
    # Read back which tree the subprocess tests; the working directory
    # alone is not an import pin in a linked checkout.
    import brr.loom.runtime.loom as loom
    assert Path(loom.__file__).resolve() == Path(sys.argv[3]).resolve()
    stop = threading.Event()

    def watch() -> None:
        while not stop_file.exists():
            time.sleep(0.02)
        stop.set()

    threading.Thread(target=watch, daemon=True).start()
    run(root, adapter="fake", core="haiku", tick=0.05, stop=stop)
    """
)


def _src() -> str:
    return str(Path(__file__).resolve().parents[2] / "src")


def test_two_loops_on_one_home_answer_one_letter(tmp_path: Path) -> None:
    root = tmp_path / "home"
    write_thread(root, "work", "Work", "answer-fast", wait="0")
    letter = inject_letter(root, to="thread:work", body="hello from outside")
    stop = tmp_path / "stop"
    script = tmp_path / "loop.py"
    script.write_text(_LOOP)
    env = os.environ.copy()
    env["PYTHONPATH"] = _src() + os.pathsep + env.get("PYTHONPATH", "")
    logs = []
    procs = []
    for index in (1, 2):
        log = tmp_path / f"loop-{index}.log"
        logs.append(log)
        procs.append(subprocess.Popen(
            [sys.executable, str(script), str(root), str(stop),
             str(Path(_src()) / "brr/loom/runtime/loom.py")],
            env=env,
            stdout=log.open("w"),
            stderr=subprocess.STDOUT,
        ))
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            facts = read_facts(Home(root))
            replies = [
                fact for fact in facts
                if fact.data.get("re") == letter and fact.kind in {"letter", "note"}
            ]
            if replies:
                time.sleep(1.5)
                break
            time.sleep(0.05)
        else:
            tails = []
            for log in logs:
                text = log.read_text(errors="replace") if log.exists() else ""
                tails.append(text[-1500:])
            raise AssertionError("no reply in 20s\n" + "\n---\n".join(tails))
        facts = read_facts(Home(root))
    finally:
        stop.write_text("stop\n")
        for proc in procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        home = Home(root)
        for fact in read_facts(home):
            if fact.kind != "body.started":
                continue
            try:
                if body_alive(home.room(fact.data["strand"])):
                    os.killpg(fact.data["pid"], signal.SIGKILL)
            except (ProcessLookupError, PermissionError, KeyError, OSError):
                pass

    started = [fact for fact in facts if fact.kind == "body.started"]
    replies = [
        fact for fact in facts
        if fact.data.get("re") == letter and fact.kind in {"letter", "note"}
    ]
    kinds = sorted({fact.kind for fact in facts})
    detail = f"bodies {len(started)} replies {len(replies)} kinds {kinds}"
    assert len(started) == 1, detail
    assert len(replies) == 1, detail
    assert replies[0].kind == "note", detail


def _lock_held(root: Path) -> bool:
    import fcntl
    path = root / "loom" / "loop.lock"
    if not path.exists():
        return False
    with path.open("r") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
    return False


def _cli_loop(root: Path, log: Path):
    """Drive the real foreground verb, including its SIGTERM handler."""
    env = os.environ.copy()
    env["PYTHONPATH"] = _src() + os.pathsep + env.get("PYTHONPATH", "")
    code = (
        "from pathlib import Path; import sys; "
        "import brr.loom.runtime.loom as loom; "
        "assert Path(loom.__file__).resolve() == Path(sys.argv[2]).resolve(); "
        "from brr.loom.runtime.__main__ import main; "
        "sys.exit(main(['loom', '--home', sys.argv[1], '--adapter', 'fake', '--tick', '0.05']))"
    )
    with log.open("w") as handle:
        return subprocess.Popen(
            [sys.executable, "-c", code, str(root), str(Path(_src()) / "brr/loom/runtime/loom.py")],
            env=env, stdout=handle, stderr=subprocess.STDOUT,
        )


def _until(condition, log: Path, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError(log.read_text(errors="replace") if log.exists() else "loop did not start")


def _end(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)


def test_a_waiting_loop_stops_without_stopping_the_holder_and_crash_releases_lock(tmp_path):
    root = tmp_path / "home"
    write_thread(root, "work", "Work", "answer-fast", wait="0")
    owner_log, waiter_log = tmp_path / "owner.log", tmp_path / "waiter.log"
    owner = _cli_loop(root, owner_log)
    waiter = successor = None
    try:
        _until(lambda: _lock_held(root), owner_log)
        waiter = _cli_loop(root, waiter_log)
        _until(lambda: "loop: waiting" in waiter_log.read_text(), waiter_log)
        waiter.terminate()
        assert waiter.wait(timeout=2) == 0
        assert owner.poll() is None
        assert _lock_held(root)
        owner.kill()
        owner.wait(timeout=2)
        assert not _lock_held(root)
        successor = _cli_loop(root, owner_log)
        _until(lambda: _lock_held(root), owner_log)
        assert successor.poll() is None
    finally:
        for process in (owner, waiter, successor):
            if process is not None:
                _end(process)


def test_waiting_loop_takes_over_after_hand_started_loop_stops(tmp_path):
    root = tmp_path / "home"
    write_thread(root, "work", "Work", "answer-fast", wait="0")
    owner_log, waiter_log = tmp_path / "owner.log", tmp_path / "waiter.log"
    owner = _cli_loop(root, owner_log)
    waiter = None
    try:
        _until(lambda: _lock_held(root), owner_log)
        waiter = _cli_loop(root, waiter_log)
        _until(lambda: "loop: waiting" in waiter_log.read_text(), waiter_log)
        owner.terminate()
        assert owner.wait(timeout=2) == 0
        letter = inject_letter(root, to="thread:work", body="after hand-started loom stops")
        _until(lambda: any(f.kind == "note" and f.data.get("re") == letter
                          for f in read_facts(Home(root))), waiter_log)
        facts = read_facts(Home(root))
        assert len([f for f in facts if f.kind == "body.started"]) == 1
    finally:
        for process in (owner, waiter):
            if process is not None:
                _end(process)
        for fact in read_facts(Home(root)):
            if fact.kind == "body.started" and body_alive(Home(root).room(fact.data["strand"])):
                try:
                    os.killpg(fact.data["pid"], signal.SIGKILL)
                except ProcessLookupError:
                    pass

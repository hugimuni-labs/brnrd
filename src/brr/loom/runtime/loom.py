"""One process, one loop: ingest, route, rewrite boundaries, reap."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from brr.daemon2.facts import Fact

from .adapters import claude_argv, fake_argv, wait_seconds
from .home import Home, atomic_write, mint, thread_of
from .ledger import LedgerConflict, append, read_facts
from .port import fact_from_port, parse_frontmatter, render_boundary, render_wake
from .project import fold, generation, holder, owed, sender_threads

SRC = str(Path(__file__).resolve().parents[3])
FUSE_WINDOW_S = 600
FUSE_DEATHS = 2
_GIT_PIN = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")


@dataclass
class Body:
    strand: str
    thread: str
    gen: int
    proc: subprocess.Popen
    started_hlc: tuple
    log: object

    def close_log(self) -> None:
        close = getattr(self.log, "close", None)
        if close is not None:
            close()


def _log(home: Home, message: str) -> None:
    path = home.root / "loom" / "loom.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now(timezone.utc).isoformat()} {message}\n"
    with path.open("a") as handle:
        handle.write(line)
    sys.stderr.write(line)


def _env(room: Path) -> dict[str, str]:
    env = os.environ.copy()
    for name in _GIT_PIN:
        env.pop(name, None)
    parts = [part for part in env.get("PYTHONPATH", "").split(os.pathsep) if part]
    if SRC not in parts:
        parts.insert(0, SRC)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env["BRNRD_ROOM"] = str(room)
    return env


def _by(home: Home) -> str:
    return f"loom:{home.install_id()}"


def _attention(home: Home, fact_id: str, why: str, *,
               thread: str | None = None, letter: str | None = None) -> None:
    data: dict = {"why": why}
    if thread is not None:
        data["thread"] = thread
    if letter is not None:
        data["letter"] = letter
    try:
        append(home, Fact(kind="attention", by=_by(home), data=data, id=fact_id))
    except LedgerConflict:
        _log(home, f"attention {fact_id} already recorded with different content")


def _record(home: Home, kind: str, data: dict, fact_id: str) -> Fact:
    return append(home, Fact(kind=kind, by=_by(home), data=data, id=fact_id))


def _next_gen(facts: list[Fact], thread: str) -> int:
    gen = 0
    for fact in facts:
        if fact.kind in {"lease", "released"} and str(fact.data.get("thread")) == thread:
            gen = max(gen, int(fact.data["gen"]))
    return gen + 1


def _policy(home: Home, thread: str) -> str | None:
    path = home.thread_dir(thread) / "policy"
    if not path.is_file():
        return None
    name = path.read_text().strip()
    return name or None


def _recent_deaths(facts: list[Fact], strand: str) -> int:
    now = datetime.now(timezone.utc)
    count = 0
    for fact in facts:
        if fact.kind != "body.died" or fact.data.get("strand") != strand:
            continue
        at = datetime.fromisoformat(fact.at)
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if (now - at).total_seconds() <= FUSE_WINDOW_S:
            count += 1
    return count


def _fused(home: Home, facts: list[Fact], strand: str, thread: str) -> bool:
    if _recent_deaths(facts, strand) < FUSE_DEATHS:
        return False
    _attention(
        home, f"attention:fuse:{strand}",
        f"fuse: {strand} died twice within 10 minutes", thread=thread,
    )
    _log(home, f"fuse {strand} thread {thread}")
    return True


def _prepare(home: Home, strand: str, thread: str, gen: int,
             letters: list[Fact], threads: dict[str, str]) -> Path:
    room = home.room(strand)
    (room / "port" / "in").mkdir(parents=True, exist_ok=True)
    (room / "port" / "out").mkdir(parents=True, exist_ok=True)
    wait_src = home.thread_dir(thread) / "wait"
    if wait_src.is_file():
        atomic_write(room / "port" / "wait", wait_src.read_text().strip() + "\n")
    for name in ("jack-state.json", "molt-pending"):
        (room / "port" / name).unlink(missing_ok=True)
    readme = (home.thread_dir(thread) / "README.md").read_text()
    atomic_write(room / "port" / "wake.md",
                 render_wake(readme, strand, gen, thread, letters, threads))
    shown = fold(read_facts(home)).shown.get(strand, set())
    atomic_write(
        room / "port" / "in" / "boundary.md",
        render_boundary(strand, gen, thread, letters, shown, threads),
    )
    return room


def _spawn(room: Path, argv: list[str]) -> tuple[subprocess.Popen, object]:
    log = open(room / "port" / "body.log", "ab")
    try:
        proc = subprocess.Popen(
            argv, cwd=room, env=_env(room), stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    except Exception:
        log.close()
        raise
    return proc, log


def _start(home: Home, strand: str, thread: str, gen: int, adapter: str,
           core: str, bodies: dict[str, Body]) -> bool:
    """Launch one body. Two launch failures inside the fuse window stop there."""
    for _attempt in range(FUSE_DEATHS):
        facts = read_facts(home)
        if strand in bodies:
            return True
        letters = owed(facts, thread)
        room = _prepare(home, strand, thread, gen, letters, sender_threads(facts))
        if adapter == "fake":
            policy = _policy(home, thread)
            if policy is None:
                _attention(home, f"attention:no-policy:{thread}",
                           f"fake adapter: thread {thread} has no policy file",
                           thread=thread)
                return False
            argv = fake_argv(room, policy)
        elif adapter == "claude":
            argv = claude_argv(room, core, wait_seconds(room))
        else:
            raise ValueError(f"unknown adapter {adapter!r}")
        try:
            proc, log = _spawn(room, argv)
        except OSError as exc:
            _log(home, f"launch failed {strand}: {exc}")
            _record(home, "body.died",
                    {"strand": strand, "gen": gen, "code": 127},
                    f"body.died:{strand}:{gen}:launch:{mint(6)}")
            if _fused(home, read_facts(home), strand, thread):
                return False
            continue
        started = _record(
            home, "body.started",
            {"strand": strand, "gen": gen, "pid": proc.pid, "adapter": adapter},
            f"body.started:{strand}:{gen}:{proc.pid}:{mint(6)}",
        )
        bodies[strand] = Body(
            strand, thread, gen, proc, tuple(started.hlc or ()), log,
        )
        _log(home, f"start {strand} thread {thread} gen {gen} pid {proc.pid} {adapter}")
        return True
    return False


def _threads(home: Home) -> list[str]:
    root = home.root / "self" / "threads"
    if not root.is_dir():
        return []
    return sorted(
        path.name for path in root.iterdir() if (path / "README.md").is_file()
    )


def _reject(home: Home, room: Path, path: Path, message: str) -> None:
    rejected = room / "port" / "rejected"
    rejected.mkdir(parents=True, exist_ok=True)
    os.replace(path, rejected / path.name)
    _attention(home, f"attention:reject:{path.stem}", why=message)
    _log(home, f"rejected {path.name}: {message}")


def _ingest(home: Home) -> None:
    rooms = home.root / "rooms"
    if not rooms.is_dir():
        return
    for room in sorted(path for path in rooms.iterdir() if path.is_dir()):
        out = room / "port" / "out"
        if not out.is_dir():
            continue
        strand = room.name
        for path in sorted(out.glob("*.md")):
            try:
                fact = fact_from_port(
                    parse_frontmatter(path.read_text()), strand,
                    generation(read_facts(home), strand),
                )
                append(home, fact)
            except Exception as exc:
                _reject(home, room, path, f"{type(exc).__name__}: {exc}")
                continue
            path.unlink()


def _route(home: Home, bodies: dict[str, Body], adapter: str, core: str) -> None:
    threads = _threads(home)
    for thread in threads:
        facts = read_facts(home)
        if holder(facts, thread) or any(body.thread == thread for body in bodies.values()):
            continue
        if not owed(facts, thread):
            continue
        if adapter == "fake" and _policy(home, thread) is None:
            _attention(home, f"attention:no-policy:{thread}",
                       f"fake adapter: thread {thread} has no policy file",
                       thread=thread)
            continue
        strand = f"s-{home.install_id()}-{mint(6)}"
        gen = _next_gen(facts, thread)
        _record(home, "lease",
                {"thread": thread, "strand": strand, "gen": gen},
                f"lease:{thread}:{gen}")
        _log(home, f"lease {strand} thread {thread} gen {gen}")
        _start(home, strand, thread, gen, adapter, core, bodies)
    facts = read_facts(home)
    state = fold(facts)
    known = set(threads)
    for fact in state.accepted:
        if fact.kind != "letter" or fact.data.get("id") in state.handled:
            continue
        letter_id = str(fact.data.get("id") or fact.id)
        try:
            thread = thread_of(str(fact.data.get("to")))
        except ValueError:
            _attention(home, f"attention:unroutable:{letter_id}",
                       f"unroutable to {fact.data.get('to')!r}", letter=letter_id)
            continue
        if thread not in known:
            _attention(home, f"attention:no-thread:{letter_id}",
                       f"no such thread {thread}", thread=thread, letter=letter_id)


def _boundaries(home: Home, bodies: dict[str, Body]) -> None:
    facts = read_facts(home)
    state = fold(facts)
    threads = sender_threads(facts)
    for body in bodies.values():
        letters = [
            fact for fact in state.accepted
            if fact.kind == "letter" and fact.data.get("id") not in state.handled
            and _destination(fact) == body.thread
        ]
        text = render_boundary(
            body.strand, body.gen, body.thread, letters,
            state.shown.get(body.strand, set()), threads,
        )
        path = home.room(body.strand) / "port" / "in" / "boundary.md"
        if not path.is_file() or path.read_text() != text:
            atomic_write(path, text)


def _destination(fact: Fact) -> str | None:
    try:
        return thread_of(str(fact.data.get("to")))
    except ValueError:
        return None


def _molted(home: Home, body: Body, facts: list[Fact]) -> bool:
    if (home.room(body.strand) / "port" / "molt-pending").is_file():
        return True
    for fact in facts:
        if fact.kind != "molt":
            continue
        if fact.data.get("strand") != body.strand or int(fact.data.get("gen", -1)) != body.gen:
            continue
        if fact.hlc and body.started_hlc and tuple(fact.hlc) > tuple(body.started_hlc):
            return True
    return False


def _reap(home: Home, bodies: dict[str, Body], adapter: str, core: str) -> None:
    for strand, body in list(bodies.items()):
        code = body.proc.poll()
        if code is None:
            continue
        body.close_log()
        del bodies[strand]
        facts_before = read_facts(home)
        molted = _molted(home, body, facts_before)
        kind = "body.exited" if code == 0 else "body.died"
        _record(home, kind, {"strand": strand, "gen": body.gen, "code": code},
                f"{kind}:{strand}:{body.gen}:{mint(6)}")
        _log(home, f"exit {strand} code {code}")
        facts = read_facts(home)
        if code != 0:
            if not _fused(home, facts, strand, body.thread):
                _start(home, strand, body.thread, body.gen, adapter, core, bodies)
            continue
        if molted or owed(facts, body.thread):
            _start(home, strand, body.thread, body.gen, adapter, core, bodies)
            continue
        _record(home, "released", {
            "thread": body.thread, "strand": strand, "gen": body.gen,
            "why": "nothing owed",
        }, f"released:{body.thread}:{body.gen}")
        _log(home, f"released {strand} thread {body.thread}")


def tick_once(home: Home, bodies: dict[str, Body], adapter: str, core: str) -> None:
    _ingest(home)
    _route(home, bodies, adapter, core)
    _boundaries(home, bodies)
    _reap(home, bodies, adapter, core)


def _pause(stop, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while True:
        if stop is not None and stop.is_set():
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.02, remaining))


def _shutdown(bodies: dict[str, Body]) -> None:
    for body in bodies.values():
        if body.proc.poll() is None:
            try:
                os.killpg(body.proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    time.sleep(0.05)
    for body in bodies.values():
        if body.proc.poll() is None:
            try:
                os.killpg(body.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        body.close_log()


def _own_stop(stop):
    """SIGTERM ends the loop. Bodies are in their own sessions, so a kill must reap them."""
    if stop is not None:
        return stop
    stop = threading.Event()
    if threading.current_thread() is threading.main_thread():
        def _stop(_signum, _frame, event=stop):
            event.set()
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
    return stop


def run(root: Path | str, *, adapter: str = "fake", core: str = "haiku",
        tick: float = 0.2, stop=None) -> None:
    if adapter not in {"fake", "claude"}:
        raise ValueError(f"unknown adapter {adapter!r}")
    if tick <= 0:
        raise ValueError("tick must be positive")
    stop = _own_stop(stop)
    home = Home(root)
    home.install_id()
    bodies: dict[str, Body] = {}
    try:
        while not (stop is not None and stop.is_set()):
            try:
                tick_once(home, bodies, adapter, core)
            except Exception:
                _log(home, traceback.format_exc().rstrip())
            if _pause(stop, tick):
                break
    finally:
        _shutdown(bodies)

"""One process, one loop: ingest, route, rewrite boundaries, reap."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import tempfile
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from brr.daemon2.facts import Fact

from .adapters import claude_argv, fake_argv, wait_seconds, walled
from .attention import actionable
from .home import Home, atomic_write, is_channel, mint
from .ledger import LedgerConflict, append, read_facts
from .port import (
    fact_from_port, fact_from_taint, parse_frontmatter, render_boundary,
)
from .project import fold, generation, holder, sender_threads
from .selfrepo import (
    AUTHOR_EMAIL, AUTHOR_NAME, SelfError, _env_without_pin, room as clone_room, run_wake,
)

SRC = str(Path(__file__).resolve().parents[3])
FUSE_WINDOW_S = 600
FUSE_DEATHS = 2
# A body that ended on a provider limit is started again after this long.
# The reset time in the provider's text is not parsed: a start against a
# spent window is refused before it costs anything, so the retry is the probe.
WALL_WAIT_S = 900.0
# Only the end of a body's output is read for the limit line, so a crash
# that merely mentions a limit earlier on still counts toward the fuse.
WALL_TAIL = 400
# Held by the body process, not the loom: the wrapper flocks, then execs.
# preexec_fn after fork from the test's loom thread can deadlock on a lock
# another thread still owns. The fd stays open across exec, so the lock
# dies with the body.
_FLOCK = (
    "import fcntl, os, sys\n"
    "path, argv = sys.argv[1], sys.argv[2:]\n"
    "os.makedirs(os.path.dirname(path) or '.', exist_ok=True)\n"
    "fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)\n"
    # Python opens fds non-inheritable. Without this the lock vanishes at
    # exec and a peer treats a live body as gone.
    "os.set_inheritable(fd, True)\n"
    "fcntl.flock(fd, fcntl.LOCK_EX)\n"
    # execvp, not execv: claude is a PATH name. execv only takes a file path,
    # so a haiku body died at the wrapper before main.
    "os.execvp(argv[0], argv)\n"
)


@dataclass
class Body:
    strand: str
    thread: str
    gen: int
    proc: subprocess.Popen
    log: object
    log_start: int = 0

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
    env = _env_without_pin()
    parts = [part for part in env.get("PYTHONPATH", "").split(os.pathsep) if part]
    if SRC not in parts:
        parts.insert(0, SRC)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    # Human enrollment is for hand commits; a body must not inherit it.
    for role in ("AUTHOR", "COMMITTER"):
        env[f"GIT_{role}_NAME"] = AUTHOR_NAME
        env[f"GIT_{role}_EMAIL"] = AUTHOR_EMAIL
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
        # An exit 0 that left letters owed (not a molt) is a failed attempt
        # too: a body that quits at once with code 0 would otherwise respawn
        # forever, the crash loop the fuse exists for.
        unfinished = fact.kind == "body.exited" and fact.data.get("unfinished")
        if (fact.kind != "body.died" and not unfinished) or fact.data.get("strand") != strand:
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
        f"fuse: {strand} died or quit unfinished twice within 10 minutes", thread=thread,
    )
    _log(home, f"fuse {strand} thread {thread}")
    return True


def _prepare(home: Home, strand: str, thread: str, gen: int,
             letters: list[Fact], threads: dict[str, str],
             facts: list[Fact]) -> Path:
    room = home.room(strand).resolve()
    clone = clone_room(home.root, strand)
    (room / "port" / "in").mkdir(parents=True, exist_ok=True)
    (room / "port" / "out").mkdir(parents=True, exist_ok=True)
    wait_src = home.thread_dir(thread) / "wait"
    if wait_src.is_file():
        atomic_write(room / "port" / "wait", wait_src.read_text().strip() + "\n")
    for name in ("jack-state.json", "molt-pending"):
        (room / "port" / name).unlink(missing_ok=True)
    owed_path = room / "port" / "owed.json"
    atomic_write(owed_path, json.dumps([letter.data for letter in letters]))
    part_path = room / "port" / "loom.md"
    atomic_write(part_path, (
        f"you are strand {strand} on thread {thread}; letters arrive at your "
        "tool boundaries; answer with `python -m brr.loom.runtime send --re <id> "
        "--to thread:<from-thread> \"…\"`; commit in this self clone and land "
        "your branch with `python -m brr.loom.runtime send-self --room .`; "
        "when you're done, stop, and the jack holds you while letters may come\n"
    ))
    with tempfile.TemporaryFile(mode="w+") as wake, tempfile.TemporaryFile(mode="w+") as why:
        code = run_wake(clone, thread, owed_path, loom_part=part_path, stdout=wake, stderr=why)
        why.seek(0)
        if code != 0:
            raise SelfError(f"wake exited {code}: {why.read().strip()}")
        wake.seek(0)
        atomic_write(room / "port" / "wake.md", wake.read())
    shown = fold(facts).shown.get(strand, set())
    atomic_write(
        room / "port" / "in" / "boundary.md",
        render_boundary(strand, gen, thread, letters, shown, threads),
    )
    return room


def body_alive(room: Path) -> bool:
    """True while the body's process holds the flock on ``room/.body``."""
    path = Path(room) / ".body"
    if not path.exists():
        return False
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return False
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def _spawn(room: Path, argv: list[str]) -> tuple[subprocess.Popen, object]:
    log = open(room / "port" / "body.log", "ab")
    cmd = [sys.executable, "-c", _FLOCK, str(room / ".body"), *argv]
    try:
        proc = subprocess.Popen(
            cmd, cwd=room / "self", env=_env(room), stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    except Exception:
        log.close()
        raise
    return proc, log


def _start(home: Home, strand: str, thread: str, gen: int, adapter: str,
           core: str, bodies: dict[str, Body], facts: list[Fact]) -> list[Fact]:
    """Launch one body. Process failures are reaped; launch errors reach run()."""
    if strand in bodies:
        return facts
    letters = actionable(facts, thread)
    try:
        room = _prepare(
            home, strand, thread, gen, letters, sender_threads(facts), facts,
        )
    except (SelfError, subprocess.TimeoutExpired) as exc:
        _attention(home, f"attention:wake:{strand}:{gen}", str(exc), thread=thread)
        return facts
    if adapter == "fake":
        argv = fake_argv(room, _policy(home, thread))
    elif adapter == "claude":
        argv = claude_argv(room, core, wait_seconds(room))
    else:
        raise ValueError(f"unknown adapter {adapter!r}")
    log_path = room / "port" / "body.log"
    log_start = log_path.stat().st_size if log_path.is_file() else 0
    proc, log = _spawn(room, argv)
    started = _record(
        home, "body.started",
        {"strand": strand, "gen": gen, "pid": proc.pid, "adapter": adapter},
        f"body.started:{strand}:{gen}:{proc.pid}:{mint(6)}",
    )
    facts.append(started)
    bodies[strand] = Body(
        strand, thread, gen, proc, log, log_start,
    )
    _log(home, f"start {strand} thread {thread} gen {gen} pid {proc.pid} {adapter}")
    return facts


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


def _may_ingest(home: Home, strand: str, facts: list[Fact], ttl: float) -> bool:
    """A strand's own loom ingests it. The router also ingests a silent install.

    Otherwise a dead router's outbound letters would sit in ``port/out`` forever.
    """
    from .router import install_of, install_silent
    owner = install_of(strand)
    if owner is None:
        return False
    if owner == home.install_id():
        return True
    return install_silent(facts, owner, ttl)


def _stamp_router(fact: Fact, facts: list[Fact]) -> None:
    if fact.kind != "letter" or not is_channel(fact.data.get("to")):
        return
    gens = [
        int(item.data["gen"]) for item in facts
        if item.kind == "router" and "gen" in item.data
    ]
    if gens:
        fact.data["router_gen"] = max(gens)


def _ingest(home: Home, facts: list[Fact] | None = None, ttl: float | None = None) -> list[Fact]:
    if facts is None:
        facts = read_facts(home)
    if ttl is None:
        from .config import load_config
        ttl = load_config(home.root).router_ttl
    rooms = home.root / "rooms"
    if not rooms.is_dir():
        return facts
    for room in sorted(path for path in rooms.iterdir() if path.is_dir()):
        out = room / "port" / "out"
        if not out.is_dir():
            continue
        strand = room.name
        if not _may_ingest(home, strand, facts, ttl):
            continue
        for path in sorted(out.glob("*.md")):
            try:
                fact = fact_from_port(
                    parse_frontmatter(path.read_text()), strand,
                    generation(facts, strand), room=room,
                )
                _stamp_router(fact, facts)
                facts.append(append(home, fact))
            except Exception as exc:
                _reject(home, room, path, f"{type(exc).__name__}: {exc}")
                continue
            path.unlink()
        for path in sorted(out.glob("taint-*.json")):
            try:
                fact = fact_from_taint(path.read_text(), strand, f"{strand}/{path.stem}")
                facts.append(append(home, fact))
            except Exception as exc:
                _reject(home, room, path, f"{type(exc).__name__}: {exc}")
                continue
            path.unlink()
    return facts


def _boundaries(home: Home, bodies: dict[str, Body], facts: list[Fact]) -> None:
    state = fold(facts)
    threads = sender_threads(facts)
    for body in bodies.values():
        letters = actionable(facts, body.thread)
        text = render_boundary(
            body.strand, body.gen, body.thread, letters,
            state.shown.get(body.strand, set()), threads,
        )
        path = home.room(body.strand) / "port" / "in" / "boundary.md"
        if not path.is_file() or path.read_text() != text:
            atomic_write(path, text)


def _molted(home: Home, body: Body) -> bool:
    return (home.room(body.strand) / "port" / "molt-pending").is_file()


def _reap(home: Home, bodies: dict[str, Body], adapter: str, core: str,
         facts: list[Fact]) -> list[Fact]:
    for strand, body in list(bodies.items()):
        code = body.proc.poll()
        if code is None:
            continue
        body.close_log()
        del bodies[strand]
        molted = _molted(home, body)
        failed = code != 0 or (not molted and bool(actionable(facts, body.thread)))
        if failed and walled(_log_tail(home.room(strand), body.log_start)):
            # The provider's window is spent. Not a death: nothing counts
            # toward the fuse or against the letters, and the thread stays
            # held and owed. `_rewake` starts the body again after `until`.
            facts.append(_record(home, "body.walled", {
                "strand": strand, "thread": body.thread, "gen": body.gen,
                "code": code, "until": time.time() + WALL_WAIT_S,
            }, f"body.walled:{strand}:{body.gen}:{mint(6)}"))
            _log(home, f"wall {strand} code {code}, next start in {WALL_WAIT_S:.0f}s")
            continue
        kind = "body.exited" if code == 0 else "body.died"
        data = {"strand": strand, "gen": body.gen, "code": code}
        # An unrunnable letter stays owed and must not look like unfinished work,
        # or the body that answered everything else would spin the fuse.
        if code == 0 and not molted and actionable(facts, body.thread):
            data["unfinished"] = True
        facts.append(_record(home, kind, data, f"{kind}:{strand}:{body.gen}:{mint(6)}"))
        _log(home, f"exit {strand} code {code}")
        if code != 0 or data.get("unfinished"):
            if not _fused(home, facts, strand, body.thread):
                facts = _start(
                    home, strand, body.thread, body.gen, adapter, core, bodies, facts,
                )
            continue
        if molted:
            facts = _start(
                home, strand, body.thread, body.gen, adapter, core, bodies, facts,
            )
            continue
        facts.append(_record(home, "released", {
            "thread": body.thread, "strand": strand, "gen": body.gen,
            "why": "nothing owed", "install": home.install_id(),
        }, f"released:{body.thread}:{body.gen}"))
        _log(home, f"released {strand} thread {body.thread}")
    return facts


def _log_tail(room: Path, start: int) -> str:
    """The last ``WALL_TAIL`` bytes this body wrote to its log."""
    path = room / "port" / "body.log"
    if not path.is_file():
        return ""
    with open(path, "rb") as handle:
        handle.seek(max(start, path.stat().st_size - WALL_TAIL))
        return handle.read().decode("utf-8", "replace")


def _walled_last(facts: list[Fact]) -> dict[str, Fact]:
    """Strands whose latest body fact is a wall: down, waiting, still holding."""
    last: dict[str, Fact] = {}
    for fact in facts:
        if fact.kind.startswith("body.") and fact.data.get("strand"):
            last[str(fact.data["strand"])] = fact
    return {strand: fact for strand, fact in last.items() if fact.kind == "body.walled"}


def _rewake(home: Home, bodies: dict[str, Body], adapter: str, core: str,
            facts: list[Fact]) -> list[Fact]:
    """Start again the bodies whose wait at a provider limit is over."""
    from .router import install_of
    for strand, fact in _walled_last(facts).items():
        if strand in bodies:
            continue
        if install_of(strand) != home.install_id() or time.time() < fact.data["until"]:
            continue
        thread, gen = str(fact.data["thread"]), int(fact.data["gen"])
        if holder(facts, thread) != (strand, gen):
            continue
        facts = _start(home, strand, thread, gen, adapter, core, bodies, facts)
    return facts


def _recover(home: Home) -> list[Fact]:
    """Release threads this install held whose bodies died with the last loom.

    Only this loom reaps its own strands, and the router re-grants a dead
    holder only when its install is silent or the strand fused on an unrunnable letter.
    A restarted loom is neither, so without this every thread it held stays
    leased to a corpse. A body still holding its flock is left alone, and a
    fused strand stays fused: the fuse is the person's to reset. A walled
    strand stays held too: `_rewake` starts it when its wait is over.
    """
    from .router import install_of
    facts = read_facts(home)
    state = fold(facts)
    own = home.install_id()
    for thread, (strand, gen) in sorted(state.holder.items()):
        if install_of(strand) != own or body_alive(home.room(strand)):
            continue
        if any(fact.kind == "attention" and fact.id == f"attention:fuse:{strand}"
               for fact in facts):
            continue
        if _walled_last(facts).get(strand) is not None:
            continue
        try:
            facts.append(_record(home, "released", {
                "thread": thread, "strand": strand, "gen": gen,
                "why": "loom restarted", "install": own,
            }, f"released:{thread}:{gen}"))
        except LedgerConflict:
            continue
        _log(home, f"released {strand} thread {thread} (loom restarted)")
    return facts


def tick_once(home: Home, bodies: dict[str, Body], adapter: str, core: str,
              router=None) -> None:
    """One read of the ledger, then ingest, route, boundaries, reap.

    ``router`` is omitted only by callers that have not opened one yet;
    ``run`` always passes the process's router.
    """
    from .router import Router
    if router is None:
        router = Router(home)
    facts = read_facts(home)
    facts = router.begin_tick(facts)
    facts = _ingest(home, facts, router.config.router_ttl)
    facts = router.route(facts, bodies, adapter, core)
    facts = router.speak_out(facts)
    _boundaries(home, bodies, facts)
    facts = _reap(home, bodies, adapter, core, facts)
    _rewake(home, bodies, adapter, core, facts)


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
        tick: float = 0.2, stop=None, install: str | None = None) -> None:
    if adapter not in {"fake", "claude"}:
        raise ValueError(f"unknown adapter {adapter!r}")
    if tick <= 0:
        raise ValueError("tick must be positive")
    stop = _own_stop(stop)
    home = Home(root, install=install)
    home.install_id()
    from .router import Router
    router = Router(home)
    _recover(home)
    relay = _arm_relay(home, router.config, stop)
    bodies: dict[str, Body] = {}
    try:
        while not (stop is not None and stop.is_set()):
            try:
                tick_once(home, bodies, adapter, core, router)
            except Exception:
                _log(home, traceback.format_exc().rstrip())
            if _pause(stop, tick):
                break
    finally:
        _shutdown(bodies)
        if relay is not None:
            _disarm_relay(relay)


def _arm_relay(home: Home, config, stop):
    """``relay_state`` arms the speech effect and the relay poll thread."""
    if not config.relay_state:
        return None
    from brr.gates.relay_lock import RelayLock
    from . import speak
    from .channels.relay import RelayClient, make_effect, poll_forever
    client = RelayClient(config.relay_state)
    speak.EFFECTS["relay"] = make_effect(client)
    lock = RelayLock(config.relay_state, "loom")
    thread = threading.Thread(
        target=poll_forever, args=(home, client, lock, stop),
        kwargs={"log": lambda message: _log(home, message)},
        daemon=True, name="loom-relay",
    )
    thread.start()
    _log(home, f"relay: armed on {config.relay_state}")
    return thread


def _disarm_relay(thread) -> None:
    from . import speak
    speak.EFFECTS.pop("relay", None)
    # The poll thread sees ``stop`` within one long-poll; it releases the
    # lock and its want on the way out. A daemon thread never blocks exit.
    thread.join(timeout=1.0)

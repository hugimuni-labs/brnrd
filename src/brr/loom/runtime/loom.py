"""One process, one loop: ingest, route, rewrite boundaries, reap."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import shlex
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

from .adapters import resolve
from .home import Home, atomic_write, is_channel, mint
from .ledger import LedgerConflict, append, read_facts
from .port import (
    fact_from_port, fact_from_taint, parse_frontmatter, render_boundary,
)
from .project import actionable, fold, generation, is_notice, sender_threads
from .selfrepo import (
    AUTHOR_EMAIL, AUTHOR_NAME, SelfError, _env_without_pin, room as clone_room, run_wake,
)

SRC = str(Path(__file__).resolve().parents[3])
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
    env["BRNRD_WAKE"] = str(room / "port" / "wake.md")
    env["BRNRD_JACK"] = (f"{shlex.quote(sys.executable)} -m brr.loom.runtime jack "
                         f"--room {shlex.quote(str(room))}")
    wait = room / "port" / "wait"
    env["BRNRD_WAIT"] = wait.read_text().strip() if wait.is_file() else "3600"
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


def notice_id(kind: str, subject: str) -> str:
    return f"loom/{kind}-{hashlib.sha256(subject.encode()).hexdigest()[:10]}"


def notice(home: Home, facts: list[Fact], kind: str, subject: str, what: str, can: str, *,
           thread: str | None = None, about: Fact | None = None, stands: bool = False,
           taint: bool = False, channel: str | None = None,
           source_strand: str | None = None, passive: bool = False) -> bool:
    """The one writer of loom notices: what the loom cannot settle, a body is told.

    A notice is an owed letter from ``loom``, so it is shown at every
    boundary until a body settles it. Its id is derived from the condition:
    a condition met on every tick writes one letter. It goes to ``thread``
    when a body can run there, else to ``inbox``. The last lines always name
    the handle. ``stands`` ties it to ``about``: the two settle together.
    """
    ident = notice_id(kind, subject)
    if any(fact.id == ident for fact in facts):
        return False
    from .labels import Label, SELF_AUDIENCE, _sender_label, join, strand_label
    label = Label(taint, SELF_AUDIENCE)
    if source_strand is not None:
        label = join(label, strand_label(facts, source_strand, self_root=home.root / "self"))
    lines = [what]
    data: dict = {"id": ident, "from": "loom",
                  "to": channel or f"thread:{thread if home.routable(thread) else 'inbox'}"}
    if passive:
        data["notice"] = True
    if about is not None:
        label = join(label, _sender_label(about, facts, home.root / "self", frozenset()))
        lines.append(f"{about.data['id']}: {can}")
        if stands:
            data["stands"] = str(about.data["id"])
            lines.append(f"settling this notice settles {about.data['id']} too")
    else:
        lines.append(can)
    if channel is None:
        lines.append(f"settle this notice with a reply or a note, re: {ident}")
    data.update(body="\n".join(lines), label=label.as_dict())
    draft = Fact(kind="letter", by=_by(home), data=data, id=ident)
    if channel is not None:
        if about is not None:
            data["re"] = about.data["id"]
        _stamp_router(draft, facts)
    try:
        facts.append(append(home, draft))
    except LedgerConflict:
        return False
    _log(home, f"notice {ident} to {data['to']}: {what}")
    return True


def _closures(home: Home, facts: list[Fact], threads: list[str]) -> list[Fact]:
    from .lifecycle import CLOSED, closing_commit, read_thread
    writers = sender_threads(facts)
    for thread in threads:
        data = read_thread(home, thread)
        if data.get("status") not in CLOSED:
            continue
        commit, why, writer = closing_commit(home, thread)
        ident = f"thread.closed:{thread}:{commit}"
        if not any(f.id == ident for f in facts):
            facts.append(_record(home, "thread.closed", {
                "thread": thread, "status": data["status"], "why": why, "commit": commit,
            }, ident))
        opener = str(data.get("opened-by", ""))
        if opener.startswith("thread:") and writers.get(writer) != opener.removeprefix("thread:"):
            notice(home, facts, "closed", ident, f"thread {thread} {data['status']}: {why}",
                   "the thread is closed", thread=opener.removeprefix("thread:"), passive=True)
    return facts


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


def _prepare(home: Home, strand: str, thread: str, gen: int,
             letters: list[Fact], threads: dict[str, str],
             facts: list[Fact], jack: str) -> Path:
    room = home.room(strand).resolve()
    clone = clone_room(home.root, strand)
    (room / "port" / "in").mkdir(parents=True, exist_ok=True)
    (room / "port" / "out").mkdir(parents=True, exist_ok=True)
    wait_src = home.thread_dir(thread) / "wait"
    if wait_src.is_file():
        atomic_write(room / "port" / "wait", wait_src.read_text().strip() + "\n")
    for name in ("jack-state.json", "policy"):
        (room / "port" / name).unlink(missing_ok=True)
    owed_path = room / "port" / "owed.json"
    atomic_write(owed_path, json.dumps([letter.data for letter in letters]))
    part_path = room / "port" / "loom.md"
    atomic_write(part_path, (
        f"you are strand {strand} on thread {thread}; letters arrive at your "
        "tool boundaries; answer with `python -m brr.loom.runtime send --re <id> "
        "--to thread:<from-thread> \"…\"`; commit in this self clone and land "
        "your branch with `python -m brr.loom.runtime send-self --room .`; "
        "a letter from `loom` is a notice of something the loom could not settle, "
        "yours to deal with: its last lines name what settles it "
        "(core/loom/README.md, Notices); "
        + ("call `$BRNRD_JACK --event start` now, `$BRNRD_JACK --event post` between "
           "steps and `$BRNRD_JACK --event stop` before ending; read each returned boundary\n"
           if jack == "hand" else
           "when you're done, stop, and the jack holds you while letters may come\n")
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


def _start(home: Home, strand: str, thread: str, gen: int,
           bodies: dict[str, Body], facts: list[Fact], choice=None) -> list[Fact]:
    """Launch one body; the gauge can defer a start without consuming letters."""
    if strand in bodies:
        return facts
    letters = actionable(facts, thread)
    ids = [str(letter.data["id"]) for letter in letters]
    try:
        started = next((f for f in facts if f.kind in {"body.started", "body.pending", "body.parked"}
                        and f.data.get("strand") == strand), None)
        shell, core = (started.data["shell"], started.data["core"]) if started else choice
        folder = resolve(home, shell)
        from .usage import reading
        prior = [f for f in facts if f.data.get("strand") == strand]
        after = max((datetime.fromisoformat(f.at).timestamp() for f in prior
                     if f.kind in {"body.exited", "body.parked"}), default=0)
        gauge = reading(home, shell, after)
        data = {"strand": strand, "gen": gen, "shell": shell, "core": core}
        if gauge is None:
            if not prior or prior[-1].kind not in {"body.pending", "body.parked"}:
                facts.append(_record(home, "body.pending", data, f"body.pending:{strand}:{mint(6)}"))
            return facts
        empty = next((w for w in gauge["windows"] if w["left_pct"] <= 0), None)
        if empty:
            try:
                until = datetime.fromisoformat(empty["resets_at"]).timestamp()
            except (ValueError, TypeError, KeyError):
                until = 0
            # A reset time that is missing or already past (a gauge that lags
            # its own reset) would park, expire and re-park on every reading.
            if until <= time.time():
                until = time.time() + 900
            data.update(window=empty["name"], until=until)
            facts.append(_record(home, "body.parked", data, f"body.parked:{strand}:{mint(6)}"))
            return facts
        room = _prepare(
            home, strand, thread, gen, letters, sender_threads(facts), facts,
            (folder / "jack").read_text().strip(),
        )
        policy = _policy(home, thread)
        if policy is not None:
            atomic_write(room / "port/policy", policy + "\n")
        argv = [str(folder / "run"), str(room), core]
        # The wake has already exposed these letters. Publish that label
        # before exec: send-self can precede the body's first jack receipt.
        facts.append(_record(home, "shown", {
            "strand": strand, "gen": gen, "ids": ids,
        }, f"shown:wake:{strand}:{gen}:{mint(6)}"))
        readme = _readme(room, thread)
        proc, log = _spawn(room, argv)
    except Exception as exc:
        why = f"{type(exc).__name__}: {exc}"
        _log(home, f"start failed {strand}: {why}")
        facts.append(_record(home, "body.exited", {
            "strand": strand, "gen": gen, "code": None, "why": why,
        }, f"body.exited:{strand}:{gen}:{mint(6)}"))
        # No process reached the wake: tell the author about the failed start,
        # rather than repeatedly trying preparation for the same letter.
        _waste(home, facts, strand, thread, None, letters, why)
        return _release(home, facts, strand, thread, gen)
    facts.append(_record(home, "body.started", {
        "strand": strand, "gen": gen, "pid": proc.pid, "shell": shell, "core": core,
        "readme": readme,
    }, f"body.started:{strand}:{gen}:{proc.pid}:{mint(6)}"))
    bodies[strand] = Body(strand, thread, gen, proc, log)
    _log(home, f"start {strand} thread {thread} gen {gen} pid {proc.pid} {shell}/{core}")
    return facts


def _threads(home: Home) -> list[str]:
    root = home.root / "self" / "threads"
    if not root.is_dir():
        return []
    return sorted(
        path.name for path in root.iterdir() if (path / "README.md").is_file()
    )


def _reject(home: Home, room: Path, path: Path, message: str, facts: list[Fact]) -> None:
    rejected = room / "port" / "rejected"
    rejected.mkdir(parents=True, exist_ok=True)
    os.replace(path, rejected / path.name)
    _log(home, f"rejected {path.name}: {message}")
    # Only a strand that still holds its thread is told: it reads the notice
    # at its next boundary. A late file from a released strand is logged; the
    # letter it meant to answer is still owed and reaches the next body.
    thread = next((name for name, (strand, _gen) in fold(facts).holder.items()
                   if strand == room.name), None)
    if thread is not None:
        notice(home, facts, "reject", f"{room.name}/{path.name}",
               f"the loom refused your port file {path.name}: {message}",
               f"nothing in it was recorded; it is kept in port/rejected/{path.name}. "
               "Send it again, corrected, if it still matters",
               thread=thread)


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
                _reject(home, room, path, f"{type(exc).__name__}: {exc}", facts)
                continue
            path.unlink()
        for path in sorted(out.glob("taint-*.json")):
            try:
                fact = fact_from_taint(path.read_text(), strand, f"{strand}/{path.stem}")
                facts.append(append(home, fact))
            except Exception as exc:
                _reject(home, room, path, f"{type(exc).__name__}: {exc}", facts)
                continue
            path.unlink()
    return facts


def _readme(room: Path, thread: str) -> str | None:
    try:
        return hashlib.sha256((room / "self/threads" / thread / "README.md").read_bytes()).hexdigest()
    except OSError:
        return None


def _release(home, facts, strand, thread, gen):
    facts.append(_record(home, "released", {
        "thread": thread, "strand": strand, "gen": gen,
        "why": "body ended", "install": home.install_id(),
    }, f"released:{thread}:{gen}"))
    return facts


def _waste(home, facts, strand, thread, code, letters, failure=""):
    from .selfrepo import ReadmeError, parse_readme
    from .usage import fuel
    room = home.room(strand)
    try:
        with (room / "port/body.log").open("rb") as log:
            log.seek(0, os.SEEK_END)
            log.seek(max(0, log.tell() - 400))
            tail = log.read().decode(errors="replace")
    except OSError:
        tail = failure
    start = next((f for f in reversed(facts) if f.kind == "body.started"
                  and f.data.get("strand") == strand), None)
    changed = (_readme(room, thread) != start.data.get("readme")) if start else "unknown"
    tank = fuel(home, start.data["shell"]) if start else ""
    try:
        opener = parse_readme((home.thread_dir(thread) / "README.md").read_text()).get("opened-by", "")
    except (OSError, ReadmeError):
        opener = ""
    threads = sender_threads(facts)
    for letter in letters:
        ident, author = str(letter.data["id"]), str(letter.data.get("from", ""))
        if letter.data.get("notice"):
            # Told once and nothing was asked: not a failure anyone must hear of.
            facts.append(_record(home, "note", {"strand": strand, "re": ident,
                                "why": "notice shown"}, f"wasted:{ident}"))
            continue
        what = (f"letter {ident} on thread {thread}: body exited {code}; "
                f"README changed since start: {changed}; {tank or 'fuel unknown'}\n{tail}")
        target = threads.get(author) or (opener.removeprefix("thread:") if opener.startswith("thread:") else None)
        if target == thread:
            target = opener.removeprefix("thread:") if opener.startswith("thread:") else None
        if author.startswith("person:"):
            retry = f"retry:{ident}"
            if not any(f.id == retry for f in facts):
                facts.append(_record(home, "letter.retry", {"strand": strand, "letter": ident}, retry))
                continue
            source = next((f for f in facts if f.id == letter.data.get("cites")), None)
            channel = (f"channel:{source.data['platform']}/{source.data['chat']}"
                       if source and source.data.get("platform") and source.data.get("chat") is not None else None)
            if channel:
                first = str(letter.data.get("body", "")).splitlines()
                notice(home, facts, "failed-message", ident,
                       f"thread {thread} could not handle your letter: {first[0] if first else ''}\n{tail}",
                       "send again, or leave it", about=letter, channel=channel, source_strand=strand)
            else:
                _attention(home, f"attention:wasted:{ident}", what, thread=thread, letter=ident)
                _log(home, f"wasted {ident} on thread {thread}: nobody to tell")
        elif not is_notice(letter) and not author.startswith("stranger:") and home.routable(target):
            notice(home, facts, "bounce", ident, what, "send again, or leave it",
                   thread=target, about=letter, source_strand=strand)
        else:
            _attention(home, f"attention:wasted:{ident}", what, thread=thread, letter=ident)
            _log(home, f"wasted {ident} on thread {thread}: nobody to tell")
        # The loom disposes of the failed letter; the bounce is a separate ask.
        facts.append(_record(home, "note", {"strand": strand, "re": ident,
                            "why": "body ended without handling it"}, f"wasted:{ident}"))


def _reap(home: Home, bodies: dict[str, Body], facts: list[Fact]) -> list[Fact]:
    for strand, body in list(bodies.items()):
        code = body.proc.poll()
        if code is None:
            continue
        # Drain the body's last boundary and answer before deciding what it wasted.
        facts = _ingest(home)
        body.close_log()
        del bodies[strand]
        facts.append(_record(home, "body.exited", {
            "strand": strand, "gen": body.gen, "code": code,
        }, f"body.exited:{strand}:{body.gen}:{mint(6)}"))
        _log(home, f"exit {strand} code {code}")
        molted = any(f.kind == "molt" and f.data.get("strand") == strand
                     and f.data.get("gen") == body.gen for f in fold(facts).accepted)
        # A closed thread's body is expected to leave; its letters climb (router).
        from .lifecycle import CLOSED, read_thread
        if not molted and read_thread(home, body.thread).get("status") not in CLOSED:
            shown = fold(facts).shown.get(strand, set())
            _waste(home, facts, strand, body.thread, code,
                   [letter for letter in actionable(facts, body.thread) if letter.data["id"] in shown])
        facts = _release(home, facts, strand, body.thread, body.gen)
    return facts


def pending_bodies(facts: list[Fact]) -> dict[str, Fact]:
    """The latest body fact for each strand, used by the tick and attention."""
    return {str(f.data["strand"]): f for f in facts
            if f.kind.startswith("body.") and f.data.get("strand")}


def _refresh_holders(home: Home, bodies: dict[str, Body], facts: list[Fact]) -> list[Fact]:
    """Own holders: refresh live boundaries, retry pending starts, release corpses."""
    from .router import install_of
    from .usage import fuel
    from .lifecycle import CLOSED, closing_commit, read_thread
    shells = {f.data["strand"]: f.data.get("shell") for f in facts if f.kind == "body.started"}
    state, threads, pending = fold(facts), sender_threads(facts), pending_bodies(facts)
    for thread, (strand, gen) in state.holder.items():
        if install_of(strand) != home.install_id():
            continue
        if strand in bodies or body_alive(home.room(strand)):
            settled = (closing_commit(home, thread)[1]
                       if read_thread(home, thread).get("status") in CLOSED else None)
            text = render_boundary(strand, gen, thread, actionable(facts, thread),
                                   state.shown.get(strand, set()), threads,
                                   fuel(home, shells[strand]) if shells.get(strand) else "", settled)
            path = home.room(strand) / "port" / "in" / "boundary.md"
            if not path.is_file() or path.read_text() != text:
                atomic_write(path, text)
        elif read_thread(home, thread).get("status") in CLOSED:
            facts = _release(home, facts, strand, thread, gen)
        elif strand in pending and pending[strand].kind in {"body.pending", "body.parked"}:
            if time.time() >= pending[strand].data.get("until", 0):
                facts = _start(home, strand, thread, gen, bodies, facts)
        else:
            try:
                facts.append(_record(home, "released", {
                    "thread": thread, "strand": strand, "gen": gen,
                    "why": "holder gone", "install": home.install_id(),
                }, f"released:{thread}:{gen}"))
            except LedgerConflict:
                # Someone else's release of this grant is on the ledger; a
                # conflict here must not abort the tick for every thread.
                continue
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
    facts = _refresh_holders(home, bodies, facts)
    facts = router.route(facts, bodies, (adapter, core))
    facts = router.speak_out(facts)
    facts = _reap(home, bodies, facts)


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
    if tick <= 0:
        raise ValueError("tick must be positive")
    stop = _own_stop(stop)
    home = Home(root, install=install)
    home.install_id()
    from .router import Router
    router = Router(home)
    relay = _arm_relay(home, router.config, stop)
    bodies: dict[str, Body] = {}
    try:
        while not (stop is not None and stop.is_set()):
            try:
                if relay is not None and not relay.is_alive():
                    _log(home, "relay: worker died; restarting")
                    relay = _arm_relay(home, router.config, stop)
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

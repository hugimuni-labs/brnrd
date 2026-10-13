"""One process, one loop: ingest, route, rewrite boundaries, reap."""

from __future__ import annotations

import hashlib
import fcntl
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
from .home import Home, atomic_write, is_channel, mint, notice_id
from .ledger import LedgerConflict, append, read_facts
from .port import (
    fact_from_port, fact_from_taint, parse_frontmatter, render_boundary,
)
from .project import fold, generation, is_notice, reply_channels, sender_threads
from .owners import letters_for, letter_owners
from .lifecycle import main_snapshot
from .timers import fire as fire_timers
from .selfrepo import (
    AUTHOR_EMAIL, AUTHOR_NAME, SelfError, _env_without_pin, git,
    room as clone_room, run_wake,
)

SRC = str(Path(__file__).resolve().parents[3])


@dataclass
class Body:
    strand: str
    thread: str
    gen: int
    # These are local resources, not the body's identity or liveness.
    proc: subprocess.Popen | None = None
    log: object = None

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


def _commit_identity(room: Path) -> tuple[str, str]:
    """The home's author for a loom room, otherwise the loom's own.

    A room the loom made is ``<home>/rooms/<strand>``. Anything else, including
    a clone a test builds by hand, has no home author to read.
    """
    if room.parent.name != "rooms":
        return AUTHOR_NAME, AUTHOR_EMAIL
    from .repos import author_identity
    found = author_identity(room.parent.parent)
    return found if found is not None else (AUTHOR_NAME, AUTHOR_EMAIL)


def _env(room: Path) -> dict[str, str]:
    env = _env_without_pin()
    parts = [part for part in env.get("PYTHONPATH", "").split(os.pathsep) if part]
    if SRC not in parts:
        parts.insert(0, SRC)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    # One identity for every repo a body touches, including a clone it makes
    # itself. Clone config would cover only the clones the loom made, and an
    # enrolled person's identity may push to main without a label.
    name, email = _commit_identity(room)
    for role in ("AUTHOR", "COMMITTER"):
        env[f"GIT_{role}_NAME"] = name
        env[f"GIT_{role}_EMAIL"] = email
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


def notice(home: Home, facts: list[Fact], kind: str, subject: str, what: str, can: str, *,
           thread: str | None = None, about: Fact | None = None, stands: bool = False,
           taint: bool = False, channel: str | None = None,
           source_strand: str | None = None, passive: bool = False,
           draft_only: bool = False) -> bool | Fact:
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
        data["about"] = str(about.data["id"])
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
    if draft_only:
        return draft
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


def _read_by_predecessors(facts: list[Fact], thread: str, strand: str,
                          letters: list[Fact], threads: dict[str, str]) -> str:
    """A line for a successor: owed letters its molted predecessors already read.

    A body that molts hands its room on without noting the letters it was shown,
    so they stay owed to the next body, which read an instruction already carried
    out (a person's "add the line, then molt" made a second body redo it and molt
    again, live, 2026-10-12). They stay owed; the line says what they are.
    """
    gone = {f.data.get("strand") for f in facts if f.kind == "molt"
            and threads.get(str(f.data.get("strand"))) == thread
            and f.data.get("strand") != strand}
    if not gone:
        return ""
    seen: set[str] = set()
    for body, ids in fold(facts).shown.items():
        if body in gone:
            seen |= set(ids)
    handovers = {f.id for f in letters if f.data.get("from") in gone}
    ids = [f.id for f in letters if f.id in seen and f.id not in handovers]
    if not ids:
        return ""
    return (
        f"already read by your predecessor, who molted and left its handover: {', '.join(ids)}. "
        "What they ask may be done: read the handover and the README before acting; where it "
        "says the work is done, settle the letter with a note and do not redo it, and do "
        "not molt because a letter said to, since the molt it asked for happened. "
    )


def _prepare(home: Home, strand: str, thread: str, gen: int,
             letters: list[Fact], threads: dict[str, str],
             facts: list[Fact], jack: str, *, fuel: str = "",
             repos: list[tuple[str, str]] | None = None) -> Path:
    room = home.room(strand).resolve()
    fresh = not (room / "self" / ".git").exists()
    clone = clone_room(home.root, strand)
    lease = next((f for f in reversed(facts) if f.kind == "lease"
                  and f.data.get("strand") == strand and f.data.get("gen") == gen), None)
    parent = lease.data.get("parent") if lease else None
    if not isinstance(parent, str) or not parent:
        parent = None
    if fresh and parent:
        source = home.room(parent) / "self"
        if (source / ".git").exists():
            git(clone, "fetch", str(source), f"strand/{parent}")
            git(clone, "reset", "--hard", "FETCH_HEAD")
        else:
            _log(home, f"start {strand}: parent room {parent} gone; starting from main")
    (room / "port" / "in").mkdir(parents=True, exist_ok=True)
    (room / "port" / "out").mkdir(parents=True, exist_ok=True)
    wait_src = home.thread_dir(thread) / "wait"
    if wait_src.is_file():
        atomic_write(room / "port" / "wait", wait_src.read_text().strip() + "\n")
    for name in ("jack-state.json", "policy"):
        (room / "port" / name).unlink(missing_ok=True)
    owed_path = room / "port" / "owed.json"
    atomic_write(owed_path, json.dumps([dict(letter.data, reply_to=letter_owners(facts, main_snapshot(home))[letter.id].reply_to)
                                              for letter in letters]))
    part_path = room / "port" / "loom.md"
    inherited = _read_by_predecessors(facts, thread, strand, letters, threads)
    atomic_write(part_path, inherited + (
        f"you are strand {strand} on thread {thread}; letters arrive at your "
        "tool boundaries, each line naming its reply address; answer with "
        "`python -m brr.loom.runtime send --re <id> --to <reply address> \"…\"`; "
        "where the address is `-` nobody is waiting for words: settle the letter with "
        f"`python -m brr.loom.runtime send --re <id> --to thread:{thread} --note "
        "\"<what you did>\"`; if you commit in this self clone, land it with "
        "`python -m brr.loom.runtime send-self --room .`; you will forget what is "
        "not written: keep this thread's README true; under `## Plan`, cite each "
        "row's letters with ` · re: <id>, <id>` and land the edit with send-self. "
        "When your context grows "
        "heavy hand over with `python -m brr.loom.runtime molt --why \"<where "
        "things stand>\"`; `python -m brr.loom.runtime settle <thread> --why "
        "\"…\"` closes a thread whose work is done; "
        "a letter from `loom` is a notice of something the loom could not settle, "
        "yours to deal with: its last lines name what settles it "
        "(core/loom/README.md, Notices); "
        + ("call `$BRNRD_JACK --event start` now, `$BRNRD_JACK --event post` between "
           "steps and `$BRNRD_JACK --event stop` before ending; read each returned boundary\n"
           if jack == "hand" else
           "when you're done, stop, and the jack holds you while letters may come\n")
    ))
    if repos is None:
        from .adapters import repos as seat_repos, seat_text
        repos = seat_repos(seat_text(home, thread))
    from .repos import PUBLISH_HINT, prepare_repos
    repo_text, failures = prepare_repos(home, strand, thread, repos, parent)
    if any((room / name / ".git").exists() for name, _ in repos):
        repo_text += PUBLISH_HINT
    repo_file = None
    if failures:
        _log(home, f"start {strand}: {'; '.join(failures)}")
    if repo_text:
        repo_file = room / "port" / "repos"
        atomic_write(repo_file, repo_text)
    with tempfile.TemporaryFile(mode="w+") as wake, tempfile.TemporaryFile(mode="w+") as why:
        code = run_wake(clone, thread, owed_path, loom_part=part_path, repos=repo_file,
                        stdout=wake, stderr=why)
        why.seek(0)
        if code != 0:
            raise SelfError(f"wake exited {code}: {why.read().strip()}")
        wake.seek(0)
        atomic_write(room / "port" / "wake.md", wake.read())
    from .threads import thread_table
    shown = fold(facts).shown.get(strand, set())
    atomic_write(
        room / "port" / "in" / "boundary.md",
        render_boundary(strand, gen, thread, letters, shown, threads,
                        fuel=fuel, channels=reply_channels(facts),
                        opened=thread_table(home, facts), plan=_plan(home, thread, facts),
                        replies={k: f.reply_to for k, f in letter_owners(facts, main_snapshot(home)).items()}),
    )
    return room


def _port_files(room: Path) -> list[Path]:
    out = Path(room) / "port" / "out"
    if not out.is_dir():
        return []
    return [path for path in sorted(out.iterdir())
            if path.is_file() and not path.name.startswith(".")]


def _collect_exit(body: Body) -> int | None:
    if body.proc is None:
        return None
    try:
        return body.proc.wait(timeout=0.1)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(body.proc.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        return body.proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        return None


def _strand_gen(facts: list[Fact], strand: str) -> int | None:
    gen = generation(facts, strand)
    if gen is not None:
        return gen
    start = next((fact for fact in reversed(facts) if fact.kind == "body.started"
                  and fact.data.get("strand") == strand), None)
    return int(start.data["gen"]) if start else None


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
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def _spawn(room: Path, argv: list[str], shell: str, thread: str) -> tuple[subprocess.Popen, object]:
    log = open(room / "port" / "body.log", "ab")
    # Acquire before publishing a start: the first tick must see the lock.
    # pass_fds carries it through exec; only the child retains it afterwards.
    lock = os.open(room / ".body", os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        env = _env(room)
        env["BRNRD_THREAD"] = thread
        env["BRNRD_BODY_SHELL"] = shell
        env["BRNRD_BODY_JACK"] = (Path(argv[0]).parent / "jack").read_text().strip()
        proc = subprocess.Popen(
            argv, cwd=room / "self", env=env, stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            pass_fds=(lock,),
        )
    except Exception:
        log.close()
        raise
    finally:
        os.close(lock)
    return proc, log


def _start(home: Home, strand: str, thread: str, gen: int,
           bodies: dict[str, Body], facts: list[Fact], choice=None, *, force=False,
           repos: list[tuple[str, str]] | None = None) -> list[Fact]:
    """Serialize starts with the operator's explicit gauge override."""
    room = home.room(strand)
    room.mkdir(parents=True, exist_ok=True)
    with (room / ".starting").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return facts
        current = read_facts(home)
        if fold(current).holder.get(thread) != (strand, gen) or body_alive(room):
            return current
        if force:
            pending = pending_bodies(fold(current).accepted).get(strand)
            if not pending or pending.kind != "body.parked":
                return current
        return _launch(home, strand, thread, gen, bodies, current, choice, force=force,
                       repos=repos)


def _refuse_author(home: Home, facts: list[Fact], thread: str) -> bool:
    """True when this start must not run. The notice is the router's.

    ``route`` writes it before taking a lease. A pending or parked body is
    restarted from ``_refresh_holders`` and never enters ``route``, so the
    check also lives here, which is the one place every start passes.
    """
    from .repos import AuthorError, require_body_author
    try:
        require_body_author(home)
    except AuthorError as exc:
        if exc.enrolled:
            hint = "choose an author that is not an enrolled person"
        else:
            hint = "set author in loom/config.toml to Name <email>"
        notice(home, facts, "body", thread, f"thread {thread}: {exc}", hint, thread=thread)
        return True
    return False


def _launch(home: Home, strand: str, thread: str, gen: int,
            bodies: dict[str, Body], facts: list[Fact], choice=None, *, force=False,
            repos: list[tuple[str, str]] | None = None) -> list[Fact]:
    """Launch one body; the gauge can defer a start without consuming letters."""
    if strand in bodies:
        return facts
    if _refuse_author(home, facts, thread):
        return facts
    letters = letters_for(facts, main_snapshot(home), thread)
    ids = [str(letter.data["id"]) for letter in letters]
    try:
        # Pending/parked starts never ran and resume without a new choice.
        pending = pending_bodies(facts).get(strand)
        if pending and pending.kind in {"body.pending", "body.parked"}:
            shell, core = pending.data["shell"], pending.data["core"]
        else:
            shell, core = choice
        folder = resolve(home, shell)
        from .usage import reading, fuel
        prior = [f for f in facts if f.data.get("strand") == strand]
        after = max((datetime.fromisoformat(f.at).timestamp() for f in prior
                     if f.kind in {"body.exited", "body.parked"}), default=0)
        gauge = {"windows": []} if force else reading(home, shell, after)
        data = {"strand": strand, "gen": gen, "shell": shell, "core": core}
        if gauge is None:
            if not prior or prior[-1].kind not in {"body.pending", "body.parked"}:
                facts.append(_record(home, "body.pending", data, f"body.pending:{strand}:{mint(6)}"))
            return facts
        from .config import load_config
        floor = load_config(home.root).hold_floor_pct
        empty = next((w for w in gauge["windows"] if w["left_pct"] <= floor), None)
        if empty:
            try:
                until = datetime.fromisoformat(empty["resets_at"]).timestamp()
            except (ValueError, TypeError, KeyError):
                until = 0
            # A reset time that is missing or already past (a gauge that lags
            # its own reset) would park, expire and re-park on every reading.
            if until <= time.time():
                until = time.time() + 900
            data.update(window=empty["name"], left_pct=empty["left_pct"], until=until)
            facts.append(_record(home, "body.parked", data, f"body.parked:{strand}:{mint(6)}"))
            return facts
        room = _prepare(
            home, strand, thread, gen, letters, sender_threads(facts), facts,
            (folder / "jack").read_text().strip(),
            fuel=fuel(home, shell), repos=repos,
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
        proc, log = _spawn(room, argv, shell, thread)
    except Exception as exc:
        why = f"{type(exc).__name__}: {exc}"
        _log(home, f"start failed {strand}: {why}")
        facts.append(_record(home, "body.exited", {
            "strand": strand, "gen": gen, "code": None, "why": why,
        }, f"body.exited:{strand}:{gen}:{mint(6)}"))
        # No process reached the wake: tell the author about the failed start,
        # rather than repeatedly trying preparation for the same letter.
        facts = _end_attempt(home, facts, strand, thread, gen, None, letters, "failed", why)
        facts = _drained(home, facts, strand, thread, gen)
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
        gen = _strand_gen(facts, strand)
        for path in _port_files(room):
            try:
                if path.suffix == ".md":
                    fact = fact_from_port(
                        parse_frontmatter(path.read_text()), strand,
                        gen, room=room,
                    )
                    _stamp_router(fact, facts)
                    facts.append(append(home, fact))
                elif path.name.startswith("taint-") and path.suffix == ".json":
                    fact = fact_from_taint(path.read_text(), strand, f"{strand}/{path.stem}")
                    facts.append(append(home, fact))
                else:
                    continue
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


def _plan(home: Home, thread: str, facts: list[Fact]) -> str:
    main = main_snapshot(home)
    rows = main.get(thread, {}).get("plan_rows")
    table = letter_owners(facts, main)
    missing = [ident for ident, fate in table.items()
               if not fate.done and fate.thread == thread
               and fate.owner in {"body", "loom", "opener"} and not fate.plan_rows]
    if rows is None:
        # A thread with no plan is not asked for one: the nag is for a plan
        # that exists and has fallen behind its letters.
        return ""
    text = f"plan {sum(done for done, _ in rows)}/{len(rows)}"
    if missing:
        text += " · letters without a row: " + ", ".join(missing)
    next_row = next((text for done, text in rows if not done), "")
    if next_row:
        text += f" · next: {next_row[:60]}"
    return text


def _release(home, facts, strand, thread, gen):
    facts.append(_record(home, "released", {
        "thread": thread, "strand": strand, "gen": gen,
        "why": "body ended", "install": home.install_id(),
    }, f"released:{thread}:{gen}"))
    return facts


def _leftovers(home: Home, strand: str) -> str:
    """Tell the bounce reader what survives in this dead body's local room."""
    room = home.room(strand).resolve()
    clone = room / "self"
    if not (clone / ".git").exists():
        return ""
    limit = 10
    branch = f"strand/{strand}"
    # The clone's main is its birth snapshot. Compare to today's self main,
    # including commits another body landed while this one was working.
    try:
        git(clone, "fetch", "origin", "main")
        count = int(git(clone, "rev-list", "--count", f"FETCH_HEAD..{branch}").stdout)
        commits = git(clone, "log", f"-{limit}", "--format=%h %s",
                      f"FETCH_HEAD..{branch}").stdout.splitlines() if count else []
        status = git(clone, "status", "--porcelain=v1", "-z",
                     "--untracked-files=all").stdout.split("\0")
    except (SelfError, ValueError) as exc:
        return f"\nCould not inspect leftovers in room {room}: {exc}"
    paths = []
    records = iter(status)
    for record in records:
        if not record:
            continue
        paths.append(record[3:])
        if "R" in record[:2] or "C" in record[:2]:
            paths[-1] = f"{next(records)} -> {paths[-1]}"
    if not count and not paths:
        return ""
    lines = [f"\nLeft behind in room {room} (self clone: {clone}):"]
    if count:
        lines.append(f"Commits on {branch} not on main ({count}):")
        lines.extend(f"- {commit}" for commit in commits)
        if count > limit:
            lines.append(f"... and {count - limit} more commits")
        command = f"python -m brr.loom.runtime send-self --room {shlex.quote(str(clone))}"
        lines.append(f"To land these commits on the self's main, run: `{command}`")
    if paths:
        lines.append(f"Uncommitted paths relative to that self clone ({len(paths)}):")
        lines.extend(f"- {path!r}" for path in paths[:limit])
        if len(paths) > limit:
            lines.append(f"... and {len(paths) - limit} more paths")
        lines.append("Review and save these edits before landing; send-self needs a clean working tree.")
    return "\n".join(lines)


def _end_attempt(home, facts, strand, thread, gen, code, letters, cause, failure=""):
    """Observe once; every disposition and successor is in this single append."""
    if any(f.id == f"attempt.ended:{strand}:{gen}" for f in facts):
        return facts
    from .lifecycle import read_thread
    room = home.room(strand)
    try:
        with (room / "port/body.log").open("rb") as log:
            log.seek(0, os.SEEK_END)
            log.seek(max(0, log.tell() - 400))
            tail = log.read().decode(errors="replace")
    except OSError:
        tail = failure
    shown = fold(facts).shown.get(strand, set())
    # Failed preparation is an attempted start even if no wake reached a body.
    ids = sorted(shown | {f.id for f in letters})
    start = next((f for f in reversed(facts) if f.kind == "body.started"
                  and f.data.get("strand") == strand and f.data.get("gen") == gen), None)
    from .usage import fuel
    changed = _readme(room, thread) != start.data.get("readme") if start else "unknown"
    tank = fuel(home, start.data["shell"]) if start else ""
    dispositions = []
    writers = sender_threads(facts)
    channels = reply_channels(facts)
    opener = str(read_thread(home, thread).get("opened-by", ""))
    leftovers = None
    left: list[str] = []
    if cause == "failed":
        from .publish import unlanded
        left = unlanded(home, facts, strand, thread)
    for letter in letters:
        ident, author = letter.id, str(letter.data.get("from", ""))
        if cause != "failed":
            dispositions.append({"id": ident, "outcome": "retry", "reason": cause})
            continue
        failures = sum(f.kind == "attempt.ended" and f.data.get("cause") == "failed"
                       and ident in f.data.get("ids", ()) for f in facts)
        if author.startswith(("p-", "person:")) and failures == 0:
            dispositions.append({"id": ident, "outcome": "retry", "reason": "first failed attempt"})
            continue
        if author.startswith(("p-", "person:")):
            from .channels.relay import person_dm
            channel = channels.get(ident)
            person = "person:" + author.removeprefix("p-").removeprefix("person:")
            if channel and person_dm(home, channel) == person:
                first = str(letter.data.get("body", "")).splitlines()
                successor = notice(home, facts, "failed-message", ident,
                                   f"thread {thread} could not handle your letter: {first[0] if first else ''}\n{tail}",
                                   "send again, or leave it", about=letter, channel=channel,
                                   source_strand=strand, draft_only=True)
                if isinstance(successor, Fact):
                    dispositions.append({"id": ident, "outcome": "done",
                                         "reason": "failure message owed to author",
                                         "successor": successor.data})
                    continue
            dispositions.append({"id": ident, "outcome": "operator",
                                 "reason": f"failed attempt limit; no usable author channel: {failure or tail}"})
            continue
        if letter.data.get("notice"):
            dispositions.append({"id": ident, "outcome": "done", "reason": "passive notice shown"})
            continue
        # A bounce is forwarded along its recipient's opener chain, never back to its author.
        target = (opener.removeprefix("thread:") if opener.startswith("thread:") else None)
        if not is_notice(letter):
            target = writers.get(author) or target
        if target == thread:
            target = opener.removeprefix("thread:") if opener.startswith("thread:") else None
        if home.routable(target):
            if leftovers is None:
                leftovers = _leftovers(home, strand)
            what = (f"letter {ident} on thread {thread}: body exited {code}; "
                    f"README changed since start: {changed}; {tank or 'fuel unknown'}\n"
                    + "".join(line + "\n" for line in left) + f"{tail}{leftovers}")
            successor = notice(home, facts, "bounce", ident, what, "send again, or leave it",
                               thread=target, about=letter, source_strand=strand, draft_only=True)
            if isinstance(successor, Fact):
                dispositions.append({"id": ident, "outcome": "done", "reason": "bounced",
                                     "successor": successor.data})
                continue
        dispositions.append({"id": ident, "outcome": "operator", "reason":
                             f"failed attempt limit or no opener: {failure or tail}"})
    facts.append(_record(home, "attempt.ended", {
        "thread": thread, "strand": strand, "gen": gen, "ids": ids,
        "cause": cause, "code": code, "dispositions": dispositions,
        "evidence": {"tail": tail, "readme_changed": changed, "fuel": tank,
                     **({"unpublished": left} if left else {})},
    }, f"attempt.ended:{strand}:{gen}"))
    return facts


def _drained(home, facts, strand, thread, gen):
    facts.append(_record(home, "port.drained", {
        "thread": thread, "strand": strand, "gen": gen,
    }, f"port.drained:{strand}:{gen}"))
    return facts


def _drain_strand(home: Home, facts: list[Fact], strand: str) -> list[Fact]:
    """Take this strand's last port/out files. Empty is what port.drained attests."""
    room = home.room(strand)
    for _ in range(8):
        facts = _ingest(home, facts)
        leftover = [path for path in _port_files(room) if path.suffix != ".tmp"]
        if not leftover:
            for path in _port_files(room):
                try:
                    path.unlink()
                except OSError:
                    pass
            return facts
    for path in list(_port_files(room)):
        try:
            _reject(home, room, path, "still in port at drain", facts)
        except Exception:
            try:
                path.unlink()
            except OSError:
                pass
    return facts


def _reap(home: Home, bodies: dict[str, Body], facts: list[Fact]) -> list[Fact]:
    for strand, body in list(bodies.items()):
        if body_alive(home.room(strand)):
            continue
        # Wait only to collect a child we spawned, never to decide liveness.
        # An inherited body has no waitable child and its code is unknown.
        code = _collect_exit(body)
        # Drain the body's last boundary and answer before deciding what it wasted.
        facts = _drain_strand(home, facts, strand)
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
        from .usage import below_floor
        start = next((f for f in reversed(facts) if f.kind == "body.started"
                      and f.data.get("strand") == strand), None)
        paced = bool(start and below_floor(home, start.data["shell"]))
        shown = fold(facts).shown.get(strand, set())
        letters = [letter for letter in letters_for(facts, main_snapshot(home), body.thread) if letter.id in shown]
        cause = ("molted" if molted else
                 "settled" if read_thread(home, body.thread).get("status") in CLOSED else
                 "idle" if not letters else "paced" if paced else "failed")
        facts = _end_attempt(home, facts, strand, body.thread, body.gen, code, letters, cause)
        facts = _drain_strand(home, facts, strand)
        facts = _drained(home, facts, strand, body.thread, body.gen)
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
    channels = reply_channels(facts)
    from .threads import thread_table
    table = thread_table(home, facts)
    for thread, (strand, gen) in state.holder.items():
        if install_of(strand) != home.install_id():
            continue
        if strand not in bodies and (body_alive(home.room(strand))
                                    or (strand in pending and pending[strand].kind == "body.started")):
            bodies[strand] = Body(strand, thread, gen)
        if body_alive(home.room(strand)):
            settled = (closing_commit(home, thread)[1]
                       if read_thread(home, thread).get("status") in CLOSED else None)
            letters = letters_for(facts, main_snapshot(home), thread)
            text = render_boundary(strand, gen, thread, letters,
                                   state.shown.get(strand, set()), threads,
                                   fuel(home, shells[strand]) if shells.get(strand) else "", settled,
                                   channels, opened=table, plan=_plan(home, thread, facts),
                                   replies={k: f.reply_to for k, f in letter_owners(facts, main_snapshot(home)).items()})
            path = home.room(strand) / "port" / "in" / "boundary.md"
            if not path.is_file() or path.read_text() != text:
                atomic_write(path, text)
        elif strand in bodies:
            # _reap writes the same exit for a watched or inherited holder.
            continue
        elif strand in pending and pending[strand].kind in {"body.pending", "body.parked"}:
            if time.time() >= pending[strand].data.get("until", 0):
                facts = _start(home, strand, thread, gen, bodies, facts)
        else:
            # An orphaned attempt still owes the same end and drain observations.
            bodies[strand] = Body(strand, thread, gen)

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
    # The due letter joins this tick's route. The README read is the snapshot
    # just taken; the check does not walk git again.
    facts = fire_timers(home, facts, router.armed())
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
        body.close_log()


def _own_stop(stop):
    """SIGTERM ends only the loop; bodies keep their own sessions and files."""
    if stop is not None:
        return stop
    stop = threading.Event()
    if threading.current_thread() is threading.main_thread():
        def _stop(_signum, _frame, event=stop):
            event.set()
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
    return stop


def _hold_loop(home: Home, stop) -> int | None:
    """Serialize loops on one home; a stopped waiter leaves without ticking.

    Router leases identify installs, not processes. Two local loops share
    an install id and therefore both believe they own the router lease.
    A process lock also serializes ingest and reap, which ledger append's
    same-id no-op cannot protect. The OS releases it on a crash.
    """
    path = home.root / "loom" / "loop.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    waiting = False
    try:
        while not stop.is_set():
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except BlockingIOError:
                if not waiting:
                    _log(home, "loop: waiting for the other loom on this home")
                    waiting = True
                stop.wait(0.2)
    except BaseException:
        os.close(fd)
        raise
    os.close(fd)
    return None


def run(root: Path | str, *, adapter: str = "fake", core: str = "haiku",
        tick: float = 0.2, stop=None, install: str | None = None) -> None:
    if tick <= 0:
        raise ValueError("tick must be positive")
    stop = _own_stop(stop)
    home = Home(root, install=install)
    held = _hold_loop(home, stop)
    if held is None:
        return
    relay = None
    bodies: dict[str, Body] = {}
    try:
        if stop is not None and stop.is_set():
            return
        home.install_id()
        from .router import Router
        router = Router(home)
        relay = _arm_relay(home, router.config, stop)
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
        fcntl.flock(held, fcntl.LOCK_UN)
        os.close(held)


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

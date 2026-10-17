"""One process, one loop: ingest, route, rewrite boundaries, reap."""

from __future__ import annotations

import hashlib
import fcntl
import json
import os
import signal
import shlex
import shutil
import subprocess
import sys
import threading
import tempfile
import time
import traceback
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from brr.daemon2.facts import Fact

from .adapters import resolve
from .bodies import COMMANDS, Observation, decide, fact_for, state_of
from .home import Home, atomic_write, is_channel, mint, notice_id
from .ledger import LedgerConflict, append, read_facts
from .registry import KINDS
from .port import (
    fact_from_port, fact_from_taint, letter_attachments, letter_replies,
    parse_frontmatter, render_boundary,
)
from .project import projection_cache, fold, generation, is_notice, reply_channels, sender_threads
from .owners import letters_for, letter_owners
from .lifecycle import main_snapshot
from .timers import observe_due
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


_FORGE_TOKEN_VARS = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")


def _forge_env(room: Path, env: dict[str, str]) -> None:
    """Keep a configured forge identity isolated even when a shell bypasses the shim."""
    from .config import ConfigError, load_config, resolve_secret, secret_shell
    config = load_config(room.parent.parent) if room.parent.name == "rooms" else None
    mode = config.gh if config is not None else "auto"
    spec = config.forge_token if config is not None else None
    if mode == "auto" and not spec:
        return
    # An env source can itself name one of the machine token variables.
    source_var = spec.removeprefix("env: ") if spec and spec.startswith("env: ") else None
    source_token = env.get(source_var, "") if source_var in _FORGE_TOKEN_VARS else None
    for name in _FORGE_TOKEN_VARS:
        env.pop(name, None)
    gh_dir = room / "port" / "gh"
    gh_dir.mkdir(parents=True, exist_ok=True)
    env["GH_CONFIG_DIR"] = str(gh_dir)
    if mode == "auto" and spec:
        # Login profiles can put real gh ahead of our refreshing shim. An
        # exported token survives that PATH change; absent one, the empty
        # room config makes gh fail closed.
        try:
            env["GH_TOKEN"] = resolve_secret(spec)
        except ConfigError:
            _log(Home(room.parent.parent), "forge_token unavailable at body start; gh remains signed out")
        path = env.get("PATH", os.defpath)
        real_gh = shutil.which("gh", path=path)
        if real_gh is None:
            _log(Home(room.parent.parent), "forge_token configured but gh not found on body PATH; no gh shim")
            return
        real_gh = str(Path(real_gh).absolute())
        bin_dir = room / "port" / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        snippet = secret_shell(spec)
        if source_token is not None:
            env["BRNRD_GH_SECRET_SOURCE"] = source_token
            snippet = (f'{source_var}=${{{source_var}-$BRNRD_GH_SECRET_SOURCE}}; '
                       f'export {source_var}; ' + snippet)
        # Restore the original PATH while resolving, so command: gh auth token
        # calls the real gh rather than recursing through this shim.
        script = (
            "#!/bin/sh\n"
            f"GH_TOKEN=$(PATH={shlex.quote(path)} sh -c {shlex.quote(snippet)} 2>/dev/null) || "
            "{ echo 'gh: forge_token source failed' >&2; exit 1; }\n"
            "[ -n \"$GH_TOKEN\" ] || { echo 'gh: forge_token source gave nothing' >&2; exit 1; }\n"
            "export GH_TOKEN\n"
            "unset BRNRD_GH_SECRET_SOURCE\n"
            f"exec {shlex.quote(real_gh)} \"$@\"\n"
        )
        shim = bin_dir / "gh"
        shim.write_text(script, encoding="utf-8")
        shim.chmod(0o700)
        env["PATH"] = str(bin_dir) + os.pathsep + path
        return


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
    _forge_env(room, env)
    env["BRNRD_ROOM"] = str(room)
    env["BRNRD_WAKE"] = str(room / "port" / "wake.md")
    env["BRNRD_JACK"] = (f"{shlex.quote(sys.executable)} -m brr.loom.runtime jack "
                         f"--room {shlex.quote(str(room))}")
    wait = room / "port" / "wait"
    from .jack import HOLD, PARK
    env["BRNRD_WAIT"] = wait.read_text().strip() if wait.is_file() else f"{HOLD:g}"
    # The stop hook may sleep through an exhausted window, or hold for one
    # wait, whichever is longer; the adapter pins its timeout from this.
    env["BRNRD_STOP_TIMEOUT"] = str(int(max(float(env["BRNRD_WAIT"]), PARK)) + 60)
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


class WakeFailure(SelfError):
    """Preparation belongs to the home unless an input is intrinsically too big."""

    def __init__(self, why, letter_ids=()):
        super().__init__(why)
        self.letter_ids = tuple(letter_ids)


def _oversized_letters(clone, thread, owed, part, repos):
    """Prove input-local failure without interpreting a recipe's exit codes.

    The wake budget is the recipe contract. A large home plus small mail is
    still the home's fault. Even intrinsically oversized mail is not blamed
    when the very same recipe cannot run with an empty occasion.
    """
    raw = os.environ.get("LOOM_BUDGET_BYTES") or "24000"
    if not raw.isdigit():
        return ()
    limit = int(raw)
    large = tuple(f["id"] for f in json.loads(owed.read_text())
                  if len((f["body"] + "\n".join(f.get("attachments", ())) +
                          f.get("reply_context", "")).encode("utf-8")) > limit)
    if not large:
        return ()
    try:
        with tempfile.TemporaryFile(mode="w+") as output:
            ok = run_wake(clone, thread, loom_part=part, repos=repos,
                          stdout=output, stderr=output) == 0
    except Exception:
        return ()
    return large if ok else ()


def _failed_words(thread: str, body: str, tail: str) -> str:
    """What a person reads when a body could not take their letter."""
    first = next((line.strip() for line in body.splitlines() if line.strip()), "")
    quoted = first if len(first) <= 80 else first[:79].rstrip() + "…"
    why = next((line.strip() for line in reversed(tail.splitlines()) if line.strip()), "no reason was recorded")
    for prefix in ("SelfError: ", "wake exited 3: "):
        why = why.removeprefix(prefix)
    why = why if len(why) <= 400 else why[:399] + "…"
    return (f"I could not take your letter on thread {thread} (\u201c{quoted}\u201d); "
            f"two bodies tried and neither started.\nWhy: {why}")


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
        # A person reads words, not letter ids.
        lines.append(can if channel else f"{about.data['id']}: {can}")
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
    attachments = letter_attachments(home.root, facts)
    reply_context = letter_replies(facts)
    atomic_write(owed_path, json.dumps([dict(letter.data, attachments=attachments.get(letter.id, []),
                                           reply_context=reply_context.get(letter.id, ""),
                                           reply_to=letter_owners(facts, main_snapshot(home))[letter.id].reply_to)
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
            raise WakeFailure(f"wake exited {code}: {why.read().strip()}",
                              _oversized_letters(clone, thread, owed_path, part_path, repo_file))
        wake.seek(0)
        atomic_write(room / "port" / "wake.md", wake.read())
    from .threads import thread_table
    shown = fold(facts).shown.get(strand, set())
    atomic_write(
        room / "port" / "in" / "boundary.md",
        render_boundary(strand, gen, thread, letters, shown, threads,
                        fuel=fuel, channels=reply_channels(facts, home),
                        opened=thread_table(home, facts), plan=_plan(home, thread, facts),
                        replies={k: f.reply_to for k, f in letter_owners(facts, main_snapshot(home)).items()},
                        attachments=attachments, reply_context=reply_context),
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


def _ps_field(pid: int, field: str) -> str | None:
    """One ``ps`` column, or None when that pid is not a live process.

    ``lstart`` is kept as text. The same ``ps`` writes it and reads it back,
    so the locale's layout never has to be parsed.
    """
    try:
        out = subprocess.run(
            ["ps", "-ww", "-p", str(pid), "-o", f"{field}="],
            capture_output=True, text=True, timeout=2, check=False,
            # One layout for ``lstart`` whoever started the loop: a login
            # service and a terminal do not share a locale.
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    text = out.stdout.strip()
    return text or None


def _holds_body_file(room: Path, pid: int) -> bool:
    """True when ``pid`` still has this room's ``.body`` lock file open.

    Spawn passes that fd to the child and closes its own. ``exec`` keeps
    the fd, so a body whose command line no longer names the room (codex
    passes the wake text, not the path) is still identifiable. A pid the
    kernel has reused does not have the fd.
    """
    path = Path(room) / ".body"
    try:
        wanted = path.stat()
    except OSError:
        return False
    proc_fds = Path(f"/proc/{pid}/fd")
    if proc_fds.is_dir():
        try:
            entries = list(proc_fds.iterdir())
        except OSError:
            return False
        for fd in entries:
            try:
                got = fd.stat()
            except OSError:
                continue
            if got.st_ino == wanted.st_ino and got.st_dev == wanted.st_dev:
                return True
        return False
    try:
        out = subprocess.run(
            ["lsof", "-a", "-p", str(pid), "-t", "--", str(path)],
            capture_output=True, text=True, timeout=2, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return out.returncode == 0 and str(pid) in out.stdout.split()


def _proven_body(room: Path, pid: int, lstart: str,
                 proc: subprocess.Popen | None, *, alive: bool | None = None) -> bool:
    """True only when ``pid`` is still this room's body.

    An unreaped child this loop spawned cannot have had its pid reused.
    Any other pid must match the start time recorded at ``body.started``
    and must still hold ``.body`` or name the room on its command line.
    With no start time recorded, only holding ``.body`` counts: a command
    line that names the room is also what ``tail -f <room>/port/body.log``
    looks like. A miss is not a signal.
    """
    if not isinstance(pid, int) or pid <= 1 or not (body_alive(room) if alive is None else alive):
        return False
    if proc is not None and proc.pid == pid and proc.poll() is None:
        return True
    if not lstart:
        return _holds_body_file(room, pid)
    if _ps_field(pid, "lstart") != lstart:
        return False
    command = _ps_field(pid, "command")
    room_path = str(Path(room).resolve())
    if command and (room_path in command or str(room) in command):
        return True
    return _holds_body_file(room, pid)


def _signal_group(pid: int, sig: int) -> None:
    try:
        os.killpg(pid, sig)
    except OSError:
        return


def _end_group(pid: int) -> None:
    """SIGTERM, then SIGKILL if the group is still there."""
    _signal_group(pid, signal.SIGTERM)
    deadline = time.monotonic() + 0.4
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            break
        time.sleep(0.05)
    _signal_group(pid, signal.SIGKILL)


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
        if fold(current).holder.get(thread) != (strand, gen):
            return current
        if force:
            pending = pending_bodies(fold(current).accepted).get(strand)
            if not pending or pending.kind != "body.parked":
                return current
        return _advance_body(home, strand, thread, gen, bodies, current, choice=choice, force=force)


def _refuse_author(home: Home, facts: list[Fact], thread: str) -> bool:
    """True when this start must not run. The notice is the router's.

    ``route`` writes it before taking a lease. A pending or parked body is
    restarted from the body table and never enters ``route``, so the
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


def _room_kept(home: Home, strand: str) -> list[str]:
    """Commits and edits only this room still holds.

    The same inventory ``leftovers`` gives ``brnrd threads --left``. An
    inspection failure is a line. The room is not removed either way.
    """
    try:
        from .leftovers import leftovers
        found = leftovers(home, strand)
    except Exception as exc:
        return [f"inspection failed: {type(exc).__name__}: {exc}"]
    lines = []
    for item in found:
        if item.error:
            lines.append(f"{item.path}: {item.error}")
        lines.extend(f"{sha} {subject}" for sha, subject in item.commits)
        lines.extend(f"edit {path}" for path in item.edits)
    return lines


def _end_attempt(home, facts, strand, thread, gen, code, letters, cause, failure="",
                 failure_origin="", failure_letters=(), repair_thread=None):
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
    channels = reply_channels(facts, home)
    opener = str(read_thread(home, thread).get("opened-by", ""))
    leftovers = None
    left: list[str] = []
    kept: list[str] = []
    if cause in {"failed", "stopped"}:
        from .publish import unlanded
        left = unlanded(home, facts, strand, thread)
    if cause == "stopped":
        kept = _room_kept(home, strand)
    stopped_why = ""
    if cause == "stopped":
        stopped_why = next(
            (str(f.data.get("why") or "") for f in reversed(facts)
             if f.kind == "body.stopped" and f.data.get("strand") == strand
             and f.data.get("gen") == gen),
            "",
        )
    if (cause == "failed" and failure_origin == "home"
            and (not letters or any(f.id not in failure_letters for f in letters))):
        # Preparation/spawn failed, or the holding install disappeared. No
        # running reader is available here to repair the condition.
        clone = room / "self"
        try:
            revision = git(clone, "rev-parse", "HEAD").stdout.strip()
        except (SelfError, OSError):
            revision = "unknown"
        notice(home, facts, "self-refusal", f"{thread}:{revision}",
               f"thread {thread} could not start from its own state: {failure or tail}",
               f"repair the home's wake recipe or context; its letters remain owed on {thread}",
               thread=repair_thread)
    for letter in letters:
        ident, author = letter.id, str(letter.data.get("from", ""))
        if cause == "failed" and failure_origin == "home" and ident not in failure_letters:
            dispositions.append({"id": ident, "outcome": "retry", "reason": "home-caused failure"})
            continue
        if cause != "failed":
            dispositions.append({"id": ident, "outcome": "retry", "reason": cause})
            continue
        failures = sum(f.kind == "attempt.ended" and f.data.get("cause") == "failed"
                       and any(d.get("id") == ident and d.get("reason") != "home-caused failure"
                               for d in f.data.get("dispositions", ())) for f in facts)
        if author.startswith(("p-", "person:")) and failures == 0:
            dispositions.append({"id": ident, "outcome": "retry", "reason": "first failed attempt"})
            continue
        if author.startswith(("p-", "person:")):
            from .connector import person_dm
            channel = channels.get(ident)
            person = "person:" + author.removeprefix("p-").removeprefix("person:")
            if channel and person_dm(home, channel) == person:
                successor = notice(home, facts, "failed-message", ident,
                                   _failed_words(thread, str(letter.data.get("body", "")), tail),
                                   "Send it again, or leave it.", about=letter, channel=channel,
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
                     **({"unpublished": left} if left else {}),
                     **({"kept": kept} if kept else {}),
                     **({"why": stopped_why} if cause == "stopped" else {})},
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


def pending_bodies(facts: list[Fact]) -> dict[str, Fact]:
    """The latest body fact for each strand, used by attention."""
    return {str(f.data["strand"]): f for f in facts
            if f.kind.startswith("body.") and f.data.get("strand")}


def _observe_body(home, facts, strand, thread, gen, bodies, *, choice=None, force=False):
    from .config import load_config
    from .lifecycle import CLOSED, closing_commit, read_thread
    from .usage import reading
    from types import SimpleNamespace
    now, config, room = time.time(), load_config(home.root), home.room(strand)
    alive = body_alive(room)
    state = state_of(facts, strand, gen, alive=alive)
    lease = fact_for(facts, "lease", strand, gen).data
    start = fact_for(facts, "body.started", strand, gen)
    closed = read_thread(home, thread).get("status") in CLOSED
    default = choice or ("fake", "haiku")
    shell, core = lease.get("shell", default[0]), lease.get("core", default[1])
    after = max((datetime.fromisoformat(f.at).timestamp() for f in facts
                 if f.data.get("strand") == strand and f.kind in {"body.exited", "body.parked"}), default=0)
    gauge = ({"windows": []} if force or state.name == "parked" and now < state.until
             else reading(home, shell, 0 if alive else after))
    empty = next((w for w in gauge["windows"] if w["left_pct"] <= config.hold_floor_pct), None) if gauge else None
    until = 0
    if empty:
        from .usage import dry_until
        until = dry_until(home, shell) or now + 900
        from .connector import person_dm
        channels = reply_channels(facts, home)
        what = (f"Out of {shell} {empty['name']} fuel ({empty['left_pct']:g}% left) "
                f"until about {datetime.fromtimestamp(until).astimezone():%H:%M}. "
                f"Your letter is kept on thread {thread} and is answered when it refills.")
        for letter in letters_for(facts, main_snapshot(home), thread):
            author = str(letter.data.get("from", ""))
            if not author.startswith(("p-", "person:")):
                continue
            channel = channels.get(letter.id)
            person = "person:" + author.removeprefix("p-").removeprefix("person:")
            if channel and person_dm(home, channel) == person:
                notice(home, facts, "parked", letter.id, what, "Nothing to do.",
                       about=letter, channel=channel, passive=True)
    pid = int(start.data.get("pid") or 0) if start else 0
    proven = None
    # A stop on record with the body still alive is a signal that was never
    # sent or did not land: prove again, every tick, until the lock is gone.
    if alive and (state.name == "stopping"
                  or closed and state.name == "leaving" and now >= state.deadline):
        body = bodies.get(strand)
        proven = _proven_body(room, pid, str(start.data.get("lstart") or "") if start else "",
                              body.proc if body else None, alive=alive)
    fuel = "fuel " + shell + " " + " ".join(f"{w['name']} {w['left_pct']:g}%" for w in gauge["windows"]) if gauge and gauge["windows"] else ""
    context = SimpleNamespace(thread=thread, shell=shell, core=core, pid=pid, empty=empty, fuel=fuel,
                              why=(closing_commit(home, thread)[1] or f"thread {thread} settled") if closed else "", failure="", repos=lease.get("repos"))
    return Observation(strand, gen, alive, closed, "refreshing" if gauge is None else "empty" if empty else "ok",
                       now, proven, until=until, force=force, stop_grace=config.stop_grace), context


def _boundary(home, facts, strand, gen, context):
    from .threads import thread_table
    main, state = main_snapshot(home), fold(facts)
    text = render_boundary(
        strand, gen, context.thread, letters_for(facts, main, context.thread),
        state.shown.get(strand, set()), sender_threads(facts), context.fuel,
        context.why or None, reply_channels(facts, home),
        opened=thread_table(home, facts), plan=_plan(home, context.thread, facts),
        replies={k: f.reply_to for k, f in letter_owners(facts, main).items()},
        attachments=letter_attachments(home.root, facts),
        reply_context=letter_replies(facts),
    )
    path = home.room(strand) / "port/in/boundary.md"
    if not path.is_file() or path.read_text() != text:
        atomic_write(path, text)


@dataclass
class _Run:
    """What the hands of one decision share. Nothing here outlives it."""
    home: Home
    bodies: dict
    facts: list
    o: Observation
    c: object
    room: Path
    values: dict
    letters: list = field(default_factory=list)
    folder: Path | None = None
    proc: subprocess.Popen | None = None


# A hand performs one command. It returns None to go on, False to drop the
# rest of the decision, or the observation the table is asked again with.

def _failed(x, exc):
    x.c.failure = f"{type(exc).__name__}: {exc}"
    x.c.failure_origin = "home"
    x.c.failure_letters = list(getattr(exc, "letter_ids", ()))
    return replace(x.o, failure=x.c.failure)


def _hand_prepare(x, command, step):
    c = x.c
    if _refuse_author(x.home, x.facts, c.thread):
        return False
    try:
        x.folder = resolve(x.home, c.shell)
        x.letters = letters_for(x.facts, main_snapshot(x.home), c.thread)
        x.room = _prepare(x.home, x.o.strand, c.thread, x.o.gen, x.letters, sender_threads(x.facts), x.facts,
                          (x.folder / "jack").read_text().strip(), fuel=c.fuel, repos=c.repos)
        policy = _policy(x.home, c.thread)
        if policy is not None:
            atomic_write(x.room / "port/policy", policy + "\n")
    except Exception as exc:
        return _failed(x, exc)


def _hand_show(x, command, step):
    x.facts.append(_record(x.home, step.records, {"strand": x.o.strand, "gen": x.o.gen, "ids": [f.id for f in x.letters]},
                           f"shown:wake:{x.o.strand}:{x.o.gen}"))


def _hand_spawn(x, command, step):
    c = x.c
    x.values["readme"] = _readme(x.room, c.thread)
    try:
        x.proc, log = _spawn(x.room, [str(x.folder / "run"), str(x.room), c.core], c.shell, c.thread)
    except Exception as exc:
        return _failed(x, exc)
    x.bodies[x.o.strand] = Body(x.o.strand, c.thread, x.o.gen, x.proc, log)
    x.values["pid"] = x.proc.pid


def _hand_record(x, command, step):
    """Write a body fact: its fields are the registry's, its id follows ``once``."""
    c, kind = x.c, KINDS[step.records]
    why = {"body.stopped": c.why, "body.exited": c.failure,
           "body.unidentified": f"pid {c.pid} could not be proven to be the body on thread {c.thread}"}
    values = {**x.values, "deadline": command.deadline, "why": why.get(kind.kind)}
    data = {name: values[name] for name in kind.names() if name in kind.required() or values.get(name)}
    ident = f"{kind.kind}:{x.o.strand}:{x.o.gen}" + ("" if kind.once else f":{mint(6)}")
    x.facts.append(_record(x.home, kind.kind, data, ident))


def _hand_started(x, command, step):
    c = x.c
    x.values["lstart"] = _ps_field(x.proc.pid, "lstart") or ""
    _hand_record(x, command, step)
    _log(x.home, f"start {x.o.strand} thread {c.thread} gen {x.o.gen} pid {x.proc.pid} {c.shell}/{c.core}")


def _hand_signal(x, command, step):
    _end_group(x.c.pid)
    if not body_alive(x.room):
        return replace(x.o, alive=False)


def _hand_collect(x, command, step):
    body = x.bodies.pop(x.o.strand, None)
    if body:
        x.values["code"] = _collect_exit(body)
        body.close_log()


def _hand_drain(x, command, step):
    x.facts = _drain_strand(x.home, x.facts, x.o.strand)


def _hand_end(x, command, step):
    from .bodies import exit_cause
    o, c = x.o, x.c
    exited = fact_for(x.facts, "body.exited", o.strand, o.gen)
    if exited and exited.data.get("code") not in (None, 0) and fact_for(x.facts, "body.started", o.strand, o.gen):
        # The cache that admitted this body cannot explain its failure. Wait
        # for a post-exit observation before retiring any of its letters.
        from .usage import reading, dry_until
        from .config import load_config
        gauge = reading(x.home, c.shell, datetime.fromisoformat(exited.at).timestamp(), fresh=True)
        if gauge is None:
            return False
        if any(w["left_pct"] <= load_config(x.home.root).hold_floor_pct for w in gauge["windows"]):
            o = replace(o, gauge="empty", until=dry_until(x.home, c.shell) or 0)
    shown = fold(x.facts).shown.get(o.strand, set())
    letters = [f for f in letters_for(x.facts, main_snapshot(x.home), c.thread) if step.arg == "failed" or f.id in shown]
    x.facts = _end_attempt(x.home, x.facts, o.strand, c.thread, o.gen, exited.data.get("code") if exited else None,
                           letters, step.arg or exit_cause(x.facts, o, owed=bool(letters)), c.failure,
                           (exited.data if exited else x.values).get("failure_origin", ""),
                           (exited.data if exited else x.values).get("failure_letters", ()))


def _hand_drained(x, command, step):
    if not fact_for(x.facts, step.records, x.o.strand, x.o.gen):
        x.facts = _drained(x.home, x.facts, x.o.strand, x.c.thread, x.o.gen)


def _hand_release(x, command, step):
    x.facts = _release(x.home, x.facts, x.o.strand, x.c.thread, x.o.gen)


HANDS = {
    "prepare": _hand_prepare, "show": _hand_show, "spawn": _hand_spawn, "started": _hand_started,
    "record": _hand_record, "signal": _hand_signal, "collect": _hand_collect, "drain": _hand_drain,
    "end": _hand_end, "drained": _hand_drained, "release": _hand_release,
}


def _execute_body(home, bodies, facts, o, c):
    """Perform what the table decided: each command's declared hand, in order."""
    empty = c.empty or {}
    x = _Run(home, bodies, facts, o, c, home.room(o.strand), {
        "strand": o.strand, "gen": o.gen, "shell": c.shell, "core": c.core, "pid": c.pid, "code": None,
        "failure_origin": getattr(c, "failure_origin", ""),
        "failure_letters": getattr(c, "failure_letters", ()),
        "window": empty.get("name"), "left_pct": empty.get("left_pct"), "until": o.until})
    commands = decide(facts, [o])
    if o.alive:
        _boundary(home, facts, o.strand, o.gen, c)
    for command in commands:
        step = COMMANDS[command.name]
        again = HANDS[step.hand](x, command, step)
        if again is False:
            break
        if again is not None:
            return _execute_body(home, bodies, x.facts, again, c)
    return x.facts


def _advance_body(home, strand, thread, gen, bodies, facts, *, choice=None, force=False):
    o, c = _observe_body(home, facts, strand, thread, gen, bodies, choice=choice, force=force)
    return _execute_body(home, bodies, facts, o, c)


def _advance_bodies(home, bodies, facts, choice):
    from .router import install_of
    leases = {f.data["strand"]: f for f in facts if f.kind == "lease"}
    holders = {strand for strand, gen in fold(facts).holder.values()}
    for strand in holders | bodies.keys():
        if install_of(strand) == home.install_id():
            d = leases[strand].data
            if not state_of(facts, strand, d["gen"]).released:
                facts = _start(home, strand, d["thread"], d["gen"], bodies, facts, choice)
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
    facts = router.reader.read(home)
    if router.projection_revision != router.reader.revision:
        router.projections.clear()
        router.projection_revision = router.reader.revision
    with projection_cache(router.projections):
        facts = router.begin_tick(facts)
        facts = _ingest(home, facts, router.config.router_ttl)
        facts = _advance_bodies(home, bodies, facts, (adapter, core))
        # Time enters the owner table once, using only the facts already held.
        facts = observe_due(home, facts, router.armed())
        facts = router.route(facts, bodies, (adapter, core))
        facts = router.speak_out(facts)


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


def _source_fingerprint():
    root = Path(__file__).parent
    return hashlib.sha256(repr([
        (str(p.relative_to(root)), p.stat().st_mtime_ns)
        for p in sorted(root.rglob("*")) if p.is_file() and "__pycache__" not in p.parts
    ]).encode()).hexdigest()


def run(root: Path | str, *, adapter: str = "fake", core: str = "haiku",
        tick: float = 0.2, stop=None, install: str | None = None) -> None:
    if tick <= 0:
        raise ValueError("tick must be positive")
    stop = _own_stop(stop)
    home = Home(root, install=install)
    held = _hold_loop(home, stop)
    if held is None:
        return
    inputs = None
    bodies: dict[str, Body] = {}
    try:
        if stop is not None and stop.is_set():
            return
        home.install_id()
        from .config import ConfigError, load_config, tick_config
        from . import usage
        config = load_config(home.root)
        fingerprint = _source_fingerprint()
        next_usage = 0
        from .router import Router
        router = Router(home, config)
        from .connector import Inputs
        inputs = Inputs(home)
        while not (stop is not None and stop.is_set()):
            try:
                config = load_config(home.root)
            except ConfigError as exc:
                _log(home, str(exc))
                notice(home, read_facts(home), "config-error", str(home.root), str(exc),
                       "fix loom/config.toml; the loop is using its last valid config")
            with tick_config(home.root, config):
                router.config = config
                if time.monotonic() >= next_usage:
                    for shell in usage.shells(home):
                        usage.reading(home, shell)
                    next_usage = time.monotonic() + usage.REFRESH_INTERVAL
                try:
                    inputs.step()
                except Exception:
                    _log(home, traceback.format_exc().rstrip())
                try:
                    tick_once(home, bodies, adapter, core, router)
                except Exception:
                    _log(home, traceback.format_exc().rstrip())
            if _source_fingerprint() != fingerprint:
                _log(home, "runtime source changed; re-executing loom")
                inputs.close()
                _shutdown(bodies)
                os.execv(sys.executable, [sys.executable, *sys.orig_argv[1:]])
                return
            if _pause(stop, tick):
                break
    finally:
        _shutdown(bodies)
        if inputs is not None:
            inputs.close()
        fcntl.flock(held, fcntl.LOCK_UN)
        os.close(held)

"""One process, one loop: ingest, route, rewrite boundaries, reap."""

from __future__ import annotations

import hashlib
import fcntl
import functools
import json
import os
import re
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

from .adapters import body_label, resolve
from .bodies import COMMANDS, TABLE, Observation, decide, fact_for, row_for, state_of
from .faces import propose, proposal_line
from .health import Health, degraded_line
from .library import problem_line
from .since import since_gone
from .update import Updater, moved_line
from .home import Home, atomic_write, is_channel, mint, notice_id
from .ledger import LedgerView, kind_facts, LedgerConflict, append, read_facts
from .registry import KINDS
from .port import (
    fact_from_port, fact_from_taint, letter_attachments, letter_replies,
    parse_boundary, parse_frontmatter, render_boundary,
)
from .project import projection_cache, fold, generation, is_notice, reply_channels, sender_threads
from .owners import active_owners, letters_for, letter_owners
from .lifecycle import main_snapshot
from .timers import observe_due
from .selfrepo import (
    AUTHOR_EMAIL, AUTHOR_NAME, SelfError, _env_without_pin, git,
    ensure_room, forge_threads, repaired_line, run_wake, seed_root,
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
    # The whole wake for any body; the core and the occasion apart for a body
    # with a system slot (_prepare).
    env["BRNRD_WAKE"] = str(room / "port" / "wake.md")
    env["BRNRD_CORE"] = str(room / "port" / "core.md")
    env["BRNRD_OCCASION"] = str(room / "port" / "occasion.md")
    # Launchers name the shim, never this interpreter (entry.py); a body types
    # ``brnrd runtime``, so the shim's directory leads its PATH.
    from . import entry
    env["BRNRD_JACK"] = entry.command("jack", "--room", str(room))
    env["PATH"] = str(entry.shim().parent) + os.pathsep + env.get("PATH", os.defpath)
    wait = room / "port" / "wait"
    from .jack import PARK
    env["BRNRD_WAIT"] = wait.read_text().strip() if wait.is_file() else f"{PARK:g}"
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


def _run_recipe(clone, thread, owed_path, part_path, repo_file, core_path, recipe=None):
    with tempfile.TemporaryFile(mode="w+") as wake, tempfile.TemporaryFile(mode="w+") as why:
        code = run_wake(clone, thread, owed_path, loom_part=part_path, repos=repo_file,
                        core=core_path, stdout=wake, stderr=why, recipe=recipe)
        wake.seek(0)
        why.seek(0)
        return code, wake.read(), why.read().strip()


def _build_wake(home, strand, gen, clone, thread, rows, part, owed_path, part_path, repo_file, core_path):
    """Run the recipe; a home that cannot wake still wakes when it can be made to.

    The resident is who repairs its home, and it can only do that awake. So a
    broken recipe is replaced by the packaged one, and owed letters that do not
    fit are left for the first boundary, which shows unread letters whole. Only
    a wake that cannot be built at all (no recipe fits even an empty occasion,
    or one letter is larger than the budget) is refused. ``owed.json`` ends as
    the letters the wake carried.
    """
    def attempt(carried, text, recipe):
        atomic_write(owed_path, json.dumps(carried))
        atomic_write(part_path, text)
        return _run_recipe(clone, thread, owed_path, part_path, repo_file, core_path, recipe)

    from .converge import WAKE_FALLBACK, record
    recipe = None
    try:
        code, occasion, why = attempt(rows, part, None)
        broken = f"wake exited {code}: {why}"
    except SelfError as exc:  # a recipe that is missing or not executable
        code, occasion, why, broken = -1, "", str(exc), str(exc)
    if code not in (0, 3):
        recipe = seed_root() / "core" / "loom" / "wake"
        _log(home, f"start {strand}: the self's core/loom/wake failed ({broken}); "
                   "waking with the packaged recipe")
        part = (f"your self's core/loom/wake failed ({broken[:400]}); brnrd's packaged recipe "
                "built this wake instead; repair core/loom/wake and land it with send-self "
                "before other work. ") + part
        record(home, WAKE_FALLBACK, strand, gen, "recipe", {"want": "recipe", "error": broken[:400]})
        code, occasion, why = attempt(rows, part, recipe)
    if code == 3 and rows and not _oversized_letters(clone, thread, owed_path, part_path,
                                                     repo_file, recipe=recipe):
        best = None
        low, high = 0, len(rows) - 1
        while low <= high:
            mid = (low + high) // 2
            text = _deferred_line(rows[mid:], len(rows)) + part
            result = attempt(rows[:mid], text, recipe)
            if result[0] == 0:
                best, low = (mid, text, result), mid + 1
            else:
                high = mid - 1
        if best is not None:
            mid, text, (code, occasion, why) = best
            atomic_write(owed_path, json.dumps(rows[:mid]))
            atomic_write(part_path, text)
            _log(home, f"start {strand}: the wake carries {mid} of {len(rows)} owed letters; "
                       "the rest arrive at the first boundary")
            record(home, WAKE_FALLBACK, strand, gen, "letters", {
                "want": "letters", "carried": mid,
                "deferred": [str(row.get("id")) for row in rows[mid:]]})
            return occasion
        attempt(rows, part, recipe)
    if code != 0:
        raise WakeFailure(f"wake exited {code}: {why}",
                          _oversized_letters(clone, thread, owed_path, part_path, repo_file,
                                             recipe=recipe))
    return occasion


def _deferred_line(rest, total):
    ids = ", ".join(str(row.get("id")) for row in rest)
    return (f"the wake had room for {total - len(rest)} of {total} owed letters; the other "
            f"{len(rest)} ({ids}) arrive whole at your first tool boundary: read them "
            "before you act on the ones below. ")


def _oversized_letters(clone, thread, owed, part, repos, recipe=None):
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
                          stdout=output, stderr=output, recipe=recipe) == 0
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
    boundary until a body settles it; a ``passive`` one is done once shown. Its id is derived from the condition:
    a condition met on every tick writes one letter. It goes to ``thread``
    when a body can run there, else to the home thread. The last lines always name
    the handle. ``stands`` ties it to ``about``: the two settle together.
    """
    ident = notice_id(kind, subject)
    if (ident in facts.by_id if isinstance(facts, LedgerView) else any(fact.id == ident for fact in facts)):
        return False
    from .labels import Label, SELF_AUDIENCE, _sender_label, join, strand_label
    label = Label(taint, SELF_AUDIENCE)
    if source_strand is not None:
        label = join(label, strand_label(facts, source_strand, self_root=home.root / "self"))
    lines = [what]
    data: dict = {"id": ident, "from": "loom",
                  "to": channel or f"thread:{thread if home.routable(thread) else home.main_thread()}"}
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
        lines.append("passive: shown once, owed by nobody" if passive
                     else f"settle this notice with a reply or a note, re: {ident}")
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
    ids = facts.by_id if isinstance(facts, LedgerView) else {fact.id for fact in facts}
    for thread in threads:
        data = read_thread(home, thread)
        if data.get("status") not in CLOSED:
            continue
        commit, why, writer = closing_commit(home, thread)
        ident = f"thread.closed:{thread}:{commit}"
        if ident not in ids:
            facts.append(_record(home, "thread.closed", {
                "thread": thread, "status": data["status"], "why": why, "commit": commit,
            }, ident))
            if not isinstance(facts, LedgerView):
                ids.add(ident)
        opener = str(data.get("opened-by", ""))
        if opener.startswith("thread:") and writers.get(writer) != opener.removeprefix("thread:"):
            notice(home, facts, "closed", ident, f"thread {thread} {data['status']}: {why}",
                   "the thread is closed", thread=opener.removeprefix("thread:"), passive=True)
    return facts


def _next_gen(facts: list[Fact], thread: str) -> int:
    gen = 0
    for fact in kind_facts(facts, "lease", "released"):
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
    lease = next((f for f in reversed(facts) if f.kind == "lease"
                  and f.data.get("strand") == strand and f.data.get("gen") == gen), None)
    parent = lease.data.get("parent") if lease else None
    if not isinstance(parent, str) or not parent:
        parent = None
    repaired: list[Fact] = []

    def ensure() -> None:
        _clone, repair = ensure_room(home.root, strand, gen)
        if repair is not None:
            _log(home, f"start {strand}: {repaired_line(repair)}")
            repaired.append(repair)

    from .converge import converge, driver_want, entry_want, port_want, room_wants
    wait_src = home.thread_dir(thread) / "wait"
    wait = wait_src.read_text().strip() if wait_src.is_file() else None
    fell = converge(home, strand, gen, [entry_want(), *room_wants(home, strand, gen, parent, facts, ensure),
                                        port_want(room, wait), driver_want(room / "self")])
    for fact in fell:
        if fact.data.get("source") not in (f"strand/{parent}", None):
            _log(home, f"start {strand}: seeded from {fact.data['source']} "
                       f"({fact.data.get('error', 'no error')})")
    facts = [*facts, *repaired, *fell]
    clone = room / "self"
    owed_path = room / "port" / "owed.json"
    attachments = letter_attachments(home.root, facts)
    reply_context = _reply_context(facts, thread, letters, room)
    rows = [dict(letter.data, attachments=attachments.get(letter.id, []),
                 reply_context=reply_context.get(letter.id, ""),
                 reply_to=letter_owners(facts, main_snapshot(home))[letter.id].reply_to)
            for letter in letters]
    atomic_write(owed_path, json.dumps(rows))
    part_path = room / "port" / "loom.md"
    inherited = _read_by_predecessors(facts, thread, strand, letters, threads)
    gone = since_gone(facts, strand, gen, thread, threads, home.install_id())
    from .reap import kept_line
    gone += kept_line(home, thread, threads)
    forge = forge_threads(home.root / "self")
    part = gone + inherited + (
        f"you are strand {strand} on thread {thread}"
        + (f" ({forge}/{thread})" if forge else "") + "; letters arrive at your "
        "tool boundaries, each line naming its reply address; answer with "
        "`brnrd runtime send --re <id> --to <reply address> \"…\"`; "
        "where the address is `-` nobody is waiting for words: settle the letter with "
        f"`brnrd runtime send --re <id> --to thread:{thread} --note "
        "\"<what you did>\"`; if you commit in this self clone, land it with "
        "`brnrd runtime send-self --room .`; you will forget what is "
        "not written: keep this thread's README true (the spec and the decisions); "
        "what is left on a wish lives on the wish: `brnrd runtime wish "
        "<w-id> --left \"<step>\" --left \"<step>\"`, the whole list, latest wins "
        "(`--left same` when nothing moved), and the stop asks for it once a wish "
        "moved; progress is the wishes; land the edit with send-self. "
        "When your context grows "
        "heavy hand over with `brnrd runtime molt --why \"<where "
        "things stand>\"`; `brnrd runtime settle <thread> --why "
        "\"…\"` closes a thread whose work is done; "
        "a letter from `loom` is a notice of something the loom could not settle, "
        "yours to deal with: its last lines name what settles it "
        "(core/loom/README.md, Notices); stamp what someone wants with "
        "`send --wish` (core/loom/README.md, Wishes), and open a thread only for "
        "a wish: `send --to thread:<new> --wish <w-id>` or `--wish new:\"<line>\"`; "
        "a `face?` line proposes a face for a fact you just met: "
        "`brnrd runtime face take` wears it, `face <face>` wears another, "
        "and doing nothing lets it lapse; "
        + ("call `$BRNRD_JACK --event start` now, `$BRNRD_JACK --event post` between "
           "steps and `$BRNRD_JACK --event stop` before ending; read each returned boundary\n"
           if jack == "hand" else
           "before you stop with wishes open, say how this body ends: a result (`wish <w-id> --result`), "
           "an `ask`, a park with its reason (`wish <w-id> --read \"waiting: …\"`), or `settle`; "
           "then stop, and the jack holds you while letters may come\n")
    )
    atomic_write(part_path, part)
    if repos is None:
        from .adapters import repos as seat_repos, seat_text
        repos = seat_repos(seat_text(home, thread), home)
    from .repos import PUBLISH_HINT, prepare_repos
    repo_text, failures = prepare_repos(home, strand, thread, repos, parent)
    if any((room / name / ".git").exists() for name, _ in repos):
        repo_text += PUBLISH_HINT
    from . import library
    repo_text += library.wake_text(home)
    shelf = library.entries(home)[0]
    library.write_room_list(room, shelf)
    for trouble in library.link(room, shelf):
        repo_text += trouble + "\n"
    repo_file = None
    if failures:
        _log(home, f"start {strand}: {'; '.join(failures)}")
    from .converge import FALLBACK, record
    for line in failures:
        name = line.split(":", 1)[0].removeprefix("repo ")
        record(home, FALLBACK, strand, gen, f"repo:{name}", {
            "want": f"repo:{name}", "expected": f"a fetched mirror and loom/{thread}", "got": line})
    if repo_text:
        repo_file = room / "port" / "repos"
        atomic_write(repo_file, repo_text)
    core_path = room / "port" / "core.md"
    occasion = _build_wake(home, strand, gen, clone, thread, rows, part, owed_path, part_path,
                           repo_file, core_path)
    # The core rides in a body's system slot, where automated turns and
    # compaction do not wear it down. wake.md stays whole, so a body that
    # reads only BRNRD_WAKE never loses it. An older recipe writes no core.
    core = core_path.read_text() if core_path.is_file() else ""
    atomic_write(core_path, core)
    atomic_write(room / "port" / "occasion.md", occasion)
    atomic_write(room / "port" / "wake.md", core + occasion)
    from .threads import thread_table
    shown = fold(facts).shown.get(strand, set())
    atomic_write(
        room / "port" / "in" / "boundary.md",
        render_boundary(strand, gen, thread, letters, shown, threads,
                        fuel=fuel, channels=reply_channels(facts, home),
                        opened=thread_table(home, facts), plan=_plan(home, thread, facts),
                        replies={f.letter.id: f.reply_to for f in active_owners(facts, main_snapshot(home))},
                        attachments=attachments, reply_context=reply_context,
                        repaired=_repaired(facts, strand, gen),
                        degraded=degraded_line(facts, home.install_id()),
                        library=problem_line(facts, home.install_id()),
                        moved=moved_line(facts, strand, gen, home.install_id()),
                        face=proposal_line(propose(facts, strand, gen, thread)),
                        left=_left(facts, thread, strand),
                        **_budget(home, thread, facts)),
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
           repos: list[tuple[str, str]] | None = None, reader=None) -> list[Fact]:
    """Serialize starts with the operator's explicit gauge override."""
    room = home.room(strand)
    room.mkdir(parents=True, exist_ok=True)
    with (room / ".starting").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return facts
        # Still refresh under the start lock: another caller may have moved
        # the lease. The loop reader preserves unchanged fact identities.
        current = reader.refresh(home) if reader is not None else read_facts(home)
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
        int(item.data["gen"]) for item in kind_facts(facts, "router")
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
        paths = _port_files(room)
        if not paths:
            continue
        gen = _strand_gen(facts, strand)
        for path in paths:
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
    """The thread's wishes, delivered or accepted over all; no wishes, no plan."""
    from .owners import unanswered_by_thread
    from .wishes import progress, told
    main, plan = main_snapshot(home), progress(told(facts), thread)
    if plan is None and main.get(thread, {}).get("plan_rows") is None:
        # Neither wishes nor a Plan: the thread is not asked for one.
        return ""
    unanswered = unanswered_by_thread(facts, main).get(thread, ())
    parts = [f"plan {plan['done']}/{plan['total']}"] if plan else []
    if unanswered:
        parts.append("unanswered: " + ", ".join(unanswered))
    if plan and plan.get("next"):
        parts.append(f"next: {plan['next'][:60]}")
    return " · ".join(parts)


def _left(facts, thread: str, strand: str) -> str:
    """The wishes this body's stop will ask to restate, derived at every render."""
    from .wishes import left_line, owed_lefts, told
    return left_line(owed_lefts(told(facts), facts, thread, strand))


def _reply_context(facts, thread: str, letters, room: Path | None = None) -> dict[str, str]:
    """Reply context, and a steers? line under each undisposed person letter."""
    context = dict(letter_replies(facts))
    from .dispositions import disposed, is_person
    person = [letter for letter in letters if is_person(letter)]
    if not person:
        return context
    from .wishes import steers_line, told
    found, done = told(facts), disposed(facts, room)
    for letter in person:
        ident = str(letter.data.get("id", letter.id))
        line = "" if ident in done else steers_line(found, thread, ident)
        if line:
            context[letter.id] = "\n".join(filter(None, (context.get(letter.id, ""), line)))
    return context


def _budget(home: Home, thread: str, facts) -> dict[str, str]:
    """``spent/allocation`` for the status line, and the warning once past it."""
    from .config import load_config
    from .lifecycle import read_thread
    from .spend import allocation, budget_text, over_line, thread_spent
    spent = thread_spent(home, facts, thread)
    if spent is None:
        return {}
    opener = str(read_thread(home, thread).get("opened-by", ""))
    allocated = allocation(facts, thread, opener, sender_threads(facts),
                           int(load_config(home.root).thread_budget)) if opener.startswith("thread:") else None
    over = over_line(thread, opener, spent, allocated) if allocated and spent > allocated else ""
    return {"budget": budget_text(spent, allocated), "over": over}


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
        command = f"brnrd runtime send-self --room {shlex.quote(str(clone))}"
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


def _home_streak(facts, thread: str, gen: int) -> int:
    """The first generation of the home-caused failures that end at ``gen``."""
    ends = sorted((int(f.data["gen"]), f) for f in kind_facts(facts, "attempt.ended")
                  if f.data.get("thread") == thread
                  and int(f.data["gen"]) < gen)
    start = gen
    for prior, fact in reversed(ends):
        if not fact.data.get("evidence", {}).get("home_failure"):
            break
        start = prior
    return start


def _end_attempt(home, facts, strand, thread, gen, code, letters, cause, failure="",
                 failure_origin="", failure_letters=(), repair_thread=None,
                 await_repair=True):
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
    retry_revision = None
    if (cause == "failed" and failure_origin == "home"
            and (not letters or any(f.id not in failure_letters for f in letters))):
        # Preparation/spawn failed, or the holding install disappeared. No
        # running reader is available here to repair the condition.
        # A takeover is its own repair: only a broken home waits for self.
        if await_repair:
            from .lifecycle import main_tip
            try:
                retry_revision = main_tip(home.root / "self")
            except (SelfError, OSError):
                retry_revision = "unknown"
        clone = room / "self"
        try:
            revision = git(clone, "rev-parse", "HEAD").stdout.strip()
        except (SelfError, OSError):
            revision = "unknown"
        # One notice per run of home-caused failures: keyed on the HEAD each
        # retry cloned, a busy main minted a new one per attempt (inbox, 2026-10-23).
        notice(home, facts, "self-refusal", f"{thread}:streak:{_home_streak(facts, thread, gen)}",
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
                     **({"home_failure": True} if cause == "failed" and failure_origin == "home" else {}),
                     **({"self_revision": retry_revision} if retry_revision else {}),
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


_FENCED = ("fenced: this install lost the inbox lease and another may lead; "
           "land what you have and stop, the thread wakes again where the lease is")


def _fenced(home, facts) -> bool:
    """A lease streak is open on this install: the router fenced and has not
    taken the inbox lease back (``Router._fence``, ``Router._recovered``)."""
    from .health import open_streaks
    return any(f.data.get("step") == "lease" for f in open_streaks(facts, home.install_id()))


def _observe_body(home, facts, strand, thread, gen, bodies, *, choice=None, force=False):
    from .config import load_config
    from .lifecycle import CLOSED, closing_commit, read_thread
    from .usage import fuels, reading
    from types import SimpleNamespace
    now, config, room = time.time(), load_config(home.root), home.room(strand)
    alive = body_alive(room)
    state = state_of(facts, strand, gen, alive=alive)
    lease = fact_for(facts, "lease", strand, gen).data
    start = fact_for(facts, "body.started", strand, gen)
    closed = read_thread(home, thread).get("status") in CLOSED
    fenced = _fenced(home, facts)
    default = choice or ("fake", "haiku")
    shell, core = lease.get("shell", default[0]), lease.get("core", default[1])
    if isinstance(facts, LedgerView):
        after = max(facts.latest_at.get((kind, strand), 0) for kind in ("body.exited", "body.parked"))
    else:
        after = max((datetime.fromisoformat(f.at).timestamp() for f in facts
                     if f.data.get("strand") == strand and f.kind in {"body.exited", "body.parked"}), default=0)
    dms = list(_person_dms(home, facts, thread)) if state.name == "parked" else []
    # A person's "wake" on their own DM is `threads --force`, said in a word;
    # "wake <shell>[/<core>]" ends this park so the next gen runs on that body.
    told = set()
    for letter, channel in dms:
        named = _wake_letter(home, facts, thread, (shell, core), letter, channel)
        if named is None:
            continue
        told.add(letter.id)
        force = force or named == (shell, core)
    dms = [(letter, channel) for letter, channel in dms if letter.id not in told]
    woken = _wake_choice(facts, thread) if state.name == "parked" else None
    # A parked body reads the gauge like any other: a refill is seen when it
    # happens, and the park's `until` only guards a gauge that reads nothing.
    gauge = {"windows": []} if force else reading(home, shell, 0 if alive else after)
    empty = next((w for w in gauge["windows"] if w["left_pct"] <= config.hold_floor_pct), None) if gauge else None
    until = 0
    if empty:
        from .usage import dry_until
        until = dry_until(home, shell) or now + 900
    pid = int(start.data.get("pid") or 0) if start else 0
    proven = None
    # A stop on record with the body still alive is a signal that was never
    # sent or did not land: prove again, every tick, until the lock is gone.
    if alive and (state.name == "stopping"
                  or (closed or fenced) and state.name == "leaving" and now >= state.deadline):
        body = bodies.get(strand)
        proven = _proven_body(room, pid, str(start.data.get("lstart") or "") if start else "",
                              body.proc if body else None, alive=alive)
    # The boundary lists every cached shell. Park still reads `empty`, the seated gauge only.
    fuel = fuels(home, shell)
    context = SimpleNamespace(thread=thread, shell=shell, core=core, pid=pid, empty=empty, fuel=fuel,
                              why=(closing_commit(home, thread)[1] or f"thread {thread} settled") if closed
                              else _FENCED if fenced else "", failure="", repos=lease.get("repos"))
    o = Observation(strand, gen, alive, closed, "refreshing" if gauge is None else "empty" if empty
                    else "ok" if gauge["windows"] else "unknown",
                    now, proven, until=until, force=force, stop_grace=config.stop_grace,
                    woken="/".join(woken) if woken else "", fenced=fenced)
    if TABLE[row_for(facts, o)].next == "parked" and not fenced:
        park = fact_for(facts, "body.parked", strand, gen)
        window, left, when = ((empty["name"], empty["left_pct"], until) if empty else
                              (park.data["window"], park.data["left_pct"], park.data["until"]))
        what = (f"Out of {shell} {window} fuel ({left:g}% left) "
                f"until about {datetime.fromtimestamp(when).astimezone():%H:%M}. "
                f"Your letter is kept on thread {thread} and is answered when it refills.")
        lines = _wake_lines(home, facts, thread, (shell, core), (window, left, when))
        can = ("Reply wake to start it now." if len(lines) == 1
               else "Reply with one of:\n" + "\n".join(lines))
        for letter, channel in dms or _person_dms(home, facts, thread):
            notice(home, facts, "parked", letter.id, what, can,
                   about=letter, channel=channel, passive=True)
    return o, context


WAKE = re.compile(r"/?wake(?:\s+([A-Za-z0-9][A-Za-z0-9._-]*)(?:/(\S+))?)?", re.IGNORECASE)


def _wake_letter(home, facts, thread, leased, letter, channel, *, parked=True):
    """Dispose of one person's wake: the body it names, its refusal words, or None.

    The word is the whole letter, or the body a brnrd.dev composer letter
    chose; that letter's text stays owed, so its disposition has its own id.
    """
    chosen = letter.data.get("wake")
    said = WAKE.fullmatch(f"wake {chosen}" if chosen else str(letter.data.get("body", "")).strip())
    ident = f"{letter.id}#wake" if chosen else letter.id
    if not said or any(f.id == f"letter.disposed:{ident}" for f in facts):
        return None
    named = _wake_body(home, facts, thread, leased, said)
    disposition = {"id": ident, "outcome": "done"}
    if isinstance(named, str):
        notice(home, facts, "wake-refused", letter.id, f"{named} Nothing was started.",
               "\n".join(_wake_lines(home, facts, thread, leased, None)),
               about=letter, channel=channel, passive=True)
        disposition["reason"] = "wake: refused, " + named
    elif parked and named == leased:
        disposition["reason"] = "wake: started before its reset"
    else:
        disposition.update(reason=f"wake: on {body_label(*named)}", thread=thread, body=body_label(*named))
    try:
        facts.append(_record(home, "letter.disposed", {"dispositions": [disposition]},
                             f"letter.disposed:{ident}"))
    except LedgerConflict:
        pass
    return named


def _wake_shells(home, leased):
    """Shells a wake may name: the leased one, and every folder with a gauge."""
    from .usage import shells
    return sorted(set(shells(home)) | {leased})


def _core_for(home, facts, thread, shell, leased):
    """The core a bare ``wake <shell>`` means, or None when none is on record."""
    if shell == leased[0]:
        return leased[1]
    from .adapters import at_birth
    try:
        seated = at_birth(home, thread, ("", ""))
    except (ValueError, OSError):
        seated = ("", "")
    if seated[0] == shell:
        return seated[1]
    return next((f.data["core"] for f in reversed(facts)
                 if f.kind == "lease" and f.data.get("shell") == shell and f.data.get("core")), None)


def _wake_body(home, facts, thread, leased, said):
    """``(shell, core)`` a wake names, or the words for why it names none."""
    if not said[1]:
        return leased
    shell = said[1].lower()
    valid = _wake_shells(home, leased[0])
    if shell not in valid:
        return f"No body {said[1]!r} here; bodies are {', '.join(valid)}."
    core = said[2] or _core_for(home, facts, thread, shell, leased)
    if core is None:
        return f"No core on record for {shell}: name one, as wake {shell}/<core>."
    return shell, core


def _wake_lines(home, facts, thread, leased, park):
    """Each body a parked thread may wake on, as the exact letter, with its fuel."""
    from .usage import shell_fuels
    found = shell_fuels(home)
    if park:
        window, left, when = park
        lines = [f"wake: retry {body_label(*leased)} ({window} {left:g}%, "
                 f"resets {datetime.fromtimestamp(when).astimezone():%a %H:%M})"]
    else:
        lines = [f"wake: retry {body_label(*leased)}"]
    for shell in _wake_shells(home, leased[0]):
        if shell == leased[0]:
            continue
        word = f"wake {shell}" if _core_for(home, facts, thread, shell, leased) is not None else f"wake {shell}/<core>"
        lines.append(f"{word}: {shell + ' ' + found[shell] if shell in found else 'fuel unknown'}")
    return lines


def _wake_choice(facts, thread):
    """The body a person woke ``thread`` on since its last lease, or None.

    Read from the ledger, so a crash between the wake and the next lease
    still converges: the parked gen ends, and the router leases this body.
    """
    last = max((i for i, f in enumerate(facts) if f.kind == "lease" and f.data.get("thread") == thread),
               default=-1)
    for f in reversed(facts[last + 1:]):
        if f.kind == "letter.disposed" and f.by.startswith("loom:"):
            for d in f.data.get("dispositions", ()):
                if d.get("thread") == thread and d.get("body"):
                    from .adapters import parse_body
                    try:
                        return parse_body(d["body"])
                    except ValueError:
                        continue
    return None


def _person_dms(home, facts, thread):
    """Owed letters on ``thread`` that a person wrote in their own direct chat."""
    from .connector import person_dm
    channels = reply_channels(facts, home)
    for letter in letters_for(facts, main_snapshot(home), thread):
        author = str(letter.data.get("from", ""))
        channel = channels.get(letter.id)
        if (author.startswith(("p-", "person:")) and channel and person_dm(home, channel)
                == "person:" + author.removeprefix("p-").removeprefix("person:")):
            yield letter, channel


def _repaired(facts, strand, gen) -> str:
    """What the loom repaired in this generation's room before it woke."""
    for fact in reversed(facts):
        if (fact.kind == "room.repaired" and fact.data.get("strand") == strand
                and fact.data.get("gen") == gen):
            return repaired_line(fact)
    return ""


def _boundary(home, facts, strand, gen, context):
    from .threads import thread_table
    main, state = main_snapshot(home), fold(facts)
    owed = letters_for(facts, main, context.thread)
    text = render_boundary(
        strand, gen, context.thread, owed,
        state.shown.get(strand, set()), sender_threads(facts), context.fuel,
        context.why or None, reply_channels(facts, home),
        opened=thread_table(home, facts), plan=_plan(home, context.thread, facts),
        replies={f.letter.id: f.reply_to for f in active_owners(facts, main)},
        attachments=letter_attachments(home.root, facts),
        reply_context=_reply_context(facts, context.thread, owed, home.room(strand)),
        repaired=_repaired(facts, strand, gen),
        degraded=degraded_line(facts, home.install_id()),
        library=problem_line(facts, home.install_id()),
        moved=moved_line(facts, strand, gen, home.install_id()),
        face=proposal_line(propose(facts, strand, gen, context.thread)),
        left=_left(facts, context.thread, strand),
        **_budget(home, context.thread, facts),
    )
    path = home.room(strand) / "port/in/boundary.md"
    if not path.is_file() or path.read_text() != text:
        atomic_write(path, text)
    else:
        # Rendered from these facts: the jack's stop compares this mtime with the ledger's.
        os.utime(path)


_PAUSE_LOOKED: dict[Path, float] = {}
# Ports with a call this process paused, and their home: a stopped call that
# holds the ledger lock is resumed before anyone waits on it.
_PAUSED: dict[Path, Home] = {}


def _free_ledger(lock):
    """``ledger.ON_CONTENDED``: resume each paused call that has ``lock`` open."""
    if not _PAUSED:
        return
    from brr import pause
    from . import pausing
    for port, records in pausing.free_lock(list(_PAUSED), Path(lock)).items():
        _log(_PAUSED[port], f"resumed {port.parent.name}'s paused call, which held the ledger lock: "
                            + pause.describe_paused(records))
    for port in [p for p in _PAUSED if not pause.read_paused_record(p)]:
        del _PAUSED[port]


def _unstick(home):
    """Between ticks nobody may wait behind a paused call that holds the lock."""
    if not _PAUSED:
        return
    lock = home.facts_dir() / ".lock"
    with open(lock, "a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            _free_ledger(lock)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _pause_for_letters(home, strand, c):
    """A letter the body has not seen pauses its tool call (pausing.py)."""
    from brr import pause
    from . import pausing
    port = home.room(strand) / "port"
    if pause.overdue_records(port):
        _log(home, f"pause of {strand} unanswered for {pausing.CAP:.0f}s; resumed "
                   + pause.describe_paused(pause.resume_pids(port)))
    boundary = port / "in/boundary.md"
    if not c.pid or not boundary.is_file():
        return
    pattern = pausing.wrapper(resolve(home, c.shell))
    unseen = any(not letter.compact for letter in parse_boundary(boundary.read_text()).letters)
    # One ps a few seconds, not one a tick, while a letter waits on a call.
    if pattern is None or not unseen or time.monotonic() - _PAUSE_LOOKED.get(port, 0) < pausing.MIN_AGE:
        return
    _PAUSE_LOOKED[port] = time.monotonic()
    state = json.loads((port / "jack-state.json").read_text()) if (port / "jack-state.json").is_file() else {}
    since = state.get("boundary_at")
    # A delegate's calls are children of the same body, and its failure hook says nothing.
    live = port / "live-delegates"
    delegating = live.is_dir() and any(time.time() - p.stat().st_mtime < pausing.CAP for p in live.iterdir())
    if since and not delegating and (records := pausing.pause_call(port, c.pid, pattern, float(since))):
        _log(home, f"paused {strand}'s call for a letter: {pause.describe_paused(records)}")
        _PAUSED[port] = home


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
        # Shown is what the wake carried; a letter left out arrives at a boundary.
        carried = {str(row.get("id")) for row in json.loads((x.room / "port/owed.json").read_text())}
        x.letters = [f for f in x.letters if str(f.data.get("id", f.id)) in carried]
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
    _log(x.home, f"start {x.o.strand} thread {c.thread} gen {x.o.gen} pid {x.proc.pid} {body_label(c.shell, c.core)}")


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
    from brr import pause
    # A body gone with its call paused: nobody is left to resume it.
    pause.release_all(x.room / "port")
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
        _pause_for_letters(home, o.strand, c)
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


def _advance_bodies(home, bodies, facts, choice, *, reader=None):
    from .router import install_of
    holders = {strand: (thread, gen) for thread, (strand, gen) in fold(facts).holder.items()}
    for strand in holders.keys() | bodies.keys():
        if install_of(strand) == home.install_id():
            if strand in holders:
                thread, gen = holders[strand]
                lease = fact_for(facts, "lease", strand, gen)
            else:
                lease = (facts.latest_strand.get(("lease", strand)) if isinstance(facts, LedgerView)
                         else next((f for f in reversed(facts) if f.kind == "lease" and f.data.get("strand") == strand), None))
            if lease is not None:
                d = lease.data
                if not state_of(facts, strand, d["gen"]).released:
                    facts = _start(home, strand, d["thread"], d["gen"], bodies, facts, choice,
                                   reader=reader)
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
    _unstick(home)
    facts = router.reader.refresh(home)
    if router.projection_revision != router.reader.revision:
        router.projections.clear()
        router.projection_revision = router.reader.revision
    with projection_cache(router.projections):
        facts = router.begin_tick(facts)
        facts = _ingest(home, facts, router.config.router_ttl)
        facts = _grant_landed(home, facts)
        facts = _grant_opened(home, facts)
        facts = _advance_bodies(home, bodies, facts, (adapter, core), reader=router.reader)
        # Time enters the owner table once, using only the facts already held.
        facts = observe_due(home, facts, router.armed())
        facts = router.route(facts, bodies, (adapter, core))
        facts = router.speak_out(facts)


_GRANTED: dict[Path, str] = {}


def _grant_landed(home: Home, facts: list[Fact]) -> list[Fact]:
    """Once per main commit: a landed Plan row's receipt is a result on each wish it cites."""
    from .lifecycle import main_tip
    from .wishes import landed_results, told
    repo = home.root / "self"
    if not (repo / ".git").exists():
        return facts
    tip = main_tip(repo)
    if _GRANTED.get(repo) == tip:
        return facts
    for fact in landed_results(told(facts), main_snapshot(home), f"loom:{home.install_id()}"):
        facts.append(append(home, fact))
    _GRANTED[repo] = tip
    return facts


def _grant_opened(home: Home, facts: list[Fact]) -> list[Fact]:
    """A pull request opened on a thread with one open wish is that wish's result."""
    from .wishes import opened_results, told
    for fact in opened_results(told(facts), kind_facts(facts, "pr.opened"), f"loom:{home.install_id()}"):
        facts.append(append(home, fact))
    return facts


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


#: An open call older than this is a lost post hook, not a body at work.
IDLE_STALE = 1800


def _idle(home: Home, young: float | None = None) -> bool:
    """No live body of this install has a tool call open younger than ``young`` seconds
    (``None``: none open at all): the loom may move its code."""
    limit = IDLE_STALE if young is None else min(young, IDLE_STALE)
    from . import tooltrace
    from .router import install_of
    for strand, _gen in fold(read_facts(home)).holder.values():
        room = home.room(strand)
        if install_of(strand) != home.install_id() or not body_alive(room):
            continue
        if any(time.time() - call["at"] < limit for call in tooltrace.live(room)["open"]):
            return False
    return True


def _read_usage(home, usage) -> None:
    for shell in usage.shells(home):
        usage.reading(home, shell)


def run(root: Path | str, *, adapter: str = "fake", core: str = "haiku",
        tick: float = 0.2, stop=None, install: str | None = None) -> None:
    if tick <= 0:
        raise ValueError("tick must be positive")
    stop = _own_stop(stop)
    home = Home(root, install=install)
    held = _hold_loop(home, stop)
    if held is None:
        return
    inputs = board = shell = None
    bodies: dict[str, Body] = {}
    try:
        if stop is not None and stop.is_set():
            return
        home.install_id()
        from . import ledger
        ledger.ON_CONTENDED = _free_ledger
        # A pause outlives a re-exec on disk; the loop must still free its lock.
        _PAUSED.update((port.parent, home) for port in (home.root / "rooms").glob("*/port/.paused.json"))
        from .receipts import record_start
        try:
            # Each start and re-exec: which merged pull requests this code holds.
            record_start(home, read_facts(home))
        except Exception:
            _log(home, traceback.format_exc().rstrip())
        from .config import ConfigError, load_config, tick_config
        from . import usage
        config = load_config(home.root)
        fingerprint = _source_fingerprint()
        next_usage = 0
        from .router import Router
        router = Router(home, config)
        from .connector import Inputs
        inputs = Inputs(home)
        health = Health(home, read_facts(home), _log)
        from .snapshot import Publisher
        publisher = Publisher(home, _log, functools.partial(health.guard, backoff=False))
        if config.board_port:  # read at start: a port change waits for the next re-exec
            from .board import Parts, serve
            board = Parts(home, lambda: load_config(home.root), _log)
            threading.Thread(target=board.run, name="board", daemon=True).start()
            try:
                shell = serve(board, int(config.board_port))
            except (OSError, ValueError) as exc:  # a taken port or not a number: the loop runs on without a board
                _log(home, f"board: port {config.board_port}: {exc}")
        from .mirror import Mirror
        mirror = Mirror(home, _log)
        from .library import Keeper
        keeper = Keeper(home, _log)
        from .reap import Reaper
        reaper = Reaper(home, _log)
        from .receipts import source_root
        updater = Updater(home, _log, source_root())
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
                    health.guard("usage", _read_usage, home, usage, backoff=False)
                    next_usage = time.monotonic() + usage.REFRESH_INTERVAL
                # Inputs, publisher, mirror and reap pace their own retries; only the tick backs off.
                # Connectors run only on the router lease's holder: a second
                # install ingesting too would answer every letter twice.
                if router.armed():
                    health.guard("inputs", inputs.step, backoff=False)
                else:
                    inputs.close()
                health.guard("tick_once", tick_once, home, bodies, adapter, core, router)
                health.guard("publisher", publisher.step, config, router, backoff=False)
                health.guard("mirror", mirror.step, config, backoff=False)
                health.guard("library", keeper.step, backoff=False)
                health.guard("disk", reaper.disk, backoff=False)
                health.guard("reap", reaper.step, backoff=False)
                # Only at an idle tick, and the re-exec follows in this same pass.
                health.guard("update", updater.step, config, functools.partial(_idle, home),
                             backoff=False)
            if updater.moved is not None or _source_fingerprint() != fingerprint:
                _log(home, "runtime source changed; re-executing loom")
                inputs.close()
                _shutdown(bodies)
                from .entry import reexec_argv
                argv = reexec_argv() or [sys.executable, *sys.orig_argv[1:]]
                os.execv(argv[0], argv)
                return
            if _pause(stop, tick):
                break
    finally:
        _shutdown(bodies)
        if board is not None:
            board.stop.set()
        if shell is not None:
            shell.shutdown()
            shell.server_close()
        if inputs is not None:
            inputs.close()
        fcntl.flock(held, fcntl.LOCK_UN)
        os.close(held)

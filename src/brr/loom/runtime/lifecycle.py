"""Thread decisions are README commits, landed through the self's existing door."""

import re
from pathlib import Path
from typing import Callable

from .home import Home, home_of_room, strand_of_room, check_thread_id
from .ledger import read_facts
from .merge import _continue, _rebase, _rebasing, send_to_self
from .port import PortError
from .project import sender_threads
from .selfrepo import ReadmeError, git, parse_readme, resolve_clone

CLOSED = {"settled", "retired"}
DONE_KINDS = ("pr", "issue", "doc", "research", "file", "answer", "post", "action")


def parse_plan(text: str) -> list[tuple[bool | None, str]] | None:
    """``- step`` rows under Plan, a ``[ ]``/``[x]`` tick kept but counted by no one
    (None when the row has no box); None means there is no Plan section."""
    plan = None
    inside = False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line == "## Plan"
            if inside and plan is None:
                plan = []
        elif inside:
            row = re.match(r"^\s*- (?:\[([ xX])\] )?(.+)$", line)
            if row:
                box = row.group(1)
                plan.append((None if box is None else box.lower() == "x", row.group(2)))
    return plan


def parse_done(text: str) -> list[tuple[str, str, str]]:
    """``- <kind> <ref> — <line>`` under Done; any other line there is prose."""
    done, inside = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line == "## Done"
        elif inside:
            row = re.match(rf"^- ({'|'.join(DONE_KINDS)}) (\S+) — (.+)$", line)
            if row:
                done.append(row.groups())
    return done


def read_thread(home: Home, thread: str, ref: str = "main") -> dict:
    """Read a landed README, never a room's unlanded status."""
    path = home.thread_dir(thread) / "README.md"
    if ref == "main" and (home.root / "self/.git").exists():
        # The tick asks this per thread; the pinned snapshot already holds it.
        return dict(main_snapshot(home).get(thread, {}))
    try:
        if (home.root / "self/.git").exists():
            proc = git(home.root / "self", "show", f"{ref}:threads/{thread}/README.md", check=False)
            if proc.returncode:
                return {}
            text = proc.stdout
        else:
            text = path.read_text()
        data = parse_readme(text)
        plan = parse_plan(text)
        data["plan_rows"] = plan
        data["done"] = parse_done(text)
        return data
    except (OSError, ReadmeError):
        return {}


def own_thread(room: Path) -> str:
    thread = sender_threads(read_facts(home_of_room(room))).get(strand_of_room(room))
    if thread is None:
        raise PortError("room has no thread")
    return thread


def land(room: Path, message: str, edit: Callable[[Path], dict[str, str]]) -> None:
    """Rebase, make one scoped edit, and land it through the existing door."""
    clone = resolve_clone(room)
    stopped = _continue(clone) if _rebasing(clone) else _rebase(clone)
    if stopped is not None:
        raise PortError(f"landing {stopped.status}: {', '.join(stopped.files)}")
    files = edit(clone)
    for path, text in files.items():
        target = clone / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    paths = list(files)
    git(clone, "add", "--", *paths)
    if git(clone, "diff", "--cached", "HEAD", "--quiet", "--", *paths, check=False).returncode:
        git(clone, "commit", "--only", "-m", message, "--", *paths)
    outcome = send_to_self(clone)
    if outcome.status != "merged":
        raise PortError(outcome.stderr or f"landing {outcome.status}: {', '.join(outcome.files)}")


def land_status(room: Path, thread: str, status: str, message: str) -> None:
    check_thread_id(thread)

    def edit(clone):
        path = f"threads/{thread}/README.md"
        text = (clone / path).read_text()
        parse_readme(text)
        lines = text.splitlines(keepends=True)
        for i in range(1, lines.index("---\n", 1)):
            if lines[i].split(":", 1)[0].strip() == "status":
                lines[i] = f"status: {status}\n"
                break
        changed = "".join(lines)
        parse_readme(changed)
        return {path: changed}

    land(room, message, edit)


def frontmatter_text(value: str) -> str:
    """One line the README grammar can hold: whitespace folded, double quotes made single."""
    return " ".join(str(value).split()).replace('"', "'")


def open_thread(room: Path, thread: str, sender: str, text: str,
                body_choice: str | None = None, repo_sources: tuple[str, ...] | list[str] = (),
                opened_for: tuple[str, ...] | list[str] = (),
                wish_lines: dict[str, str] | None = None) -> None:
    from .port import _current_label
    check_thread_id(thread)
    own = sender_threads(read_facts(home_of_room(room))).get(sender)
    if own is None:
        raise PortError("send: opening a thread requires a sender with a thread")
    if _current_label(resolve_clone(room), sender).taint:
        raise PortError("send: a tainted strand cannot open a thread on main; "
                        f"send a clean letter to {home_of_room(room).main_thread()} instead")
    if body_choice is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*(/[^\s]+)?", body_choice):
        raise PortError("send: --body must be shell or shell/core")
    from .adapters import repos
    if any("\n" in source or "\r" in source for source in repo_sources):
        raise PortError("send: --repo must be one seat line")
    seat = (f"body: {body_choice}\n" if body_choice is not None else "")
    seat += "".join(f"repo: {source}\n" for source in repo_sources)
    repos(seat)
    first = text.split("\n", 1)[0]
    # The wish's line is copied beside its id: INDEX.md is built in the room,
    # which has no ledger. A copy of an immutable fact, like a letter id.
    lines = "".join(f'wish-{ident}: "{frontmatter_text((wish_lines or {}).get(ident, ""))}"\n'
                    for ident in opened_for if (wish_lines or {}).get(ident, "").strip())
    readme = (f"---\nid: {thread}\nstatus: open\ntense: plan\n"
              f"opened-by: thread:{own}\n"
              + (f"opened-for: [{', '.join(opened_for)}]\n" if opened_for else "")
              + lines
              + f"---\n# {first}\n")
    parse_readme(readme)

    def edit(clone):
        path = f"threads/{thread}/README.md"
        if git(clone, "cat-file", "-e", f"origin/main:{path}", check=False).returncode == 0:
            raise PortError(f"send: thread {thread} was opened meanwhile; send to the existing thread")
        files = {path: readme}
        if seat:
            files[f"threads/{thread}/seat"] = seat
        return files

    land(room, f"open {thread} from thread:{own}", edit)


def settle(room: Path, thread: str, why: str, retire: bool = False,
           hand_to: str | None = None, word: str | None = None) -> None:
    if not why.strip():
        raise PortError("settle: --why is empty")
    home = home_of_room(room)
    data = read_thread(home, thread)
    if not data:
        raise PortError(f"settle: no thread {thread} on main")
    own = own_thread(room)
    opener = data.get("opened-by", "")
    if opener != f"thread:{own}" and not (
        own == thread and (not opener or str(opener).startswith("person:"))
    ):
        raise PortError(f"settle: only {opener or 'its own body on a person’s word'} may settle {thread}")
    from .ledger import append
    from .project import _destination, fold
    from .verbs_wishes import wish_fact
    from .wishes import wishes

    facts = read_facts(home)
    if opener != f"thread:{own}":
        # A person's thread settles on their word, never on silence: the
        # body judges the reply and cites it; the loom checks who wrote it.
        said = next((f for f in fold(facts).accepted if f.kind == "letter" and f.id == word), None)
        if said is None or not str(said.data.get("from", "")).startswith(("p-", "person:")) \
                or _destination(said.data) != thread:
            raise PortError(f"settle: {thread} settles on its person's word: "
                            f"--word <their letter to {thread}>")
    from .guards import for_room, violations
    # The settled thread's own rooms too: an opener settling a child would
    # otherwise judge only the room it settles from.
    rooms = {Path(room).resolve(): own}
    for strand, served in sender_threads(facts).items():
        if served == thread and home.room(strand).is_dir():
            rooms.setdefault(home.room(strand).resolve(), thread)
    broken = []
    for where, serving in rooms.items():
        lines = violations(for_room(where, serving)[0], where)
        broken += [f"{where.name}: {line}" for line in lines]
    if broken:
        raise PortError("settle: a path guard is broken in this thread's rooms:\n" + "\n".join(broken))
    pending = sorted(w.id for w in wishes(facts).values()
                     if w.thread == thread and w.state == "delivered")
    if hand_to is not None:
        check_thread_id(hand_to)
        if hand_to == thread or not home.routable(hand_to):
            raise PortError("settle: --hand-to must name another thread on main")
    if pending and hand_to is None:
        raise PortError(f"settle: delivered wishes await a verdict: {', '.join(pending)}; "
                        "record a verdict or use --hand-to <thread>")
    handed = [wish_fact(facts, by="strand:" + strand_of_room(room), ident=ident, thread=hand_to)
              for ident in pending]
    for fact in reopen_for(room, handed):
        append(home, fact)
    for stamp in handed:
        append(home, stamp)
    land_status(room, thread, "retired" if retire else "settled", f"settle {thread}: {why}")


def reopen_for(room: Path, stamps) -> list:
    """A wish placed on a closed thread opens it again, whatever placed it.

    Every ``wish`` stamp names a thread and leaves the wish open, so a closed
    thread holding it would hold open work. The status line on main stays the
    one store: it flips to ``open`` here, before the stamp lands. The returned
    ``thread.reopened`` facts are the event the next wake reads, never a state.
    """
    from brr.daemon2.facts import Fact
    from .home import mint
    from .registry import admit
    home = home_of_room(room)
    out, seen = [], set()
    for stamp in stamps:
        thread = stamp.data.get("thread")
        if not thread or thread in seen:
            continue
        seen.add(thread)
        status = read_thread(home, thread).get("status")
        if status not in CLOSED:
            continue
        sha, why, _ = closing_commit(home, thread)
        land_status(room, thread, "open", f"reopen {thread}: {stamp.data['wish']} placed on it")
        fact = Fact(kind="thread.reopened", by=stamp.by, id="thread.reopened:" + mint(12),
                    data={"thread": thread, "wish": stamp.data["wish"], "was": status,
                          "closed": sha, "why": why})
        admit(fact)
        out.append(fact)
    return out


_closing: dict[tuple[str, str, str], tuple[str, str, str]] = {}


def closing_commit(home: Home, thread: str) -> tuple[str, str, str]:
    """The commit that last changed the status line: (sha, why, writing strand).

    The loom asks on every tick, and the search walks the README's whole
    history, so the answer is kept per README blob on main.
    """
    path = f"threads/{thread}/README.md"
    _, blobs = main_tree(home.root / "self")
    blob = blobs.get(path, "")
    key = (str(home.root), thread, blob)
    if key not in _closing:
        _closing[key] = _closing_commit(home, path)
    return _closing[key]


def _closing_commit(home: Home, path: str) -> tuple[str, str, str]:
    text = git(home.root / "self", "log", "-1", "-G", "^status:", "--format=%H%n%s%n%(trailers:key=Loom-Strand,valueonly)",
               "main", "--", path, check=False).stdout.splitlines()
    if len(text) < 2:
        return "", "", ""
    commit, subject, *writer = text
    return commit, subject.partition(": ")[2] or subject, writer[0].strip() if writer else ""


_MAIN_SNAPSHOTS = {}
_MAIN_TREES = {}
_READMES = {}
_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


def main_tip(repo: Path) -> str:
    """The commit main names. Read from the ref itself when it can be: the
    tick asks many times, and a git process each time was most of its cost."""
    try:
        loose = (repo / ".git/refs/heads/main").read_text().strip()
        if _SHA.fullmatch(loose):
            return loose
    except OSError:
        pass
    try:
        for line in (repo / ".git/packed-refs").read_text().splitlines():
            sha, _, name = line.partition(" ")
            if name == "refs/heads/main" and _SHA.fullmatch(sha):
                return sha
    except OSError:
        pass
    return git(repo, "rev-parse", "main").stdout.strip()


def main_tree(repo: Path) -> tuple[str, dict[str, str]]:
    """main's commit and the blob of each file under threads/: one git call per new commit."""
    tip = main_tip(repo)
    cached = _MAIN_TREES.get(repo)
    if cached and cached[0] == tip:
        return cached
    blobs = {}
    for line in git(repo, "ls-tree", "-r", tip, "--", "threads").stdout.splitlines():
        meta, tab, path = line.partition("\t")
        if tab and len(meta.split()) == 3:
            blobs[path] = meta.split()[2]
    _MAIN_TREES[repo] = (tip, blobs)
    return tip, blobs


def readme_at(home: Home, thread: str, blob: str, ref: str) -> dict:
    """A landed README parsed once per content: a commit elsewhere rereads nothing."""
    key = (home.root / "self", thread, blob)
    if key not in _READMES:
        try:
            _READMES[key] = read_thread(home, thread, ref)
        except ValueError:
            _READMES[key] = {}
    return _READMES[key]


def main_snapshot(home: Home, tree: tuple[str, dict[str, str]] | None = None) -> dict[str, dict]:
    """Pin main once; both inventory and README fields come from that tree.

    A caller that also walks main's tree passes the ``main_tree`` it read, so a
    commit landing between two reads cannot split one view across two tips.
    """
    repo = home.root / "self"
    if (repo / ".git").exists():
        ref, blobs = tree or main_tree(repo)
        cached = _MAIN_SNAPSHOTS.get(repo)
        if cached and cached[0] == ref:
            return cached[1]
    else:
        ref = "main"
        blobs = {p.relative_to(repo).as_posix(): "" for p in (repo / "threads").glob("*/README.md")}
    result = {}
    for path, blob in blobs.items():
        parts = path.split("/")
        if len(parts) == 3 and parts[2] == "README.md":
            try:
                data = readme_at(home, parts[1], blob, ref) if blob else read_thread(home, parts[1], ref)
            except ValueError:
                continue
            if data:
                result[parts[1]] = data
    if (repo / ".git").exists():
        _MAIN_SNAPSHOTS[repo] = (ref, result)
        for key in [k for k in _READMES if k[0] == repo and blobs.get(f"threads/{k[1]}/README.md") != k[2]]:
            del _READMES[key]
    return result

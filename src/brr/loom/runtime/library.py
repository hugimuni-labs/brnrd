"""A person's folders, kept or known, one page each at ``library/<name>.md`` in the self.

A page's frontmatter is the entry: ``kind`` (kept or known), ``path`` (a
folder on ``machine``) or ``source`` (a repo URL, cloned where a seat names
it), ``whose`` and ``for``. Below it is the resident's map of the folder.
The loom reads pages as they landed on main and writes none of them: ``lib
add`` and ``lib drop`` change the room's self clone, and the body lands it.

A kept folder on this machine is a git repo worked in place: every room
links it at ``lib/<name>``, a body commits its own writes with ``lib
commit`` before it stops, and the loom only looks. Its idle check turns a
missing folder, a lost ``.git``, a conflict, edits left uncommitted for an
hour and commits not on the upstream into ``library.problem`` facts, read
back as one ``library:`` boundary line, each closed by ``library.cleared``.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .selfrepo import SelfError, git

KINDS = ("kept", "known")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# The wake budget is 24 KB for everything; a library of any size stays a few KB.
LINE_CAP = 200
SHOWN = 12
# His edits left this long with nobody committing them become a problem.
DIRTY_AFTER = 3600.0
CHECK_EVERY = 60.0
# A first commit past this is a folder to know, or a narrower .gitignore.
INIT_CAP = 1 << 30
LINKS = "lib"


class LibraryError(ValueError):
    """An entry or a page that cannot be used."""


@dataclass(frozen=True)
class Entry:
    name: str
    kind: str
    whose: str
    purpose: str
    path: str = ""
    source: str = ""
    machine: str = ""
    inode: str = ""

    @property
    def cloned(self) -> bool:
        return bool(self.source)

    def here(self, host: str) -> bool:
        """A folder on this machine (a page without a machine is anyone's)."""
        return bool(self.path) and (not self.machine or self.machine == host)


def machine() -> str:
    return socket.gethostname().split(".")[0]


def _frontmatter(text: str) -> dict[str, str]:
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}
    data: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return data
        key, colon, value = line.partition(":")
        if colon:
            data[key.strip()] = value.strip().strip('"')
    return {}


def parse(name: str, text: str) -> Entry:
    data = _frontmatter(text)
    kind = data.get("kind", "")
    if kind not in KINDS:
        raise LibraryError(f"library/{name}.md: kind must be kept or known, not {kind!r}")
    path, source = data.get("path", ""), data.get("source", "")
    if bool(path) == bool(source):
        raise LibraryError(f"library/{name}.md: needs exactly one of path or source")
    return Entry(name=name, kind=kind, whose=data.get("whose", ""), purpose=data.get("for", ""),
                 path=path, source=source, machine=data.get("machine", ""),
                 inode=data.get("inode", ""))


def read(self_dir: Path, rev: str = "main") -> tuple[list[Entry], list[str]]:
    """Entries on ``rev`` by name, and one line per page that cannot be read."""
    if rev == "main":
        from .lifecycle import main_tip
        try:
            tip = main_tip(self_dir)
        except SelfError:
            return [], []
        cached = _READ.get(self_dir)
        if cached is None or cached[0] != tip:
            cached = _READ[self_dir] = (tip, _read(self_dir, tip))
        return list(cached[1][0]), list(cached[1][1])
    return _read(self_dir, rev)


# self dir -> (main tip, read): the router resolves seat repos every tick.
_READ: dict = {}


def _read(self_dir: Path, rev: str) -> tuple[list[Entry], list[str]]:
    proc = git(self_dir, "ls-tree", "--name-only", rev, "library/", check=False)
    if proc.returncode != 0:
        return [], []
    found: list[Entry] = []
    bad: list[str] = []
    for path in sorted(proc.stdout.split()):
        name = path.removeprefix("library/").removesuffix(".md")
        if not path.endswith(".md") or not NAME.fullmatch(name):
            continue
        shown = git(self_dir, "show", f"{rev}:{path}", check=False)
        try:
            found.append(parse(name, shown.stdout))
        except LibraryError as exc:
            bad.append(str(exc))
    return found, bad


def entries(home) -> tuple[list[Entry], list[str]]:
    self_dir = home.root / "self"
    if not (self_dir / ".git").exists():
        return [], []
    return read(self_dir)


def sources(home) -> dict[str, str]:
    """Name -> source, for a seat's ``repo: <name>``."""
    return {entry.name: entry.source for entry in entries(home)[0] if entry.source}


def _home_short(path: str) -> str:
    user = str(Path.home())
    return "~" + path[len(user):] if path == user or path.startswith(user + "/") else path


def _where(entry: Entry, here: str) -> str:
    if entry.cloned:
        return f"{entry.source}, cloned where a seat says `repo: {entry.name}`, publish to send"
    shown = _home_short(entry.path)
    if entry.machine and entry.machine != here:
        return f"at {shown} on {entry.machine}, not this machine"
    if not Path(entry.path).is_dir():
        return f"MISSING at {shown}"
    if entry.kind == "kept":
        return f"at {LINKS}/{entry.name} ({shown})"
    return f"at {shown}"


def _git(folder: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    """A read in his folder: bounded, never raising, None when git could not run."""
    try:
        return subprocess.run(["git", *args], cwd=folder, capture_output=True, text=True,
                              timeout=20, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    except (OSError, subprocess.TimeoutExpired):
        return None


def _own_repo(folder: Path) -> bool:
    """``folder`` is the top of its own work tree, not a folder inside another."""
    proc = _git(folder, "rev-parse", "--show-toplevel")
    return proc is not None and proc.returncode == 0 and \
        Path(proc.stdout.strip()).resolve() == folder.resolve()


def _status(folder: Path) -> list[tuple[str, str]] | None:
    proc = _git(folder, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if proc is None or proc.returncode != 0:
        return None
    found: list[tuple[str, str]] = []
    parts = iter(proc.stdout.split("\x00"))
    for part in parts:
        if not part:
            continue
        found.append((part[:2], part[3:]))
        if "R" in part[:2] or "C" in part[:2]:
            next(parts, None)
    return found


_CONFLICT = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}


def _kept_state(entry: Entry) -> str:
    """``last commit 10-26 13:40, clean`` or ``…, 3 uncommitted``: one git read each."""
    folder = Path(entry.path)
    last = _git(folder, "log", "-1", "--format=%cd", "--date=format:%m-%d %H:%M")
    when = last.stdout.strip() if last is not None and last.returncode == 0 else ""
    status = _status(folder)
    if status is None:
        return "NOT A GIT REPO"
    dirty = f"{len(status)} uncommitted" if status else "clean"
    return f"last commit {when}, {dirty}" if when else f"no commits, {dirty}"


def line(entry: Entry, here: str | None = None) -> str:
    here = here or machine()
    whose = f"{entry.whose}'s " if entry.whose else ""
    may = "I commit here" if entry.kind == "kept" else "read and work on request, tidy after"
    text = f"{entry.kind} {entry.name}: {whose}{_where(entry, here)}; {may}"
    if entry.kind == "kept" and entry.here(here) and Path(entry.path).is_dir():
        text += f"; {_kept_state(entry)}"
    if entry.purpose:
        text += f"; for: {entry.purpose}"
    if len(text) > LINE_CAP:
        text = text[: LINE_CAP - 1] + "…"
    return text


def wake_text(home) -> str:
    found, bad = entries(home)
    if not found and not bad:
        return ""
    here = machine()
    lines = [line(entry, here) for entry in found[:SHOWN]]
    if len(found) > SHOWN:
        lines.append(f"and {len(found) - SHOWN} more: `python -m brr.loom.runtime lib list`")
    lines += [f"library page unreadable: {why}" for why in bad]
    return "".join(f"{text}\n" for text in lines)


def page(entry: Entry) -> str:
    keys = [("kind", entry.kind), ("path", entry.path), ("source", entry.source),
            ("machine", entry.machine), ("inode", entry.inode), ("whose", entry.whose),
            ("for", entry.purpose)]
    head = "".join(f"{key}: {value}\n" for key, value in keys if value)
    title = entry.purpose or (entry.source or entry.path)
    return (f"---\n{head}---\n# {entry.name}: {title}\n\n"
            "What lives where, the person's conventions, what not to touch.\n")


def _is_source(target: str) -> bool:
    return "://" in target or (":" in target and not target.startswith("/"))


def add(self_dir: Path, name: str, target: str, *, kind: str, whose: str, purpose: str,
        git_init: bool = False) -> Path:
    """Write ``library/<name>.md`` in ``self_dir`` and commit it there.

    A target with a scheme or ``host:path`` is a source to clone; anything
    else is a folder on this machine, recorded with its device and inode so
    a move can be traced. A kept folder must be a git repo of its own;
    ``git_init`` makes one of a plain folder, which the person agreed to.
    """
    if not NAME.fullmatch(name) or name == "self":
        raise LibraryError(f"library name {name!r} is not a directory name")
    if kind not in KINDS:
        raise LibraryError(f"kind must be kept or known, not {kind!r}")
    dest = self_dir / "library" / f"{name}.md"
    if dest.exists():
        raise LibraryError(f"library/{name}.md exists; drop it first")
    if any("\n" in value for value in (target, whose, purpose)):
        raise LibraryError("one line each for target, whose and for")
    if _is_source(target):
        entry = Entry(name=name, kind=kind, whose=whose, purpose=purpose, source=target)
    else:
        folder = Path(os.path.expanduser(target))
        if not folder.is_absolute() or not folder.is_dir():
            raise LibraryError(f"{target}: not a folder (give a path from / or ~)")
        folder = folder.resolve()
        if kind == "kept":
            _keepable(folder, git_init)
        stat = folder.stat()
        entry = Entry(name=name, kind=kind, whose=whose, purpose=purpose, path=str(folder),
                      machine=machine(), inode=f"{stat.st_dev}:{stat.st_ino}")
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(page(entry))
    rel = f"library/{name}.md"
    git(self_dir, "add", "--", rel)
    git(self_dir, "commit", "--only", "-m", f"library: add {name} ({kind})", "--", rel)
    return dest


def _keepable(folder: Path, git_init: bool) -> None:
    if (folder / ".git").exists():
        if not _own_repo(folder):
            raise LibraryError(f"{folder}: has a .git that git does not read as its own work tree")
        return
    inside = _git(folder, "rev-parse", "--show-toplevel")
    if inside is not None and inside.returncode == 0:
        raise LibraryError(f"{folder} is inside the repo at {inside.stdout.strip()}; "
                           "keep that repo, or make this one known")
    if not git_init:
        raise LibraryError(f"{folder} is not a git repo; tell the person you will run git init there "
                           "and what .gitignore will leave out, then pass --git-init")
    proc = _git(folder, "init", "-q")
    if proc is None or proc.returncode != 0:
        raise LibraryError(f"git init in {folder} failed: {proc.stderr.strip() if proc else 'git did not run'}")


def first_commit_size(folder: Path) -> tuple[int, int]:
    """Files and bytes a ``git add -A`` would take in, after .gitignore."""
    proc = _git(folder, "ls-files", "-z", "--others", "--cached", "--exclude-standard")
    if proc is None or proc.returncode != 0:
        return 0, 0
    files = total = 0
    for rel in filter(None, proc.stdout.split("\x00")):
        try:
            total += (folder / rel).lstat().st_size
            files += 1
        except OSError:
            continue
    return files, total


def drop(self_dir: Path, name: str) -> None:
    rel = f"library/{name}.md"
    if not NAME.fullmatch(name) or not (self_dir / rel).is_file():
        raise LibraryError(f"no {rel}")
    git(self_dir, "rm", "-q", "--", rel)
    git(self_dir, "commit", "--only", "-m", f"library: drop {name}", "--", rel)


# --- kept folders: links, commits, checks ----------------------------------

def link(room: Path, found: list[Entry], host: str | None = None) -> list[str]:
    """Link each kept folder of this machine at ``room/lib/<name>``; drop stale links.

    Only symlinks are ever removed here, so nothing of his is deleted.
    Returns one line per folder that could not be linked.
    """
    host = host or machine()
    lib = room / LINKS
    want = {e.name: Path(e.path) for e in found if e.kind == "kept" and e.here(host)}
    if not want and not lib.is_dir():
        return []
    lib.mkdir(exist_ok=True)
    trouble = []
    for path in lib.iterdir():
        if path.is_symlink() and (path.name not in want or Path(os.readlink(path)) != want[path.name]):
            path.unlink()
    for name, target in sorted(want.items()):
        path = lib / name
        if not target.is_dir():
            trouble.append(f"kept {name}: {target} is missing, not linked")
            continue
        if path.is_symlink():
            continue
        if path.exists():
            trouble.append(f"kept {name}: {path} exists and is not a link, not linked")
            continue
        path.symlink_to(target)
    return trouble


def commit(folder: Path, message: str, paths: list[str]) -> str:
    """Commit ``paths`` and nothing else in a tree another body may share.

    New paths are added first (``--only`` refuses an untracked path); the
    rest of the index is left as it was. A held ``index.lock`` is waited
    out once. Returns the new commit's sha.
    """
    folder = folder.resolve()
    rels = []
    for given in paths:
        path = Path(given) if Path(given).is_absolute() else folder / given
        rel = os.path.relpath(os.path.abspath(path), folder)
        if rel == ".." or rel.startswith("../"):
            raise LibraryError(f"{given} is outside {folder}")
        rels.append(rel)
    if not rels:
        raise LibraryError("name the paths you wrote; a commit here takes only those")
    for attempt in (0, 1):
        try:
            untracked = git(folder, "ls-files", "-z", "--others", "--exclude-standard", "--", *rels).stdout
            new = [rel for rel in untracked.split("\x00") if rel]
            if new:
                git(folder, "add", "--", *new)
            git(folder, "commit", "-q", "--only", "-m", message, "--", *rels)
            return git(folder, "rev-parse", "HEAD").stdout.strip()
        except SelfError as exc:
            if attempt or "index.lock" not in str(exc):
                raise LibraryError(str(exc)) from exc
            time.sleep(1.0)
    raise AssertionError("unreachable")


def _missing(name: str, path: str, inode: str) -> str:
    return (f"missing at {_home_short(path)}: look for a folder named {Path(path).name} whose device:inode"
            f" is {inode or '?'}, propose the new path to its owner, and repoint library/{name}.md on their word")


def problems(entry: Entry, now: float | None = None) -> dict[str, str]:
    """What is wrong with a folder of this machine, by kind: missing for any, the git checks for a kept one."""
    now = time.time() if now is None else now
    folder = Path(entry.path)
    if not folder.is_dir():
        return {"missing": _missing(entry.name, entry.path, entry.inode)}
    if entry.kind != "kept":
        return {}
    if not (folder / ".git").exists():
        return {"not-git": f"no .git at {_home_short(entry.path)}; never re-init it unasked"}
    status = _status(folder)
    if status is None:
        return {"not-git": f"git status fails at {_home_short(entry.path)}"}
    found: dict[str, str] = {}
    conflicted = [path for code, path in status if code in _CONFLICT]
    if conflicted:
        found["conflict"] = f"conflict in {len(conflicted)} file(s): " + ", ".join(conflicted[:3])
    dirty = [path for code, path in status if code not in _CONFLICT]
    if dirty:
        stamps = []
        for rel in dirty:
            try:
                stamps.append((folder / rel).lstat().st_mtime)
            except OSError:
                pass
        newest = max(stamps) if stamps else 0.0
        if now - newest >= DIRTY_AFTER:
            since = datetime.fromtimestamp(newest).strftime("%m-%d %H:%M") if newest else "?"
            found["dirty"] = f"{len(dirty)} file(s) uncommitted, untouched since {since}"
    ahead = _git(folder, "rev-list", "--count", "@{upstream}..HEAD")
    if ahead is not None and ahead.returncode == 0 and ahead.stdout.strip() not in {"", "0"}:
        found["ahead"] = f"{ahead.stdout.strip()} commit(s) not on its upstream"
    return found


def _stamp(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def open_problems(facts, install: str) -> dict[tuple[str, str], object]:
    """``library.problem`` facts of this install no ``library.cleared`` has closed."""
    from .ledger import kind_facts
    closed = {f.id.replace("library.cleared:", "library.problem:", 1)
              for f in kind_facts(facts, "library.cleared")}
    return {(str(f.data["name"]), str(f.data["problem"])): f
            for f in kind_facts(facts, "library.problem")
            if f.id not in closed and f.data.get("install") == install}


def problem_line(facts, install: str) -> str:
    """One boundary line: each kept folder's open problems, oldest first."""
    found = sorted(open_problems(facts, install).values(), key=lambda f: str(f.data.get("since")))
    if not found:
        return ""
    return "library: " + "; ".join(f"{f.data['name']} {f.data['text']}" for f in found)


class Keeper:
    """The loop's look at kept folders: a fact when a problem opens, one when it clears."""

    def __init__(self, home, log):
        self.home, self.log = home, log
        self.checked: float | None = None

    def step(self) -> None:
        now = time.monotonic()
        if self.checked is not None and now - self.checked < CHECK_EVERY:
            return
        self.checked = now
        from brr.daemon2.facts import Fact
        from .ledger import LedgerConflict, append, read_facts
        install = self.home.install_id()
        host = machine()
        found = {}
        for entry in entries(self.home)[0]:
            if entry.here(host):
                for kind, text in problems(entry).items():
                    found[(entry.name, kind)] = text
        opened = open_problems(read_facts(self.home), install)
        writes = []
        for key, text in sorted(found.items()):
            if key not in opened:
                since = _stamp(time.time())
                writes.append(Fact(kind="library.problem", by=f"loom:{install}",
                                   id=f"library.problem:{install}:{key[0]}:{key[1]}:{since}",
                                   data={"install": install, "name": key[0], "problem": key[1],
                                         "text": text, "since": since}))
        for key, fact in sorted(opened.items()):
            if key not in found:
                writes.append(Fact(kind="library.cleared", by=f"loom:{install}",
                                   id=fact.id.replace("library.problem:", "library.cleared:", 1),
                                   data={"install": install, "name": key[0], "problem": key[1]}))
        for fact in writes:
            try:
                append(self.home, fact)
            except LedgerConflict:
                pass


# --- the stop: a body's own uncommitted writes ------------------------------

def written_since(room: Path, since: float, host: str | None = None) -> list[str]:
    """Uncommitted paths in this room's kept folders changed after ``since``.

    Read from the folder, not from tool calls, so a write through the shell
    counts too. His concurrent edits count as well; the stop names them and
    lets the body judge, once.
    """
    host = host or machine()
    try:
        found = json.loads((room / "port" / "library.json").read_text())
    except (OSError, ValueError):
        return []
    lines = []
    for item in found if isinstance(found, list) else []:
        if not isinstance(item, dict) or item.get("kind") != "kept":
            continue
        folder = Path(str(item.get("path", "")))
        status = _status(folder) if folder.is_dir() else None
        for code, rel in status or []:
            try:
                if (folder / rel).lstat().st_mtime < since:
                    continue
            except OSError:
                pass  # deleted since: count it
            lines.append(f"{LINKS}/{item.get('name')}/{rel}")
    return lines


def write_room_list(room: Path, found: list[Entry], host: str | None = None) -> None:
    """``port/library.json``: this machine's folders, for the room's hooks."""
    host = host or machine()
    rows = [{"name": e.name, "kind": e.kind, "path": e.path, "inode": e.inode} for e in found if e.here(host)]
    from .home import atomic_write
    atomic_write(room / "port" / "library.json", json.dumps(rows, indent=1) + "\n")


# --- known folders: what a body touched, nudged once at stop ----------------

TOUCHED = "library-touched.json"
_WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def _known(room: Path) -> tuple[float, list[tuple[str, str, set[str]]]]:
    """This body's start (the room list's mtime) and its known folders: name, real path, spellings."""
    listed = room / "port" / "library.json"
    since = listed.stat().st_mtime
    found = json.loads(listed.read_text())
    folders = []
    for item in found if isinstance(found, list) else []:
        if isinstance(item, dict) and item.get("kind") == "known" and item.get("path"):
            full = os.path.expanduser(str(item["path"])).rstrip(os.sep)
            real = os.path.realpath(full)
            spelled = {full, real, _home_short(full), _home_short(real)}
            folders.append((str(item.get("name")), real, spelled))
    return since, folders


def _names(command: str, spelled: set[str]) -> bool:
    """A shell command names the folder: a spelling followed by an end, a slash, a quote or a space."""
    return any(re.search(re.escape(path) + r"(?=$|[/\s'\";|)&])", command) for path in spelled)


def _touched_state(room: Path, since: float) -> dict:
    """The touch record for the body that began at ``since``; a stale one starts over."""
    try:
        state = json.loads((room / "port" / TOUCHED).read_text())
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict) or state.get("since") != since:
        state = {"since": since, "folders": {}, "nudged": []}
    return state


def touch(room: Path, stdin_text: str) -> None:
    """Count a tool call aimed inside a known folder: a path argument, or a shell command naming it.

    The full path is matched here and never stored; the trace keeps basenames.
    """
    since, folders = _known(room)
    if not folders:
        return
    payload = json.loads(stdin_text)
    tool = payload.get("tool_name")
    given = payload.get("tool_input")
    if not isinstance(given, dict):
        return
    if tool == "Bash":
        command = str(given.get("command") or "")
        hits = [name for name, _real, spelled in folders if _names(command, spelled)]
        how = "ran"
    else:
        raw = next((str(given[key]) for key in ("file_path", "notebook_path", "path") if given.get(key)), "")
        if not raw:
            return
        cwd = str(payload.get("cwd") or room)
        target = os.path.realpath(os.path.join(cwd, os.path.expanduser(raw)))
        hits = [name for name, real, _spelled in folders if target == real or target.startswith(real + os.sep)]
        how = "wrote" if tool in _WRITE_TOOLS else "read"
    if not hits:
        return
    state = _touched_state(room, since)
    for name in hits:
        counts = state["folders"].setdefault(name, {})
        counts[how] = counts.get(how, 0) + 1
    from .home import atomic_write
    atomic_write(room / "port" / TOUCHED, json.dumps(state) + "\n")


def known_nudge(room: Path) -> str:
    """Once per folder per body: the known folders this body touched and not yet asked about."""
    since, _folders = _known(room)
    state = _touched_state(room, since)
    fresh = [name for name in state["folders"] if name not in state["nudged"]]
    if not fresh:
        return ""
    parts = []
    for name in fresh:
        counts = state["folders"][name]
        did = ", ".join(f"{how} {counts[how]}" for how in ("read", "wrote", "ran") if counts.get(how))
        parts.append(f"{name}/ ({did})")
    wrote = any(state["folders"][name].get("wrote") or state["folders"][name].get("ran") for name in fresh)
    state["nudged"] = state["nudged"] + fresh
    from .home import atomic_write
    atomic_write(room / "port" / TOUCHED, json.dumps(state) + "\n")
    ask = "anything to tidy, or to note on its page?" if wrote else "anything worth noting on its page?"
    return f"you touched the known folder{'s' if len(fresh) > 1 else ''} {', '.join(parts)}: {ask}"


# --- the jack: a kept folder's HEAD moved, or a folder vanished, under a body --

HEADS = "library-heads.json"


def head(folder: Path) -> str:
    """A repo's HEAD sha from its files: the jack reads it at every boundary, so no subprocess."""
    git_dir = folder / ".git"
    try:
        text = (git_dir / "HEAD").read_text().strip()
        if not text.startswith("ref: "):
            return text
        ref = text[5:]
        try:
            return (git_dir / ref).read_text().strip()
        except OSError:
            for row in (git_dir / "packed-refs").read_text().splitlines():
                sha, _, name = row.partition(" ")
                if name == ref:
                    return sha
    except OSError:
        pass
    return ""


def _heads_state(room: Path, since: float) -> dict | None:
    try:
        state = json.loads((room / "port" / HEADS).read_text())
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) and state.get("since") == since else None


def changes(room: Path) -> str:
    """Lines for what moved under this body since its last boundary, each said once.

    A kept folder's HEAD moved by anyone but this body's ``lib commit``, or a
    folder vanished. The body's first boundary only records: what it woke to
    is in its wake.
    """
    listed = room / "port" / "library.json"
    since = listed.stat().st_mtime
    found = json.loads(listed.read_text())
    items = [i for i in found if isinstance(i, dict) and i.get("path")] if isinstance(found, list) else []
    if not items:
        return ""
    state = _heads_state(room, since)
    first = state is None
    state = state or {"since": since, "heads": {}, "gone": []}
    lines = []
    for item in items:
        name, folder = str(item.get("name")), Path(str(item["path"]))
        shown = f"{LINKS}/{name}" if item.get("kind") == "kept" else name
        if not folder.is_dir():
            if name not in state["gone"]:
                state["gone"].append(name)
                if not first:
                    lines.append(f"library: {shown} vanished under you; it is "
                                 + _missing(name, str(item["path"]), str(item.get("inode", ""))))
            continue
        if name in state["gone"]:
            state["gone"].remove(name)
        if item.get("kind") != "kept":
            continue
        now, was = head(folder), state["heads"].get(name, "")
        if now and now != was:
            state["heads"][name] = now
            if was and not first:
                lines.append(f"library: {shown} HEAD moved {was[:8]}..{now[:8]} under you, not by your lib commit;"
                             f" re-read what you rely on there (git -C {shown} log --stat {was[:8]}..)")
    from .home import atomic_write
    atomic_write(room / "port" / HEADS, json.dumps(state) + "\n")
    return "\n".join(lines)


def own_head(room: Path, name: str, sha: str) -> None:
    """``lib commit`` in a body: its own new HEAD is no news to the jack."""
    try:
        since = (room / "port" / "library.json").stat().st_mtime
        state = _heads_state(room, since)
        if state is None:
            return
        state["heads"][name] = sha
        from .home import atomic_write
        atomic_write(room / "port" / HEADS, json.dumps(state) + "\n")
    except OSError:
        return

"""A person's folders, kept or known, one page each at ``library/<name>.md`` in the self.

A page's frontmatter is the entry: ``kind`` (kept or known), ``path`` (a
folder on ``machine``) or ``source`` (a repo URL, cloned where a seat names
it), ``whose`` and ``for``. Below it is the resident's map of the folder.
The loom reads pages as they landed on main: ``lib add`` and ``lib drop``
change the room's self clone, and the body lands it. The one part the loom
writes is a page's ``## Work here`` block, from ``place.touched`` facts (the
places section at the end), in its own commit; the map above it is the body's.

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
# Each library entry takes at most one bounded line in the wake.
LINE_CAP = 200
SHOWN = 12
# The person's edits become a problem if they remain uncommitted this long.
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
    """A read in the person's folder: bounded, never raising, None when git could not run."""
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


def wake_text(home, room: Path | None = None) -> str:
    found, bad = entries(home)
    if not found and not bad:
        return ""
    here = machine()
    folders = [entry for entry in found if not entry.source]
    lines = []
    for entry in found:
        if entry.source:
            cloned = " (cloned)" if room is not None and (room / entry.name / ".git").exists() else ""
            text = f"repo {entry.name}{cloned}; for: {entry.purpose}"
            lines.append(text if len(text) <= LINE_CAP else text[:LINE_CAP - 1] + "…")
    lines += [line(entry, here) for entry in folders[:SHOWN]]
    if len(folders) > SHOWN:
        lines.append(f"and {len(folders) - SHOWN} more: `python -m brr.loom.runtime lib list`")
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

    Only symlinks are ever removed here, so the person's files stay in place.
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
        self.write_pages()

    def write_pages(self) -> None:
        """Land each library page whose ``## Work here`` block the touched facts have moved, in one loom commit."""
        from .ledger import read_facts
        facts = read_facts(self.home)
        count = sum(1 for fact in facts if fact.kind == "place.touched")
        if not count:
            return
        from .lifecycle import main_tip
        try:
            seen = (count, main_tip(self.home.root / "self"))
        except SelfError:
            return
        if seen == getattr(self, "pages_at", None):
            return
        from .loom import _fenced
        if _fenced(self.home, facts):
            return  # the machine holding the inbox lease writes the self
        changed = pages(self.home, facts)
        if changed:
            from .selfrepo import land_files
            names = ", ".join(sorted(Path(rel).stem for rel in changed))
            try:
                land_files(self.home.root, changed, f"library: work here, {names}\n\n"
                           "Written from place.touched facts (threads/touched-places).\n\n")
            except SelfError as exc:
                self.log(self.home, f"library: work pages not landed: {exc}")
                return
        self.pages_at = seen


# --- the stop: a body's own uncommitted writes ------------------------------

def written_since(room: Path, since: float, host: str | None = None) -> list[str]:
    """Uncommitted paths in this room's kept folders changed after ``since``.

    Read from the folder, not from tool calls, so a write through the shell
    counts too. The person's concurrent edits count as well; the stop names them and
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
    The same call is where a write, a clone or a repo created lands work in a
    place: ``place.touched``, once per place per body (``record``).
    """
    payload = json.loads(stdin_text)
    tool = payload.get("tool_name")
    given = payload.get("tool_input")
    if not isinstance(given, dict):
        return
    cwd = str(payload.get("cwd") or room)
    try:
        since, folders = _known(room)
    except (OSError, ValueError):
        since, folders = 0.0, []
    if tool == "Bash":
        command = str(given.get("command") or "")
        hits = [name for name, _real, spelled in folders if _names(command, spelled)]
        how = "ran"
        response = payload.get("tool_response")
        output = str(response.get("stdout") or "") if isinstance(response, dict) else str(response or "")
        shell_places(room, command, cwd, output)  # naming a folder is not work landed in it
    else:
        raw = next((str(given[key]) for key in ("file_path", "notebook_path", "path") if given.get(key)), "")
        if not raw:
            return
        target = os.path.realpath(os.path.join(cwd, os.path.expanduser(raw)))
        hits = [name for name, real, _spelled in folders if target == real or target.startswith(real + os.sep)]
        how = "wrote" if tool in _WRITE_TOOLS else "read"
        if how == "wrote":
            record_folder(room, "wrote", target)
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


# --- places: where a person's ask put a body to work ------------------------
#
# A place is an address that outlives the room and is not the resident's own:
# a forge repo by its URL, or a folder on a machine by its real path. Each act
# that lands work in one is appended by the caller that already sees it
# (open_thread, publish, the post-tool hook) as ``place.touched``, with the
# letters and wishes the loom woke the body for. The loom reads them back: the
# library page's ``## Work here`` block, and one wake line for a place the
# library does not hold yet (threads/touched-places in the self).

# Reaped by the OS: work written there outlives nothing.
EPHEMERAL = ("/tmp", "/private/tmp", "/var/tmp", "/var/folders", "/private/var/folders")
WISHES = "wishes.json"
PLACES = "places.json"
WORK = "## Work here"
WORK_ROWS = 10
OFFERED = 5
_ID = re.compile(r"[^A-Za-z0-9._-]+")


def repo_key(url: str) -> str | None:
    """One key for every spelling of a repo URL: ``repo:<host>/<path>``, or None for a local path."""
    text = url.strip()
    if "://" in text:
        from urllib.parse import urlsplit
        parts = urlsplit(text)
        host, path = (parts.hostname or parts.scheme or "").lower(), parts.path
        if parts.scheme == "file":
            host = "file"
    elif ":" in text and not text.startswith(("/", ".", "~")):
        host, _colon, path = text.partition(":")
        host = host.rpartition("@")[2].lower()
    else:
        return None
    path = path.strip("/").removesuffix(".git").rstrip("/")
    if not host or not path:
        return None
    return f"repo:{host}/{path if host == 'file' else path.lower()}"


def repo_url(key: str) -> str:
    """The URL a repo key is shown as: built from the key, so no credential typed in a URL survives."""
    host, _slash, path = key.removeprefix("repo:").partition("/")
    return f"file:///{path}" if host == "file" else f"https://{host}/{path}"


def folder_key(path: str, host: str | None = None) -> str:
    return f"folder:{host or machine()}:{path}"


def _key_path(key: str) -> tuple[str, str] | None:
    """``(machine, path)`` of a folder key."""
    if not key.startswith("folder:"):
        return None
    host, _colon, path = key.removeprefix("folder:").partition(":")
    return host, path


def entry_key(entry: Entry, host: str | None = None) -> str | None:
    if entry.source:
        return repo_key(entry.source)
    real = os.path.realpath(os.path.expanduser(entry.path))
    return folder_key(real, entry.machine or host or machine())


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _not_a_place(real: str, room: Path) -> bool:
    """The room, the loom home that holds it, and the OS's temp roots are the resident's or nobody's."""
    try:
        from .home import home_of_room
        own = os.path.realpath(home_of_room(room).root)
    except ValueError:
        own = os.path.realpath(room)
    return any(_under(real, os.path.realpath(root)) for root in (own, str(room), *EPHEMERAL))


def folder_root(target: str, libraries: list[str]) -> tuple[str, str]:
    """The place a written path belongs to, and how it was found: a library folder
    holding it (``library``), the nearest ancestor with a ``.git`` below the user's
    home (``git``), else its own folder (``loose``). A loose folder never holds
    another place: ``~/Documents`` reached by one note is not a root for the repos
    under it."""
    for real in libraries:
        if _under(target, real):
            return real, "library"
    start = Path(target) if os.path.isdir(target) else Path(target).parent
    user = Path.home().resolve()
    for folder in (start, *start.parents):
        if folder == user or folder == folder.parent:
            break
        if (folder / ".git").exists():
            return str(folder), "git"
    return str(start), "loose"


def _tool_state(root: str) -> bool:
    """The user's home itself, and anything under a dot-folder in it (``~/.claude``,
    ``~/.config``): an install's own state, not a place a person's ask names."""
    user = os.path.realpath(Path.home())
    if root == user:
        return True
    if not _under(root, user):
        return False
    return os.path.relpath(root, user).split(os.sep)[0].startswith(".")


def _cause(room: Path) -> tuple[list[str], list[str]]:
    """The letters and wishes this body was woken for, as the loom wrote them at its wake."""
    def ids(name: str, field: str | None) -> list[str]:
        try:
            rows = json.loads((room / "port" / name).read_text())
        except (OSError, ValueError):
            return []
        found = [str(row.get(field) if field else row) for row in rows if row] if isinstance(rows, list) else []
        return [item for item in found if item and item != "None"]
    # A thread's open wishes are not the one worked on: name one only when it is
    # the only one, else the letters say it.
    woken = ids(WISHES, None)
    return ids("owed.json", "id"), woken if len(woken) == 1 else []


def record(room: Path, act: str, key: str, shown: str, *, thread: str = "",
           wishes: list[str] | None = None, root: str = "") -> bool:
    """Append ``place.touched`` for this room's body: once per thread, act and place, the first cause wins.

    ``root``: for a folder, how its root was found (``folder_root``); only a
    ``git`` or ``library`` root holds the places under it.

    True when a fact was written. Never raises: a lost touch costs a page row.
    """
    try:
        from brr.daemon2.facts import Fact
        from .home import atomic_write, home_of_room
        from .ledger import LedgerConflict, append
        room = Path(room)
        strand = room.name
        thread = thread or os.environ.get("BRNRD_THREAD", "")
        ident = f"place.touched:{strand}:{_ID.sub('_', thread)}:{act}:{_ID.sub('_', key)}"
        seen_path = room / "port" / PLACES
        try:
            seen = json.loads(seen_path.read_text())
        except (OSError, ValueError):
            seen = []
        if ident in seen:
            return False
        letters, woken = _cause(room)
        data = {"place": key, "kind": key.split(":", 1)[0], "act": act, "shown": shown,
                "thread": thread, "strand": strand,
                "letters": letters, "wishes": list(wishes) if wishes is not None else woken}
        if root:
            data["root"] = root
        try:
            append(home_of_room(room), Fact(kind="place.touched", by=f"strand:{strand}", id=ident, data=data))
        except LedgerConflict:
            pass  # an earlier gen of this strand said it, with another cause
        if seen_path.parent.is_dir():
            atomic_write(seen_path, json.dumps([*seen, ident]) + "\n")
        return True
    except Exception:  # noqa: BLE001 - a hook and a verb must never fail on this
        return False


def record_repo(room: Path, act: str, source: str, *, thread: str = "",
                wishes: list[str] | None = None, home=None) -> bool:
    """A repo named by URL or by library name."""
    url = source
    if repo_key(url) is None and home is not None:
        url = sources(home).get(source, "")
    key = repo_key(url) if url else None
    return bool(key) and record(room, act, key, repo_url(key), thread=thread, wishes=wishes)


def _library_reals(room: Path) -> list[str]:
    try:
        found = json.loads((room / "port" / "library.json").read_text())
    except (OSError, ValueError):
        return []
    return [os.path.realpath(os.path.expanduser(str(item["path"]))) for item in found
            if isinstance(item, dict) and item.get("path")] if isinstance(found, list) else []


def record_folder(room: Path, act: str, target: str, made: bool = False) -> bool:
    """A path written under; ``made``: the folder an act created (a clone's), its own root."""
    real = os.path.realpath(target)
    if _not_a_place(real, room):
        return False
    libraries = _library_reals(room)
    root, how = (real, "git") if made else folder_root(real, libraries)
    if how != "library" and _tool_state(root):
        return False
    return record(room, act, folder_key(root), _home_short(root), root=how)


_SEGMENT = re.compile(r"&&|\|\||[;|\n]")
_GH_URL = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+")


def _words(command: str) -> list[list[str]]:
    import shlex
    found = []
    for part in _SEGMENT.split(command):
        try:
            found.append(shlex.split(part))
        except ValueError:
            continue
    return found


def _operands(words: list[str]) -> list[str]:
    return [word for word in words if not word.startswith("-")]


def _cloned(folder: str) -> bool:
    return os.path.isdir(os.path.join(folder, ".git"))


def shell_places(room: Path, command: str, cwd: str, output: str) -> None:
    """``git clone``, ``gh repo clone`` and ``gh repo create`` in a shell command: the repo, and the folder made."""
    for words in _words(command):
        base = [os.path.basename(word) for word in words]
        if "git" in base and "clone" in words[base.index("git"):]:
            rest = words[words.index("clone", base.index("git")) + 1:]
            given = _operands(rest)
            url = next((word for word in given if repo_key(word)), None)
            if url is None:
                continue
            after = given[given.index(url) + 1:]
            dest = after[0] if after else os.path.basename(url.rstrip("/")).removesuffix(".git")
            made = os.path.join(cwd, os.path.expanduser(dest))
            if not _cloned(made):
                continue  # a failed clone landed nothing
            record_repo(room, "cloned", url)
            record_folder(room, "cloned", made, made=True)
        elif "gh" in base and words[base.index("gh") + 1:base.index("gh") + 3] in (["repo", "create"],
                                                                                 ["repo", "clone"]):
            at = base.index("gh")
            verb = words[at + 2]
            given = _operands(words[at + 3:])
            if verb == "create":
                shown = _GH_URL.search(output or "")  # gh prints the URL once the repo exists
                if shown:
                    record_repo(room, "created", shown.group(0))
                continue
            if not given:
                continue
            dest = given[1] if len(given) > 1 else given[0].rstrip("/").rpartition("/")[2].removesuffix(".git")
            made = os.path.join(cwd, os.path.expanduser(dest))
            if not _cloned(made):
                continue
            url = given[0] if repo_key(given[0]) else f"https://github.com/{given[0]}" if "/" in given[0] else ""
            if url:
                record_repo(room, "cloned", url)
            record_folder(room, "cloned", made, made=True)


def touched(facts) -> dict[str, list]:
    """``place.touched`` facts by place, oldest first."""
    from .ledger import kind_facts
    found: dict[str, list] = {}
    for fact in kind_facts(facts, "place.touched"):
        found.setdefault(str(fact.data.get("place")), []).append(fact)
    return found


def _holder(key: str, roots: list[str]) -> str | None:
    """The outermost of ``roots`` (folder keys) that holds folder ``key``, other than itself."""
    mine = _key_path(key)
    if mine is None:
        return None
    holders = [root for root in roots if root != key and (where := _key_path(root)) is not None
               and where[0] == mine[0] and _under(mine[1], where[1])]
    return min(holders, key=len) if holders else None


def _row(fact) -> str:
    data = fact.data
    when = str(fact.at)[5:16].replace("T", " ") + "Z"
    parts = [when, str(data.get("act")), f"thread {data.get('thread')}" if data.get("thread") else ""]
    parts += [str(w) for w in (data.get("wishes") or [])[:2]]
    parts += [f"letter {str(letter).rpartition(':')[2]}" for letter in (data.get("letters") or [])[:2]]
    return "- " + " · ".join(part for part in parts if part)


def work_block(facts_here: list) -> str:
    rows = sorted(facts_here, key=lambda f: str(f.at), reverse=True)[:WORK_ROWS]
    return (f"{WORK}\n\nWritten by the loom from `place.touched` facts, newest {WORK_ROWS}; "
            "the ledger keeps every one.\n\n" + "".join(_row(f) + "\n" for f in rows))


def with_work(text: str, block: str) -> str:
    """The page with its loom block replaced; the body's map above it is left as it is."""
    head, _mark, _rest = text.partition(f"\n{WORK}\n")
    return head.rstrip("\n") + "\n\n" + block


def pages(home, facts) -> dict[str, str]:
    """``library/<name>.md`` -> new text, for each page whose work block is not what the facts say."""
    found = entries(home)[0]
    if not found:
        return {}
    here = machine()
    by_place = touched(facts)
    if not by_place:
        return {}
    keys = {entry.name: entry_key(entry, here) for entry in found}
    roots = [key for key in keys.values() if key and key.startswith("folder:")]
    changed = {}
    self_dir = home.root / "self"
    for entry in found:
        key = keys[entry.name]
        mine = [f for place, rows in by_place.items()
                if place == key or (key in roots and _holder(place, [key]) == key) for f in rows]
        if not mine:
            continue
        rel = f"library/{entry.name}.md"
        text = git(self_dir, "show", f"main:{rel}", check=False).stdout
        if not text:
            continue
        new = with_work(text, work_block(mine))
        if new != text:
            changed[rel] = new
    return changed


def unlisted(facts, found: list[Entry], host: str | None = None) -> list[tuple[str, list]]:
    """Touched places no library page holds, not yet offered or dismissed: outermost folder first."""
    from .ledger import kind_facts
    host = host or machine()
    listed = {entry_key(entry, host) for entry in found} - {None}
    said = {str(f.data.get("place")) for f in kind_facts(facts, "place.offered", "place.dismissed")}
    by_place = touched(facts)
    # What holds a place: a library folder, or a touched repo folder not yet said.
    # A loose folder holds nothing, and an offered or dismissed one hides no new child.
    folders = [key for key in listed if key.startswith("folder:")] + [
        key for key, rows in by_place.items() if key.startswith("folder:") and key not in said
        and any(f.data.get("root") in ("git", "library") for f in rows)]
    out = []
    for key, rows in by_place.items():
        if key in listed or key in said or _holder(key, folders) is not None:
            continue
        held = [f for place, more in by_place.items() if key in folders
                and _holder(place, [key]) == key for f in more]
        out.append((key, rows + held))
    return sorted(out, key=lambda pair: str(pair[1][0].at))


def _name_for(key: str) -> str:
    tail = key.rstrip("/").rpartition("/")[2] or "place"
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", tail).strip("-.") or "place"
    return name if NAME.fullmatch(name) else "place"


def offer_text(home, facts, found: list[Entry] | None = None) -> str:
    """The home wake's lines for touched places outside the library, each said once (``place.offered``)."""
    from brr.daemon2.facts import Fact
    from .ledger import LedgerConflict, append
    found = entries(home)[0] if found is None else found
    fresh = unlisted(facts, found)
    if not fresh:
        return ""
    lines = []
    install = home.install_id()
    for key, rows in fresh[:OFFERED]:
        first = rows[0].data
        shown = str(first.get("shown") or key)
        why = first.get("wishes") or [str(letter).rpartition(":")[2] for letter in first.get("letters") or []]
        target = shown if key.startswith("repo:") else _key_path(key)[1]
        lines.append(
            f"touched, not in the library: {shown} ({first.get('act')} on thread {first.get('thread') or '?'}"
            + (f", for {', '.join(map(str, why[:2]))}" if why else "")
            + (f"; {len(rows)} acts" if len(rows) > 1 else "")
            + f"); one word from the person: add, `lib add {_name_for(key)} {target} --kind known "
            f"--whose <person> --for \"<what it is>\"`, or dismiss, `lib dismiss {key}`")
        try:
            append(home, Fact(kind="place.offered", by=f"loom:{install}",
                              id=f"place.offered:{_ID.sub('_', key)}", data={"place": key}))
        except LedgerConflict:
            pass
    if len(fresh) > OFFERED:
        lines.append(f"and {len(fresh) - OFFERED} more touched places, offered at the next wake")
    return "".join(f"{text}\n" for text in lines)


def dismiss(room: Path, key: str) -> None:
    """The person's word: this place is not one to remember. It is never offered again."""
    from brr.daemon2.facts import Fact
    from .home import home_of_room
    from .ledger import append
    if not (key.startswith("repo:") or _key_path(key)):
        raise LibraryError(f"{key}: not a place key (repo:… or folder:…)")
    room = Path(room)
    if room.name == "self" and room.parent.parent.name == "rooms":
        room = room.parent
    append(home_of_room(Path(room)), Fact(kind="place.dismissed", by=f"strand:{Path(room).name}",
                                          id=f"place.dismissed:{_ID.sub('_', key)}", data={"place": key}))

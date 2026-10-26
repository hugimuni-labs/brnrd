"""A person's folders, kept or known, one page each at ``library/<name>.md`` in the self.

A page's frontmatter is the entry: ``kind`` (kept or known), ``path`` (a
folder on ``machine``) or ``source`` (a repo URL, cloned where a seat names
it), ``whose`` and ``for``. Below it is the resident's map of the folder.
The loom reads pages as they landed on main and writes none of them: ``lib
add`` and ``lib drop`` change the room's self clone, and the body lands it.
"""

from __future__ import annotations

import os
import re
import socket
from dataclasses import dataclass
from pathlib import Path

from .selfrepo import git

KINDS = ("kept", "known")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# The wake budget is 24 KB for everything; a library of any size stays a few KB.
LINE_CAP = 200
SHOWN = 12


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
    return f"at {shown}"


def line(entry: Entry, here: str | None = None) -> str:
    here = here or machine()
    whose = f"{entry.whose}'s " if entry.whose else ""
    may = "I commit here" if entry.kind == "kept" else "read and work on request, tidy after"
    text = f"{entry.kind} {entry.name}: {whose}{_where(entry, here)}; {may}"
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


def add(self_dir: Path, name: str, target: str, *, kind: str, whose: str, purpose: str) -> Path:
    """Write ``library/<name>.md`` in ``self_dir`` and commit it there.

    A target with a scheme or ``host:path`` is a source to clone; anything
    else is a folder on this machine, recorded with its device and inode so
    a move can be traced.
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
        stat = folder.stat()
        entry = Entry(name=name, kind=kind, whose=whose, purpose=purpose, path=str(folder),
                      machine=machine(), inode=f"{stat.st_dev}:{stat.st_ino}")
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(page(entry))
    rel = f"library/{name}.md"
    git(self_dir, "add", "--", rel)
    git(self_dir, "commit", "--only", "-m", f"library: add {name} ({kind})", "--", rel)
    return dest


def drop(self_dir: Path, name: str) -> None:
    rel = f"library/{name}.md"
    if not NAME.fullmatch(name) or not (self_dir / rel).is_file():
        raise LibraryError(f"no {rel}")
    git(self_dir, "rm", "-q", "--", rel)
    git(self_dir, "commit", "--only", "-m", f"library: drop {name}", "--", rel)

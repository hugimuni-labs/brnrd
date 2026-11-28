"""``brnrd add [folder]``: the project in this folder joins the resident ``brnrd setup`` made.

No pairing and no account home: setup already made the loom home, and a
project joins it as a library page on the self's main (``library/<name>.md``),
the same page a body writes with ``lib add``. A checkout with an ``origin`` is
entered by its source, so a thread can name it with ``--repo <name>`` and its
work goes out as a PR; a folder without one is entered as known, by path.
Run twice, it says the project is already there and changes nothing.
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

from . import library
from .selfrepo import SelfError, git, land_files


def _toplevel(folder: Path) -> Path:
    """The checkout's root when ``folder`` sits inside one, else ``folder``."""
    proc = subprocess.run(["git", "-C", str(folder), "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True, check=False)
    top = proc.stdout.strip() if proc.returncode == 0 else ""
    return Path(top).resolve() if top else folder


def _origin(folder: Path) -> str:
    proc = subprocess.run(["git", "-C", str(folder), "remote", "get-url", "origin"],
                          capture_output=True, text=True, check=False)
    url = proc.stdout.strip() if proc.returncode == 0 else ""
    return url if url and library._is_source(url) else ""


def _slug(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-._")
    return slug if slug and slug != "self" else "project"


def _name_for(source: str, folder: Path) -> str:
    if source:
        tail = source.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
        return _slug(tail.removesuffix(".git"))
    return _slug(folder.name)


def _person(self_dir: Path) -> str:
    """The person setup seeded: the one folder under ``people/`` on main, else the OS login."""
    proc = git(self_dir, "ls-tree", "-d", "--name-only", "main", "people/", check=False)
    names = [line.removeprefix("people/") for line in proc.stdout.split() if line]
    if len(names) == 1:
        return names[0]
    import getpass
    return _slug(getpass.getuser().lower())


def add_project(home: Path, folder: Path) -> tuple[str, str]:
    """Land ``folder``'s library page on the self's main. ``(name, what happened)``.

    ``what happened`` is ``added`` or ``already``. Raises ``SelfError`` when
    there is no self (setup has not run) and ``LibraryError`` on a folder
    that cannot be entered.
    """
    from .home import Home
    self_dir = Path(home) / "self"
    if not (self_dir / ".git").exists():
        raise SelfError(f"no brnrd home at {home}; run brnrd setup first")
    folder = folder.expanduser().resolve()
    if not folder.is_dir():
        raise library.LibraryError(f"{folder}: not a folder")
    folder = _toplevel(folder)
    source = _origin(folder)
    found, _bad = library.entries(Home(Path(home)))
    for entry in found:
        if (source and entry.source == source) or (not source and entry.path == str(folder)):
            return entry.name, "already"
    # Every page on main takes its name, an unreadable one included.
    listed = git(self_dir, "ls-tree", "--name-only", "main", "library/", check=False).stdout
    taken = {p.removeprefix("library/").removesuffix(".md") for p in listed.split()}
    base = _name_for(source, folder)
    name = next(n for n in (base, *(f"{base}-{i}" for i in range(2, 100))) if n not in taken)
    person = _person(self_dir)
    shown = library._home_short(str(folder))
    if source:
        entry = library.Entry(name=name, kind="known", whose=person, source=source,
                              purpose=f"the project at {shown}, {library.ADDED}")
    else:
        stat = folder.stat()
        entry = library.Entry(name=name, kind="known", whose=person, path=str(folder),
                              machine=library.machine(), inode=f"{stat.st_dev}:{stat.st_ino}",
                              purpose=f"a project folder, {library.ADDED}")
    land_files(Path(home), {f"library/{name}.md": library.page(entry)},
               f"library: add {name} (known), from brnrd add\n\n")
    return name, "added"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="brnrd add",
        description="Add the project in this folder (or FOLDER) to the brnrd that "
                    "`brnrd setup` set up. No pairing: it uses the same home.")
    parser.add_argument("folder", nargs="?", default=".")
    parser.add_argument("--home", help="the loom home (default: the one setup made)")
    args = parser.parse_args(argv)
    from .service import _service_home
    home, _why = _service_home(args.home)
    try:
        name, what = add_project(home, Path(args.folder))
    except (SelfError, library.LibraryError, OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"brnrd add: {exc}") from None
    if what == "already":
        print(f"{name} is already in brnrd's library; nothing changed.")
    else:
        print(f"Added {name} to brnrd's library.")
    print(f"Ask brnrd to work on it by name, e.g. \"in {name}, …\".")
    return 0

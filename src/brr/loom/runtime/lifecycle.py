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


def read_thread(home: Home, thread: str, ref: str = "main") -> dict:
    """Read a landed README, never a room's unlanded status."""
    path = home.thread_dir(thread) / "README.md"
    try:
        if (home.root / "self/.git").exists():
            proc = git(home.root / "self", "show", f"{ref}:threads/{thread}/README.md", check=False)
            if proc.returncode:
                return {}
            text = proc.stdout
        else:
            text = path.read_text()
        data = parse_readme(text)
        plan = []
        inside = False
        for line in text.splitlines():
            if line.startswith("## "):
                inside = line == "## Plan"
            elif inside:
                row = re.match(r"^\s*- \[([ xX])\] (.+)$", line)
                if row:
                    plan.append(row.group(1).lower() == "x")
        data["plan"] = f"{sum(plan)}/{len(plan)}" if plan else "—"
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


def open_thread(room: Path, thread: str, sender: str, text: str,
                body_choice: str | None = None) -> None:
    from .port import _current_label
    check_thread_id(thread)
    own = sender_threads(read_facts(home_of_room(room))).get(sender)
    if own is None:
        raise PortError("send: opening a thread requires a sender with a thread")
    if _current_label(resolve_clone(room), sender).taint:
        raise PortError("send: a tainted strand cannot open a thread on main; send a clean letter to inbox instead")
    if body_choice is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*/[^\s]+", body_choice):
        raise PortError("send: --body must be shell/core")
    first = text.split("\n", 1)[0]
    readme = (f"---\nid: {thread}\nstatus: open\ntense: plan\n"
              f"opened-by: thread:{own}\n---\n# {first}\n")
    parse_readme(readme)

    def edit(clone):
        path = f"threads/{thread}/README.md"
        if git(clone, "cat-file", "-e", f"origin/main:{path}", check=False).returncode == 0:
            raise PortError(f"send: thread {thread} was opened meanwhile; send to the existing thread")
        files = {path: readme}
        if body_choice is not None:
            files[f"threads/{thread}/seat"] = f"body: {body_choice}\n"
        return files

    land(room, f"open {thread} from thread:{own}", edit)


def settle(room: Path, thread: str, why: str, retire: bool = False) -> None:
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
    land_status(room, thread, "retired" if retire else "settled", f"settle {thread}: {why}")


_closing: dict[tuple[str, str, str], tuple[str, str, str]] = {}


def closing_commit(home: Home, thread: str) -> tuple[str, str, str]:
    """The commit that last changed the status line: (sha, why, writing strand).

    The loom asks on every tick, and the search walks the README's whole
    history, so the answer is kept per README blob on main.
    """
    path = f"threads/{thread}/README.md"
    blob = git(home.root / "self", "rev-parse", f"main:{path}", check=False).stdout.strip()
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

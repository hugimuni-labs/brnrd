"""Thread decisions are README commits, landed through the self's existing door."""

from pathlib import Path

from .home import Home, home_of_room, strand_of_room, check_thread_id
from .ledger import read_facts
from .merge import send_to_self
from .port import PortError
from .project import sender_threads
from .selfrepo import ReadmeError, git, parse_readme, resolve_clone

CLOSED = {"settled", "retired"}


def read_thread(home: Home, thread: str) -> dict:
    """Read main, never a room's unlanded status."""
    path = home.thread_dir(thread) / "README.md"
    try:
        if (home.root / "self/.git").exists():
            proc = git(home.root / "self", "show", f"main:threads/{thread}/README.md", check=False)
            if proc.returncode:
                return {}
            text = proc.stdout
        else:
            text = path.read_text()
        return parse_readme(text)
    except (OSError, ReadmeError):
        return {}


def own_thread(room: Path) -> str:
    thread = sender_threads(read_facts(home_of_room(room))).get(strand_of_room(room))
    if thread is None:
        raise PortError("room has no thread")
    return thread


def land_status(room: Path, thread: str, status: str, message: str) -> None:
    check_thread_id(thread)
    clone = resolve_clone(room)
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
    (clone / path).write_text(changed)
    if git(clone, "diff", "HEAD", "--quiet", "--", path, check=False).returncode:
        git(clone, "add", "--", path)
        git(clone, "commit", "--only", "-m", message, "--", path)
    outcome = send_to_self(clone)
    if outcome.status != "merged":
        raise PortError(outcome.stderr or f"landing {outcome.status}: {', '.join(outcome.files)}")


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

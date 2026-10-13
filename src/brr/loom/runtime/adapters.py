"""A shell is a folder; a home's folder wins over the bundled one."""

from pathlib import Path
import re


def resolve(home, shell: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", shell):
        raise ValueError(f"invalid body shell {shell!r}")
    local = home.root / "loom" / "bodies" / shell
    path = local if local.is_dir() else Path(__file__).parent / "bodies" / shell
    if not (path / "run").is_file():
        raise ValueError(f"no body folder for {shell!r}")
    return path.resolve()


_SEAT_KEYS = {"body", "repo", "watch"}
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class RepoSeatError(ValueError):
    """A seat's ``repo:`` lines cannot be used."""


def seat_text(home, thread: str) -> str:
    """The seat as it landed on main, or the file when the self is not git yet."""
    from .selfrepo import git
    if (home.root / "self/.git").exists():
        proc = git(home.root / "self", "show", f"main:threads/{thread}/seat", check=False)
        return proc.stdout if proc.returncode == 0 else ""
    path = home.thread_dir(thread) / "seat"
    return path.read_text() if path.exists() else ""


def _entries(text: str):
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, colon, value = line.partition(":")
        key = key.strip()
        if not colon or key not in _SEAT_KEYS:
            raise ValueError(f"invalid seat line: {line!r}")
        yield key, value.strip().strip('"')


def repo_dirname(source: str) -> str:
    """Last path segment of a repo source, without a trailing ``.git``."""
    text = source.strip().rstrip("/")
    if not text:
        raise RepoSeatError("repo source is empty")
    if text.startswith("-"):
        raise RepoSeatError(f"repo source {text!r} begins with '-'")
    if ":" not in text and not text.startswith("/"):
        # git resolves a relative path against whichever directory it runs
        # in, and the mirror, the room and the checkout are three of them.
        raise RepoSeatError(f"repo source {text!r} is a relative path; write it from /")
    name = text.rsplit("/", 1)[-1]
    if name.endswith(".git"):
        name = name[: -len(".git")]
    if name == "self" or not _NAME.fullmatch(name):
        raise RepoSeatError(f"repo name {name!r} from {source!r} is not a directory name")
    return name


def repos(text: str) -> list[tuple[str, str]]:
    """``repo:`` lines of a seat: ``(directory name, source)`` in seat order.

    Two lines whose sources name the same directory are refused. ``body:`` and
    ``watch:`` are ignored here.
    """
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key, value in _entries(text):
        if key != "repo":
            continue
        name = repo_dirname(value)
        if name in seen:
            raise RepoSeatError(f"repo name {name!r} is declared twice")
        seen.add(name)
        found.append((name, value))
    return found


def at_birth(home, thread: str, default: tuple[str, str],
             text: str | None = None) -> tuple[str, str]:
    from .config import load_config
    if text is None:
        text = seat_text(home, thread)
    choice = None
    for key, value in _entries(text):
        if key == "body":
            choice = value
    choice = choice or load_config(home.root).body
    if not choice:
        return default
    shell, slash, core = str(choice).partition("/")
    if not slash or not core.strip():
        raise ValueError(f"body must be shell/core: {choice!r}")
    return shell.strip(), core.strip()

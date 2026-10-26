"""A shell is a folder; a home's folder wins over the bundled one."""

import os
from pathlib import Path
import re


def resolve(home, shell: str, group: str = "bodies") -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", shell):
        raise ValueError(f"invalid {group} name {shell!r}")
    local = home.root / "loom" / group / shell
    override = local.is_dir() if group == "bodies" else (local / "carries").is_file()
    path = local if override else Path(__file__).parent / group / shell
    if not path.is_dir() or (group == "bodies" and not (path / "run").is_file()):
        raise ValueError(f"no {group} folder for {shell!r}")
    return path.resolve()


def parse_body(choice) -> tuple[str, str]:
    """``(shell, core)`` from ``shell/core`` or a bare ``shell``.

    A bare shell has the empty core: the harness's own model choice.
    """
    shell, slash, core = str(choice).strip().partition("/")
    shell, core = shell.strip(), core.strip()
    if not shell or (slash and not core):
        raise ValueError(f"body must be shell or shell/core: {choice!r}")
    return shell, core


def body_label(shell: str, core: str) -> str:
    """``shell/core``, or the bare shell when the harness picks the model."""
    return f"{shell}/{core}" if core else shell


def codex_model() -> str | None:
    """The person's own codex default: top-level ``model`` in codex's config."""
    path = Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser() / "config.toml"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        line = line.strip()
        if line.startswith("["):
            return None
        match = re.fullmatch(r'model\s*=\s*"([^"]+)"\s*(#.*)?', line)
        if match:
            return match.group(1)
    return None


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
        raise RepoSeatError(f"repo source {text!r} is neither a URL nor a path from /")
    name = text.rsplit("/", 1)[-1]
    if name.endswith(".git"):
        name = name[: -len(".git")]
    if name == "self" or not _NAME.fullmatch(name):
        raise RepoSeatError(f"repo name {name!r} from {source!r} is not a directory name")
    return name


def repos(text: str, home=None) -> list[tuple[str, str]]:
    """``repo:`` lines of a seat: ``(directory name, source)`` in seat order.

    A bare name is a library entry with a source (``library/<name>.md``),
    resolved through ``home``; with no home it is checked as a name only.
    Two lines whose sources name the same directory are refused. ``body:`` and
    ``watch:`` are ignored here.
    """
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    shelf = None
    for key, value in _entries(text):
        if key != "repo":
            continue
        if _NAME.fullmatch(value) and value != "self":
            name = value
            if home is not None:
                if shelf is None:
                    from .library import sources
                    shelf = sources(home)
                if name not in shelf:
                    raise RepoSeatError(f"repo {name!r}: no library/{name}.md with a source on main")
                value = shelf[name]
        else:
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
    return parse_body(choice)

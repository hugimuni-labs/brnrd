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


def walled(folder: Path, tail: str) -> bool:
    return any(re.search(line, tail, re.I) for line in (folder / "wall").read_text().splitlines()
               if line.strip())


def at_birth(home, thread: str, default: tuple[str, str]) -> tuple[str, str]:
    from .config import load_config
    readme = (home.thread_dir(thread) / "README.md").read_text()
    match = re.search(r"^body: *([^\n]+)$", readme.split("\n---", 1)[0], re.M)
    choice = match.group(1).strip().strip('"') if match else None
    choice = choice or load_config(home.root).body
    if not choice:
        return default
    shell, slash, core = str(choice).partition("/")
    if not slash or not core.strip():
        raise ValueError(f"body must be shell/core: {choice!r}")
    return shell.strip(), core.strip()

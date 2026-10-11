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


def at_birth(home, thread: str, default: tuple[str, str]) -> tuple[str, str]:
    from .config import load_config
    from .selfrepo import git
    if (home.root / "self/.git").exists():
        proc = git(home.root / "self", "show", f"main:threads/{thread}/seat", check=False)
        text = proc.stdout if proc.returncode == 0 else ""
    else:
        path = home.thread_dir(thread) / "seat"
        text = path.read_text() if path.exists() else ""
    choice = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, colon, value = line.partition(":")
        if not colon or key.strip() not in {"body", "repo", "watch"}:
            raise ValueError(f"invalid seat line: {line!r}")
        if key.strip() == "body":
            choice = value.strip().strip('"')
    choice = choice or load_config(home.root).body
    if not choice:
        return default
    shell, slash, core = str(choice).partition("/")
    if not slash or not core.strip():
        raise ValueError(f"body must be shell/core: {choice!r}")
    return shell.strip(), core.strip()

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

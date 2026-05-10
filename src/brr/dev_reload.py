"""Developer reload support for the foreground daemon."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable

_REEXEC_ENV = "BRR_REEXEC"
_WATCH_SUFFIXES = {".py", ".md"}
_WATCH_NAMES = {"Dockerfile"}


class DevReloadWatcher:
    """Cheap polling watcher for brr's own package files."""

    def __init__(self, package_dir: Path, extra_paths: Iterable[Path] = ()):
        self.package_dir = package_dir.resolve()
        self.extra_paths = tuple(path.resolve() for path in extra_paths)
        self._snapshot = self._take_snapshot()

    @classmethod
    def for_repo(cls, repo_root: Path) -> "DevReloadWatcher":
        """Create a watcher for the installed brr package.

        In editable installs, ``package_dir`` is normally the checkout's
        ``src/brr`` directory. When that source-layout root is visible,
        include ``pyproject.toml`` as packaging metadata that should
        prompt the operator to re-evaluate the editable install.
        """
        package_dir = Path(__file__).resolve().parent
        extra_paths: list[Path] = []
        project_root = package_dir.parent.parent
        if (
            package_dir.parent.name == "src"
            and (project_root / "pyproject.toml").exists()
        ):
            extra_paths.append(project_root / "pyproject.toml")
        elif (repo_root / "src" / "brr").resolve() == package_dir:
            pyproject = repo_root / "pyproject.toml"
            if pyproject.exists():
                extra_paths.append(pyproject)
        return cls(package_dir, extra_paths)

    def changed(self) -> bool:
        """Return True once per observed snapshot change."""
        current = self._take_snapshot()
        if current == self._snapshot:
            return False
        self._snapshot = current
        return True

    def _take_snapshot(self) -> tuple[tuple[str, int, int], ...]:
        entries: list[tuple[str, int, int]] = []
        if self.package_dir.exists():
            for path in self.package_dir.rglob("*"):
                if not _should_watch(path):
                    continue
                rel_path = path.relative_to(self.package_dir).as_posix()
                entry = _stat_entry(path, f"package/{rel_path}")
                if entry is not None:
                    entries.append(entry)
        for path in self.extra_paths:
            key = f"extra/{path.as_posix()}"
            entries.append(_stat_entry(path, key) or (key, -1, -1))
        return tuple(sorted(entries))


def _should_watch(path: Path) -> bool:
    if not path.is_file():
        return False
    return path.suffix in _WATCH_SUFFIXES or path.name in _WATCH_NAMES


def _stat_entry(path: Path, key: str) -> tuple[str, int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return (key, stat.st_size, stat.st_mtime_ns)


def is_reexec_for_current_process(pid: int | None) -> bool:
    """Return whether an existing PID file belongs to this re-exec."""
    return (
        pid == os.getpid()
        and os.environ.get(_REEXEC_ENV) == "1"
    )


def clear_reexec_marker() -> None:
    """Remove the internal re-exec marker from the running daemon env."""
    os.environ.pop(_REEXEC_ENV, None)


def reexec() -> None:
    """Replace the current process image with a fresh Python import."""
    env = os.environ.copy()
    env[_REEXEC_ENV] = "1"
    argv = [sys.executable, *sys.argv]
    os.execve(sys.executable, argv, env)

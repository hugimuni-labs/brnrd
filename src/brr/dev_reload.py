"""Developer reload support for the foreground daemon."""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Iterable

_REEXEC_ENV = "BRR_REEXEC"
_WATCH_SUFFIXES = {".py", ".md"}
_WATCH_NAMES = {"Dockerfile"}

# Code — and *only* code — is frozen into the process image at import time.
#
# This distinction is the whole of ``image_is_stale`` below, and it is the
# distinction that hid a live bug for a week.  Prompt prose lives in
# ``src/brr/prompts/*.md`` and is ``read_text()``-ed fresh on every assembly
# (``prompts.py``), so an edit to it reaches the very next wake this daemon
# assembles, stale image or not.  Prompt *logic* — the boot kernel renderer
# (``bootscore.format_kernel``), the orientation builder — is Python, and a
# running daemon renders it from the image it imported at start.
#
# Until #386 the boot was almost entirely prose, so "the daemon reads the
# checkout fresh" was true enough to reason with, and ``daemon.py``'s spawn
# dispatch was un-gated on a pending reload on exactly that premise.  #386
# moved the boot's opening from data into code, which moved it from the
# fresh-read path onto the frozen-image path — and invalidated the premise
# in silence, because the ``.md`` half went on working.
_IMAGE_SUFFIXES = {".py"}


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


_IMAGE_FINGERPRINT: tuple[tuple[str, str], ...] | None = None


def _image_snapshot() -> tuple[tuple[str, str], ...]:
    """Hash every code file that this process's image was built from.

    Content, not ``(size, mtime)`` — deliberately unlike :class:`DevReloadWatcher`,
    which may cheaply over-trigger because a spurious re-exec costs nothing.  A
    spurious *warning* costs a great deal: it renders in the kernel, above the
    ``next:`` list, on a wake that is in fact perfectly healthy.  Under mtime,
    editing a file and reverting it would cry stale forever, even though the
    bytes on disk are once again the bytes this image imported.  A drift line
    that cries wolf is worse than no drift line — it teaches the reader to skim
    the one line that exists to save them.
    """
    package_dir = Path(__file__).resolve().parent
    entries: list[tuple[str, str]] = []
    for path in package_dir.rglob("*"):
        if not path.is_file() or path.suffix not in _IMAGE_SUFFIXES:
            continue
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        entries.append((path.relative_to(package_dir).as_posix(), digest))
    return tuple(sorted(entries))


def capture_image_fingerprint() -> None:
    """Record what this process image was built from. Call once, at daemon start.

    After a re-exec this runs again in the fresh process, so the fingerprint
    always describes *the code currently executing*, not the code on disk.
    """
    global _IMAGE_FINGERPRINT
    _IMAGE_FINGERPRINT = _image_snapshot()


def image_is_stale() -> bool:
    """Has the checkout's code moved out from under this running process?

    Safe to call from worker threads — unlike :meth:`DevReloadWatcher.changed`,
    this only reads (it never advances a snapshot), so concurrent callers cannot
    race each other into swallowing an edit.

    ``False`` when no fingerprint was captured: an ad-hoc ``brr run`` is a fresh
    interpreter by construction and cannot be stale.
    """
    if _IMAGE_FINGERPRINT is None:
        return False
    return _image_snapshot() != _IMAGE_FINGERPRINT


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

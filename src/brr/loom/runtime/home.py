"""Paths, the install id, and id minting for one loom home."""

from __future__ import annotations

import os
import re
import secrets
from pathlib import Path

_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
_THREAD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")


def mint(n: int) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))


def check_install(value: str) -> str:
    if (not isinstance(value, str) or len(value) != 4 or
            any(c not in "0123456789abcdef" for c in value)):
        raise ValueError(f"install id {value!r} is not 4 hex chars")
    return value


def is_channel(to: object) -> bool:
    return isinstance(to, str) and to.startswith("channel:")


_CHANNEL = re.compile(r"channel:(telegram|whatsapp|slack)/([A-Za-z0-9_:#.-]+)")


def channel_parts(channel: str) -> tuple[str, str | None]:
    """Parse a supported channel address, raising on an unknown adapter or chat."""
    if channel == "channel:fake":
        return "fake", None
    match = _CHANNEL.fullmatch(channel) if isinstance(channel, str) else None
    if match is None:
        raise ValueError(f"no channel adapter for {channel!r}")
    return match.group(1), match.group(2)


class Home:
    def __init__(self, root: Path | str, install: str | None = None):
        self.root = Path(root)
        self._install = check_install(install) if install is not None else None

    def install_id(self) -> str:
        if self._install is not None:
            return self._install
        path = self.root / "loom" / "install-id"
        if path.is_file():
            value = path.read_text().strip()
            return check_install(value)
        path.parent.mkdir(parents=True, exist_ok=True)
        value = secrets.token_hex(2)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            return path.read_text().strip()
        try:
            os.write(fd, (value + "\n").encode())
        finally:
            os.close(fd)
        return value

    def facts_dir(self) -> Path:
        path = self.root / "ledger" / "facts" / self.install_id()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def thread_dir(self, thread: str) -> Path:
        check_thread_id(thread)
        return self.root / "self" / "threads" / thread

    def thread_exists(self, thread: str) -> bool:
        return (self.thread_dir(thread) / "README.md").is_file()

    def on_main(self, thread: str) -> bool:
        """Step 1 is the folder. A git self also requires the README on ``main``.

        No ``.git`` means the step-1 home: the file on disk is the whole check.
        """
        check_thread_id(thread)
        self_dir = self.root / "self"
        if not (self_dir / ".git").exists():
            return True
        from .selfrepo import git
        proc = git(
            self_dir, "cat-file", "-e", f"main:threads/{thread}/README.md",
            check=False,
        )
        return proc.returncode == 0

    def room(self, strand: str) -> Path:
        if not strand or strand in {".", ".."} or "/" in strand or "\\" in strand:
            raise ValueError(f"unsafe strand id {strand!r}")
        return self.root / "rooms" / strand


def check_thread_id(thread: str) -> str:
    if not isinstance(thread, str) or not _THREAD_ID.match(thread):
        raise ValueError(f"unsafe thread id {thread!r}")
    return thread


def thread_of(to: str) -> str:
    if not isinstance(to, str) or not to.startswith("thread:"):
        raise ValueError(f"step 1 routes only thread:<id>, got {to!r}")
    return check_thread_id(to.split(":", 1)[1])


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o644)
    try:
        os.write(fd, text.encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def home_of_room(room: Path) -> Home:
    room = Path(room)
    if room.parent.name != "rooms":
        raise ValueError(f"room {room} is not <home>/rooms/<strand>")
    return Home(room.parent.parent)


def strand_of_room(room: Path) -> str:
    return Path(room).name

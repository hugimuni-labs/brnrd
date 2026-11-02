"""Paths, the install id, and id minting for one loom home."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import threading
from pathlib import Path

_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
_THREAD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")


def resolve_home(flag: str | Path | None = None) -> tuple[Path, str]:
    """Resolve an explicit home, the environment, or an existing default home.

    Explicit paths may be new: ``init`` must be able to create a home.
    Only the implicit default needs evidence that it belongs to the loom.
    """
    if flag is not None:
        return Path(flag).expanduser().resolve(), "flag"
    env = os.environ.get("BRNRD_HOME")
    if env:
        return Path(env).expanduser().resolve(), "env"
    default = Path.home() / "brnrd"
    if is_home(default):
        return default.resolve(), "default"
    raise SystemExit(
        "no loom home: --home was not given; BRNRD_HOME is unset or empty; "
        f"default {default} is not a loom home. Pass --home PATH, set BRNRD_HOME, "
        "or initialise ~/brnrd with --home."
    )


_MARKERS = ("loom/config.toml", "loom/install-id", "self/core/loom/wake")


def is_home(path: Path) -> bool:
    """A folder the loom has already written into, not merely one named like a home."""
    return any((Path(path) / marker).is_file() for marker in _MARKERS)


def found_homes() -> list[Path]:
    """Every existing loom home in the places brnrd puts one, without a flag.

    ``~/brnrd``, and any folder one level inside an account under brnrd's
    state root (``accounts/<id>/<name>``), whatever it is called.
    """
    state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state").expanduser()
    accounts = state / "brnrd" / "accounts"
    candidates = [Path.home() / "brnrd"]
    if accounts.is_dir():
        candidates += sorted(p for account in accounts.iterdir() if account.is_dir()
                             for p in account.iterdir() if p.is_dir())
    found: list[Path] = []
    for path in candidates:
        if is_home(path) and path.resolve() not in found:
            found.append(path.resolve())
    return found


def mint(n: int) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))


def notice_id(kind: str, subject: str) -> str:
    return f"loom/{kind}-{hashlib.sha256(subject.encode()).hexdigest()[:10]}"


def check_install(value: str) -> str:
    if (not isinstance(value, str) or len(value) != 4 or
            any(c not in "0123456789abcdef" for c in value)):
        raise ValueError(f"install id {value!r} is not 4 hex chars")
    return value


def is_channel(to: object) -> bool:
    return isinstance(to, str) and to.startswith("channel:")


_CHANNEL = re.compile(r"channel:([A-Za-z0-9][A-Za-z0-9._-]*)(?:/([A-Za-z0-9_:@#.+/-]+))?")


def channel_parts(channel: str, home=None) -> tuple[str, str | None]:
    """Parse an address; with a home, require an enabled carrier as well."""
    match = _CHANNEL.fullmatch(channel) if isinstance(channel, str) else None
    if match is None:
        raise ValueError(f"no channel adapter for {channel!r}")
    parts = match.groups()
    if parts[1] is not None and any(segment in {"", ".", ".."} for segment in parts[1].split("/")):
        raise ValueError(f"invalid channel chat {parts[1]!r}")
    if home is not None:
        from .connector import instances
        names = instances(home)
        if parts[0] not in names:
            raise ValueError(f'not sent: no channel "{parts[0]}"; enabled: {", ".join(names) or "(none)"}')
    return parts


class Home:
    def __init__(self, root: Path | str, install: str | None = None):
        self.root = Path(root)
        self._install = check_install(install) if install is not None else None

    def install_id(self) -> str:
        if self._install is not None:
            return self._install
        path = self.root / "loom" / "install-id"
        if path.is_file():
            # Written once, never rewritten: read it once per Home.
            self._install = check_install(path.read_text().strip())
            return self._install
        path.parent.mkdir(parents=True, exist_ok=True)
        value = secrets.token_hex(2)
        # Written whole beside it, then linked into place: the link is as
        # exclusive as O_EXCL, and a reader never sees the file half-written.
        tmp = path.with_name(f".install-id.{os.getpid()}.{secrets.token_hex(4)}")
        fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        try:
            os.write(fd, (value + "\n").encode())
        finally:
            os.close(fd)
        try:
            os.link(tmp, path)
        except FileExistsError:
            value = path.read_text().strip()
        finally:
            tmp.unlink(missing_ok=True)
        self._install = check_install(value)
        return self._install

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

    def main_thread(self) -> str:
        """The home thread, where letters with no better address land."""
        return HOME_THREAD

    def routable(self, thread: object) -> bool:
        """A thread a body can be started on: a valid id with its README on main."""
        try:
            return (self.on_main(thread) if (self.root / "self/.git").exists()
                    else self.thread_exists(thread))
        except ValueError:
            return False

    def room(self, strand: str) -> Path:
        if not strand or strand in {".", ".."} or "/" in strand or "\\" in strand:
            raise ValueError(f"unsafe strand id {strand!r}")
        return self.root / "rooms" / strand


HOME_THREAD = "inbox"


def check_thread_id(thread: str) -> str:
    if not isinstance(thread, str) or not _THREAD_ID.match(thread):
        raise ValueError(f"unsafe thread id {thread!r}")
    return thread


def thread_of(to: str) -> str:
    if not isinstance(to, str) or not to.startswith("thread:"):
        raise ValueError(f"an address is thread:<id> or channel:<platform>/<chat>, copied whole "
                         f"from the letter's reply address; got {to!r}")
    return check_thread_id(to.split(":", 1)[1])


def atomic_write(path: Path, text: str | bytes, *, sync_directory: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # One temp name per writer: two processes writing the same path must not
    # rename each other's temp file away (two jacks stopping at once did).
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o644)
        with os.fdopen(fd, "wb") as stream:
            stream.write(text.encode() if isinstance(text, str) else text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    if sync_directory:
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def home_of_room(room: Path) -> Home:
    room = Path(room)
    if room.parent.name != "rooms":
        raise ValueError(f"room {room} is not <home>/rooms/<strand>")
    return Home(room.parent.parent)


def strand_of_room(room: Path) -> str:
    return Path(room).name

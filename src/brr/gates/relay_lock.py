"""One consumer per relay cursor: the daemon's cloud gate, or the loom.

Two pollers on one relay inbox would each take the other's events, so
exactly one of them may long-poll at a time. The lock lives beside the
cloud state (``<state_dir>/gates/relay.lock``) because the cursor and the
token belong to the account, not to either consumer. The daemon cannot know
where a loom home is; both can find the account's gate directory.

* The holder keeps an ``flock`` on the file. A crashed holder frees it.
* The file's content is ``{"pid", "kind", "cursor"}``: who holds it (for the
  refusal message) and the last cursor the holder committed. That cursor is
  the handoff: whoever takes the lock next starts from it, so a cutover in
  either direction neither replays the other's events nor skips any.
* The loom preempts. It writes ``relay.want`` with its pid. A daemon that
  sees a live ``want`` from another process doesn't take the lock, and the
  daemon releases the lock after every poll, so the loom gets it within one
  long-poll. Stopping the loom removes ``want``, and the daemon resumes
  polling from the loom's last cursor. Rollback is stopping the loom.
"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path


def lock_path(state_dir: Path | str) -> Path:
    return Path(state_dir) / "gates" / "relay.lock"


def want_path(state_dir: Path | str) -> Path:
    return Path(state_dir) / "gates" / "relay.want"


def _alive(pid: object) -> bool:
    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class RelayLock:
    def __init__(self, state_dir: Path | str, kind: str):
        self.state_dir = Path(state_dir)
        self.path = lock_path(state_dir)
        self.kind = kind
        self.fd: int | None = None

    @property
    def held(self) -> bool:
        return self.fd is not None

    def read(self) -> dict:
        """The last holder's record. A torn or missing file reads as ``{}``."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def cursor(self) -> int | None:
        value = self.read().get("cursor")
        return value if type(value) is int and value >= 0 else None

    def wanted_by_other(self) -> dict | None:
        """A live ``want`` from another process ⇒ its record, else ``None``."""
        try:
            data = json.loads(want_path(self.state_dir).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        pid = data.get("pid")
        if pid == os.getpid() or not _alive(pid):
            return None
        return data

    def want(self) -> None:
        path = want_path(self.state_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".want.tmp")
        tmp.write_text(json.dumps({"pid": os.getpid(), "kind": self.kind}) + "\n")
        os.replace(tmp, path)

    def unwant(self) -> None:
        path = want_path(self.state_dir)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data, dict) and data.get("pid") == os.getpid():
            path.unlink(missing_ok=True)

    def try_acquire(self) -> bool:
        if self.fd is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        self.fd = fd
        self.record(self.cursor())
        return True

    def record(self, cursor: int | None) -> None:
        """Rewrite the record in place: replacing the file would drop the flock."""
        if self.fd is None:
            raise RuntimeError("relay lock: record without holding the lock")
        data: dict = {"pid": os.getpid(), "kind": self.kind}
        if cursor is not None:
            data["cursor"] = cursor
        raw = (json.dumps(data) + "\n").encode()
        os.ftruncate(self.fd, 0)
        os.pwrite(self.fd, raw, 0)
        os.fsync(self.fd)

    def release(self) -> None:
        if self.fd is None:
            return
        fd, self.fd = self.fd, None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def holder(self) -> str:
        data = self.read()
        return f"{data.get('kind', '?')} pid {data.get('pid', '?')}"

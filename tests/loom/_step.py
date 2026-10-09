"""Loom-home setup shared by the step-1 suite."""

from __future__ import annotations

import threading
import time
import traceback
from pathlib import Path

from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import read_facts
from brr.loom.runtime.loom import _ingest, run


def write_thread(root: Path, thread: str, readme: str, policy: str,
                 wait: str = "20") -> None:
    directory = Home(root).thread_dir(thread)
    directory.mkdir(parents=True, exist_ok=True)
    text = readme if readme.endswith("\n") else readme + "\n"
    (directory / "README.md").write_text(text)
    (directory / "policy").write_text(policy.strip() + "\n")
    (directory / "wait").write_text(str(wait).strip() + "\n")


class Loom:
    """A loom loop on a background thread. ``halt`` stops it and drains port/out."""

    def __init__(self, root: Path, tick: float = 0.05):
        self.root = Path(root)
        self.home = Home(self.root)
        self.stop = threading.Event()
        self.errors: list[str] = []
        self.thread = threading.Thread(target=self._run, name="loom-step-1")
        self.tick = tick

    def _run(self) -> None:
        try:
            run(self.root, adapter="fake", tick=self.tick, stop=self.stop)
        except Exception:
            self.errors.append(traceback.format_exc())

    def start(self) -> None:
        self.thread.start()

    def facts(self):
        return read_facts(self.home)

    def halt(self) -> None:
        self.stop.set()
        self.thread.join(timeout=5)
        self.drain()

    def drain(self) -> None:
        for _ in range(50):
            _ingest(self.home)
            if not self.out_files():
                return
            time.sleep(0.02)

    def out_files(self) -> list[Path]:
        rooms = self.root / "rooms"
        if not rooms.is_dir():
            return []
        found = []
        for room in rooms.iterdir():
            out = room / "port" / "out"
            if out.is_dir():
                found.extend(path for path in out.iterdir() if not path.name.startswith("."))
        return found

    def dump(self) -> str:
        lines = ["errors:"]
        lines.extend(self.errors or ["(none)"])
        try:
            facts = self.facts()
        except Exception as exc:
            lines.append(f"facts unreadable: {exc}")
            facts = []
        lines.append("facts:")
        for fact in facts:
            lines.append(f"  {fact.kind} {fact.id} {fact.data}")
        rooms = self.root / "rooms"
        if rooms.is_dir():
            for room in sorted(rooms.iterdir()):
                lines.append(f"room {room.name}")
                for name in ("wake.md", "trace.jsonl", "body.log", "wake-seen"):
                    path = room / "port" / name
                    if path.is_file():
                        text = path.read_text(errors="replace")
                        if name == "body.log" and len(text) > 2000:
                            text = text[-2000:]
                        lines.append(f"--- {name}\n{text}")
                out = room / "port" / "out"
                if out.is_dir():
                    lines.append("out: " + ", ".join(path.name for path in out.iterdir()))
        log = self.root / "loom" / "loom.log"
        if log.is_file():
            lines.append("--- loom.log\n" + log.read_text(errors="replace")[-2000:])
        return "\n".join(lines)


def wait_until(predicate, timeout: float, dump) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    text = dump() if callable(dump) else dump
    raise AssertionError(text)

"""A live profile of the loop without root: ``loom/sample.request`` asks for one.

The file holds a number of seconds (default 60, at most 600). The loop's
next pass removes it and starts a daemon thread that reads the main thread's
frame through ``sys._current_frames()`` every ``INTERVAL`` seconds. When the
time is up it writes ``loom/samples/<stamp>.collapsed``: one ``a;b;c count``
line per distinct stack, root first, as flamegraph.pl and speedscope read
it. Nothing runs until a request lands, and one sample runs at a time.
"""

from __future__ import annotations

import sys
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

INTERVAL = 0.01
DEFAULT = 60.0
LIMIT = 600.0


def _stack(frame) -> str:
    names = []
    while frame is not None:
        code = frame.f_code
        names.append(f"{code.co_name} ({Path(code.co_filename).name}:{code.co_firstlineno})")
        frame = frame.f_back
    return ";".join(reversed(names))


def collect(ident: int, seconds: float, interval: float = INTERVAL) -> tuple[Counter, int]:
    stacks, taken = Counter(), 0
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        frame = sys._current_frames().get(ident)
        if frame is not None:
            stacks[_stack(frame)] += 1
            taken += 1
        del frame
        time.sleep(interval)
    return stacks, taken


def write(path: Path, stacks: Counter) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(f"{stack} {count}\n" for stack, count in stacks.most_common()))
    tmp.replace(path)


class Sampler:
    def __init__(self, home, log):
        self.home, self.log = home, log
        self.worker: threading.Thread | None = None

    def step(self) -> None:
        request = self.home.root / "loom" / "sample.request"
        if not request.is_file() or (self.worker is not None and self.worker.is_alive()):
            return
        text = request.read_text().strip()
        request.unlink()
        try:
            seconds = min(max(float(text or DEFAULT), 1.0), LIMIT)
        except ValueError:
            self.log(self.home, f"sample: {text!r} is not a number of seconds; nothing sampled")
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = self.home.root / "loom" / "samples" / f"{stamp}.collapsed"
        ident = threading.main_thread().ident
        self.log(self.home, f"sample: main thread for {seconds:g}s -> {path}")

        def run():
            stacks, taken = collect(ident, seconds)
            write(path, stacks)
            self.log(self.home, f"sample: wrote {taken} samples, {len(stacks)} stacks to {path}")

        self.worker = threading.Thread(target=run, name="loom-sampler", daemon=True)
        self.worker.start()

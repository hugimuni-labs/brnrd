"""A live profile of the loop without root: ``loom/sample.request`` asks for one.

The file holds a number of seconds (default 60, at most 600). The loop's
next pass removes it and starts a daemon thread that reads every other
thread's frame through ``sys._current_frames()`` every ``INTERVAL`` seconds.
When the time is up it writes ``loom/samples/<stamp>.collapsed``: one
``thread;a;b;c count`` line per distinct stack, the thread's name first and
then root first, as flamegraph.pl and speedscope read it. `ps -M` shows a
thread's CPU but not its Python name; the first frame gives it one. Nothing runs until a request lands, and one sample runs at a time.

``loom/heap.request`` (any content) asks for the heap instead: the next pass
writes ``loom/samples/<stamp>.heap``, the gc-tracked objects counted and sized
by type, then the largest containers with the dict key that holds each. Two
reads an hour apart name what grows (2026-10-30: 1.4 GB in 11 h, swapped out,
and each full gc paged it back in under the GIL).
"""

from __future__ import annotations

import gc
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


def collect(seconds: float, interval: float = INTERVAL) -> tuple[Counter, int]:
    stacks, taken, me = Counter(), 0, threading.get_ident()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        names = {t.ident: t.name for t in threading.enumerate()}
        frames = sys._current_frames()
        for ident, frame in frames.items():
            if ident != me:
                stacks[f"{names.get(ident, ident)};{_stack(frame)}"] += 1
        taken += 1
        del frames, frame
        time.sleep(interval)
    return stacks, taken


def write(path: Path, stacks: Counter) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(f"{stack} {count}\n" for stack, count in stacks.most_common()))
    tmp.replace(path)


def heap(top: int = 40, largest: int = 20) -> str:
    """The tracked heap by type, then its largest containers and who holds them."""
    objects = gc.get_objects()
    counts, sizes = Counter(), Counter()
    for obj in objects:
        name = type(obj).__qualname__
        counts[name] += 1
        sizes[name] += sys.getsizeof(obj)
    lines = [f"tracked {len(objects)} objects, {sum(sizes.values())} bytes shallow"]
    lines += [f"type {name} {count} {sizes[name]}" for name, count in counts.most_common(top)]
    big = sorted((obj for obj in objects if isinstance(obj, (list, dict, set, tuple))),
                 key=len, reverse=True)[:largest]
    del objects
    for obj in big:
        held = [f"{type(ref).__qualname__}[{key!r}]" for ref in gc.get_referrers(obj)
                if isinstance(ref, dict) for key, value in ref.items() if value is obj]
        first = type(next(iter(obj))).__qualname__ if obj else "-"
        lines.append(f"largest {type(obj).__qualname__} len {len(obj)} of {first} held by {' '.join(held[:3]) or '?'}")
    return "".join(f"{line}\n" for line in lines)


class Sampler:
    def __init__(self, home, log):
        self.home, self.log = home, log
        self.worker: threading.Thread | None = None

    def step(self) -> None:
        self._heap()
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
        self.log(self.home, f"sample: every thread for {seconds:g}s -> {path}")

        def run():
            stacks, taken = collect(seconds)
            write(path, stacks)
            self.log(self.home, f"sample: wrote {taken} samples, {len(stacks)} stacks to {path}")

        self.worker = threading.Thread(target=run, name="loom-sampler", daemon=True)
        self.worker.start()

    def _heap(self) -> None:
        request = self.home.root / "loom" / "heap.request"
        if not request.is_file():
            return
        request.unlink()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = self.home.root / "loom" / "samples" / f"{stamp}.heap"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(heap())
        tmp.replace(path)
        self.log(self.home, f"sample: heap -> {path}")

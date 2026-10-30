"""The loom watches its own CPU: sustained past a share, the loop's guard degrades ``cpu``.

On 2026-10-29 the loop ran at 83-92% for hours and nothing said so; a person
noticed the fans. :class:`CpuWatch` reads the process's user and system time
(every thread, no children) and, once a full window has passed, raises
:class:`CpuHigh` at each tick while the window's share is past the limit. The
guard turns that into ``loom.degraded`` for ``cpu`` and, past its notice delay,
a notice to inbox, as it does for the disk floor; back under clears it.
"""

from __future__ import annotations

import resource
import time
from collections import deque

WINDOW = 600.0
LIMIT = 0.30
#: Seconds between samples: a window holds ~60 of them, not one per tick.
EVERY = 10.0


class CpuHigh(Exception):
    """The window's share past the limit; ``grade`` counts the limits it is past beyond the first."""

    def __init__(self, share: float, window: float, limit: float):
        super().__init__(f"the loom used {share:.0%} of a core over the last {window / 60:g} min, "
                         f"past {limit:.0%}")
        self.share, self.grade = share, max(0, int(share / limit) - 1)


def used() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_utime + usage.ru_stime


class CpuWatch:
    def __init__(self, clock=time.monotonic, usage=used, window: float = WINDOW,
                 limit: float = LIMIT, every: float = EVERY):
        self.clock, self.usage = clock, usage
        self.window, self.limit, self.every = window, limit, every
        self.samples: deque[tuple[float, float]] = deque()
        self.high: CpuHigh | None = None

    def step(self) -> None:
        now = self.clock()
        if not self.samples or now - self.samples[-1][0] >= self.every:
            self.samples.append((now, self.usage()))
            # Keep the newest sample a full window back, so a share always spans the window.
            while len(self.samples) > 2 and now - self.samples[1][0] >= self.window:
                self.samples.popleft()
            start, begun = self.samples[0]
            span = now - start
            share = (self.samples[-1][1] - begun) / span if span >= self.window else 0.0
            self.high = CpuHigh(share, self.window, self.limit) if share > self.limit else None
        if self.high is not None:
            raise self.high

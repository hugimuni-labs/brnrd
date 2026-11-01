"""The memory floor: under memory pressure the loom starts no new body.

On 2026-10-31 an 8 GB iMac hung with 8.7 GB of swap and was hard-rebooted;
every new body is another few hundred MB on a machine already paging. On
darwin, :func:`reading` asks the kernel for ``kern.memorystatus_vm_pressure_level``
(1 normal, 2 warn, 4 critical) and ``vm.swapusage`` through ``sysctlbyname``,
no fork. The floor holds at critical, and at warn while swap is more than
``SWAP_FULL`` used. :func:`pressed` is what a start reads: a wanted body records
``body.pending`` and waits, a parked one stays parked, a live one is untouched,
and an operator's ``--force`` still starts (bodies.py). :class:`MemoryFloor`'s
step raises :class:`MemoryPressure` for the loop's guard, which writes
``loom.degraded`` for ``memory`` and, past its delay, one notice; clear pressure
recovers it. ``memory_floor = "off"`` turns it off; elsewhere it reads nothing.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import sys
import time
from pathlib import Path

NORMAL, WARN, CRITICAL = 1, 2, 4
SWAP_FULL = 0.75
#: Seconds between kernel reads; a start inside that reads the last one.
EVERY = 5.0


class MemoryPressure(Exception):
    """The kernel's pressure past the floor; ``grade`` is 1 at critical."""

    def __init__(self, level: int, swap: tuple[int, int] | None):
        name = {WARN: "warn", CRITICAL: "critical"}.get(level, str(level))
        held = (f"; swap {swap[1] >> 20} of {swap[0] >> 20} MB used" if swap and swap[0] else "")
        super().__init__(f"memory pressure {name}{held}: no new body starts until it clears")
        self.level, self.swap, self.grade = level, swap, int(level >= CRITICAL)


class _XswUsage(ctypes.Structure):
    _fields_ = [("total", ctypes.c_uint64), ("avail", ctypes.c_uint64), ("used", ctypes.c_uint64),
                ("pagesize", ctypes.c_uint32), ("encrypted", ctypes.c_int)]


_LIBC = None


def _sysctl(name: str, out) -> bool:
    global _LIBC
    if _LIBC is None:
        _LIBC = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        _LIBC.sysctlbyname.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
                                       ctypes.c_void_p, ctypes.c_size_t]
    size = ctypes.c_size_t(ctypes.sizeof(out))
    return _LIBC.sysctlbyname(name.encode(), ctypes.byref(out), ctypes.byref(size), None, 0) == 0


def reading() -> tuple[int, tuple[int, int] | None] | None:
    """``(level, (swap total, swap used))`` in bytes; None off darwin or when the kernel says nothing."""
    if sys.platform != "darwin":
        return None
    try:
        level, swap = ctypes.c_int(), _XswUsage()
        if not _sysctl("kern.memorystatus_vm_pressure_level", level):
            return None
        return level.value, ((swap.total, swap.used) if _sysctl("vm.swapusage", swap) else None)
    except (OSError, AttributeError, TypeError):
        return None


def judge(read) -> MemoryPressure | None:
    """The floor's verdict on one reading: critical holds, warn holds with swap past ``SWAP_FULL``."""
    if read is None:
        return None
    level, swap = read
    full = bool(swap and swap[0] and swap[1] > SWAP_FULL * swap[0])
    return MemoryPressure(level, swap) if level >= CRITICAL or level >= WARN and full else None


_LAST: dict[str, tuple[float, MemoryPressure | None]] = {}


def pressed(root: Path, config=None) -> MemoryPressure | None:
    """The floor's standing verdict for the home at ``root``, read at most every ``EVERY`` seconds."""
    if config is None:
        from .config import load_config
        config = load_config(root)
    if config.memory_floor == "off":
        return None
    key, now = str(root), time.monotonic()
    last = _LAST.get(key)
    if last is None or EVERY <= 0 or now - last[0] >= EVERY:
        last = _LAST[key] = (now, judge(reading()))
    return last[1]


class MemoryFloor:
    """The loop's ``memory`` step: raises while the floor holds, so the guard degrades and recovers."""

    def __init__(self, home):
        self.home = home

    def step(self) -> None:
        found = pressed(self.home.root)
        if found is not None:
            raise found

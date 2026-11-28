"""Share observations during one loom tick (or one standalone snapshot)."""

import json
import os
from contextlib import contextmanager
from contextvars import ContextVar

_INPUTS = ContextVar("loom_tick_inputs", default=None)
_WATCH = ContextVar("loom_input_watch", default=None)


@contextmanager
def tick_inputs():
    if _INPUTS.get() is not None:
        yield
        return
    token = _INPUTS.set({})
    try:
        yield
    finally:
        _INPUTS.reset(token)


def once(kind, key, read):
    cache = _INPUTS.get()
    if cache is None:
        return read()
    slot = (kind, key)
    if slot not in cache:
        cache[slot] = read()
    return cache[slot]


@contextmanager
def watch_inputs():
    watched = {}
    token = _WATCH.set(watched)
    try:
        yield watched
    finally:
        _WATCH.reset(token)


def stat(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size, st.st_ino


def input_stat(path):
    """A cache hit still registers the path with each render that used it."""
    path = os.fsdecode(path)
    stamp = once("stat", path, lambda: stat(path))
    watch_stamp(path, stamp)
    return stamp


def watch_stamp(path, stamp):
    watched = _WATCH.get()
    if watched is not None:
        watched[os.fsdecode(path)] = stamp


def read_json(path):
    # Capture the stamp before reading. A rewrite during render must retire
    # that render on the next tick, even if its open saw the old inode.
    input_stat(path)

    def read():
        try:
            return True, json.loads(path.read_text())
        except (OSError, ValueError):
            return False, None

    return once("json", path, read)

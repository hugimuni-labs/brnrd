"""Terminal styling for brnrd's own output — small, dependency-free, off by
default anywhere the output isn't a live human terminal.

The bar this exists to clear: ``gh auth login``'s interview reads as a built
*thing* — a colored ``?`` before a bold question, the answer you gave (or
brnrd resolved for you) in its own color, a green checkmark that means
*done*. brnrd's own ``init`` told the same story in plain ASCII. This module
gives every print call that wants it the same handful of moves, using
nothing beyond ANSI SGR codes every terminal brr runs in already supports —
no new dependency for a CLI whose whole pitch is a thin, fast install.

Off by construction wherever color would be a lie: piped output, a ``dumb``
``TERM``, ``NO_COLOR`` set (https://no-color.org) — and, the case that
matters for this repo's own test suite, pytest's ``capsys``, which replaces
stdout with a stream whose ``isatty()`` is ``False``. Every helper here is a
no-op producing the identical plain string when color is off, so the many
``assert "... exact text ..." in out`` checks across ``tests/test_adopt.py``
never had to change: color wraps a whole phrase, it never splits one.
``BRR_FORCE_COLOR=1`` overrides the tty check for driving this by hand
outside a real terminal (a CI log viewer that renders ANSI, a recorded
demo) — the same escape hatch ``FORCE_COLOR`` conventionally offers.
"""

from __future__ import annotations

import os
import sys
from typing import TextIO

_RESET = "\x1b[0m"
_BOLD = "\x1b[1m"
_DIM = "\x1b[2m"
_RED = "\x1b[31m"
_GREEN = "\x1b[32m"
_YELLOW = "\x1b[33m"
_CYAN = "\x1b[36m"


def enabled(stream: TextIO | None = None) -> bool:
    """Whether *stream* (stdout by default) should carry style codes.

    Checked fresh on every call rather than cached at import — the decision
    depends on the live stream and environment, both of which a test may
    swap out mid-run (monkeypatched env, a redirected stdout).
    """
    if os.environ.get("BRR_FORCE_COLOR"):
        return True
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    stream = stream if stream is not None else sys.stdout
    try:
        return bool(stream.isatty())
    except Exception:  # noqa: BLE001 — a stream that can't answer isatty() gets no color
        return False


def _wrap(code: str, text: str) -> str:
    return f"{code}{text}{_RESET}" if enabled() else text


def bold(text: str) -> str:
    """Headings, questions, and anything that should read as a label."""
    return _wrap(_BOLD, text)


def dim(text: str) -> str:
    """Chrome: hints, timeouts, optional-and-skippable, the ``[brnrd]`` tag."""
    return _wrap(_DIM, text)


def accent(text: str) -> str:
    """What you typed, or what brnrd resolved for you — gh's cyan answers."""
    return _wrap(_CYAN, text)


def good(text: str) -> str:
    return _wrap(_GREEN, text)


def bad(text: str) -> str:
    return _wrap(_RED, text)


def caution(text: str) -> str:
    return _wrap(_YELLOW, text)


# Semantic glyphs, colored — the vocabulary brnrd's ``_verify`` step already
# spoke in plain ASCII (✓ / ✗ / ⚠ / ·), given the same visual weight gh gives
# its own checkmark. Functions, not module-level constants: the color has to
# reflect the stream at *print* time, not at import time.
def check() -> str:
    return good("✓")


def cross() -> str:
    return bad("✗")


def warn_glyph() -> str:
    return caution("⚠")


def dot() -> str:
    return dim("·")


def qmark() -> str:
    """The colored ``?`` gh (and every survey-style prompt) opens a question
    with — the one glyph in this module that is always green, matching the
    reference screenshot rather than the semantic-glyph palette above."""
    return good("?")

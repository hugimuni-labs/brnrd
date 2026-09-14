"""The typed seam between the drain and a verb handler.

A handler takes one :class:`OutboxFile` and returns one :class:`Handled`.
Both are frozen: the frontmatter dict inside an ``OutboxFile`` is the drain's
own parse and a handler may still mutate it (``cut:`` pops ``event``/``gate``
before falling through), but a handler that wants the *rest of the table* to
see a different file says so by returning ``then=`` — the drain never reads a
handler's locals.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

Outcome = Literal["accepted", "refused", "deferred"]


@dataclass(frozen=True)
class DrainContext:
    """What every handler in one drain shares — ``_drain_outbox``'s arguments
    plus the inbox union resolved once per drain."""

    emit: Any
    responses_dir: Path
    event_id: str
    outbox_dir: Path | None
    inbox_dir: Path | None
    repo_root: Path | None
    account_context: Any
    stats: dict[str, int] | None
    address_sources: Any


@dataclass(frozen=True)
class OutboxFile:
    """One staged outbox file, parsed: the input to exactly one handler."""

    path: Path
    frontmatter: dict
    body: str
    run: Any
    ctx: DrainContext

    def rewritten(self, frontmatter: dict, body: str) -> "OutboxFile":
        """The same file as the rest of the table should see it."""
        return replace(self, frontmatter=frontmatter, body=body)


@dataclass(frozen=True)
class Produce:
    """A coordinate the frame verified and attests (design-the-loom §17)."""

    kind: str
    ref: str
    at: str


@dataclass(frozen=True)
class Handled:
    """What one handler did with one file.

    ``outcome`` — ``accepted``: the handler promoted something (a reply, a
    queued spawn, an armed wait); ``refused``: it promoted nothing and a
    ``refused``/``dropped`` notice names this file; ``deferred``: it consumed
    the file, promoted nothing and refused nothing (an empty reply body, a
    directive whose effect is not a promotion).
    ``notice`` — the text of the last counted notice this file produced.
    ``promoted`` — the handler's contribution to the drain's return value.
    ``then`` — set only by a handler that falls through (``cut:``): the file
    the rows *after* this one should see.
    """

    verb: str
    outcome: Outcome
    notice: str | None = None
    produce: tuple[Produce, ...] = ()
    promoted: int = 0
    then: OutboxFile | None = field(default=None, repr=False)

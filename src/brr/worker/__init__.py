"""The throw — what runs one pass of the daemon for one event.

The manual's word is *the throw*; the code keeps ``worker``. It used to be one
function, ``daemon._run_worker`` — 2,578 lines, 268 branches, one section
banner — that already had five phases and named none of them:

``prepare``  → :class:`Prepared` | :class:`Finalized`
    the event, the run record, the runner selection, trust, the environment,
    the branch plan, presence, the wake's orientation, the first Lane.
``dispatch`` → :class:`Dispatched` | :class:`Boundary`
    one attempt up to the runner's start: the prompt, the announcement.
``stream``   → :class:`Streamed`
    the runner's life, boundary by boundary, and what it handed back.
``boundary`` → :class:`Boundary`
    what the turn ended as: completed · hold · stopped · retry · fallback ·
    exhausted.
``finalize`` → :class:`Finalized`
    the ending, written.

A ``retry`` or ``fallback`` boundary carries the next :class:`Attempt` back to
``dispatch`` — the old ``while True`` is the loop in :func:`run`, and nowhere
else.

**Patchability.** ``daemon._run_worker`` keeps its name and signature and is
now a call into :func:`run`; everything that calls or patches it is
unchanged. The phases reach every helper still defined in ``daemon`` as
``daemon.<name>``, looked up at call time exactly as the old global lookups
were, so a test that patches ``daemon._capture_dominion`` (or any other
daemon-level name) patches what the phase calls. Each ``daemon.<name>`` is
also a seam the later moves can lift out.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .boundary import boundary
from .dispatch import dispatch
from .finalize import finalize
from .prepare import prepare
from .shapes import Attempt, Boundary, Dispatched, Finalized, Lane, Prepared, Streamed
from .stream import stream

if TYPE_CHECKING:
    from ..account import AccountContext
    from ..run import Run

__all__ = [
    "Attempt",
    "Boundary",
    "Dispatched",
    "Finalized",
    "Lane",
    "Prepared",
    "Streamed",
    "boundary",
    "dispatch",
    "finalize",
    "prepare",
    "run",
    "stream",
]


def run(
    event: dict,
    repo_root: Path,
    responses_dir: Path,
    cfg: dict,
    max_retries: int,
    *,
    account_context: AccountContext | None = None,
    inbox_dir: Path | None = None,
) -> Run:
    """One throw: prepare, then dispatch → stream → boundary until the turn
    ends as something other than a retry, then finalize."""
    prepared = prepare(
        event, repo_root, responses_dir, cfg, max_retries,
        account_context=account_context, inbox_dir=inbox_dir,
    )
    if isinstance(prepared, Finalized):
        return prepared.task
    attempt = Attempt(n=1, lane=prepared.lane)
    while True:
        dispatched = dispatch(prepared, attempt)
        reached = (
            dispatched if isinstance(dispatched, Boundary)
            else boundary(prepared, stream(prepared, dispatched))
        )
        if reached.next_attempt is not None:
            attempt = reached.next_attempt
            continue
        return finalize(prepared, reached).task

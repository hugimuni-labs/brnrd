"""One notice writer, and the file it is about.

Every refusal, drop, redirect and advisory the daemon records for a running
resident lands here: one JSON row per line in the run's own outbox
(``.notices.jsonl``), surfaced as ``portal-state.json → notices``. The kinds
and lifetimes are documented beside their aliases in ``daemon.py``.

**The correlation gap, closed at the writer.** A notice used to record its
verb and target only inside ``text``, so ``brnrd do`` matched "the directive
I just staged" by substring. A row written while the drain is handling a
staged file now carries that file's name (``source_file``), the table row
that claimed it (``verb``) and the run (``run``) — for every notice written
during that handling, including the ones deep inside a ``_queue_*`` helper
that never saw the path. The drain opens :func:`attributed` around each
handler; an explicit ``source_file=`` / ``verb=`` still wins. A notice written
outside any drain (the environmental ``standing`` writers, the main loop's
spawn admission) carries neither: it is about no staged file.

``text`` is unchanged, so the chip's ``!N`` and the ``✗ spawn refused``
segment — both read ``text`` and ``kind`` — keep working.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

NOTICES_FILE = ".notices.jsonl"

KINDS = frozenset({"refused", "dropped", "advisory", "redirected"})
LIFETIMES = frozenset({"run", "standing"})

#: Kinds that say "this directive was not carried out" — what makes a
#: handler's outcome ``refused``.
COUNTED_AGAINST = frozenset({"refused", "dropped"})


@dataclass
class Attribution:
    source_file: str
    verb: str
    run: str
    written: list[tuple[str, str]] = field(default_factory=list)


_current: contextvars.ContextVar[Attribution | None] = contextvars.ContextVar(
    "brr_outbox_notice_attribution", default=None,
)


@contextlib.contextmanager
def attributed(*, source_file: str, verb: str, run: str) -> Iterator[Attribution]:
    """Attribute every notice written inside the block to one staged file."""
    record = Attribution(source_file=source_file, verb=verb, run=run)
    token = _current.set(record)
    try:
        yield record
    finally:
        _current.reset(token)


def write(
    kind: str,
    text: str,
    *,
    outbox_dir: Path | None,
    lifetime: str,
    source_file: str = "",
    verb: str = "",
    run: str = "",
) -> None:
    """Record one notice for the running resident.

    *kind* and *lifetime* are required, never defaulted: every call site
    decides which it is (``daemon.py`` documents both vocabularies).
    """
    if kind not in KINDS:
        raise ValueError(f"_record_outbox_notice: invalid kind {kind!r}")
    if lifetime not in LIFETIMES:
        raise ValueError(f"_record_outbox_notice: invalid lifetime {lifetime!r}")
    current = _current.get()
    if current is not None:
        source_file = source_file or current.source_file
        verb = verb or current.verb
        run = run or current.run
        current.written.append((kind, text))
    print(f"[brnrd] outbox: {text}")
    if outbox_dir is None:
        return
    try:
        outbox_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "text": text,
            "kind": kind,
            "lifetime": lifetime,
        }
        if source_file:
            record["source_file"] = source_file
        if verb:
            record["verb"] = verb
        if run:
            record["run"] = run
        line = json.dumps(record, ensure_ascii=False)
        with (outbox_dir / NOTICES_FILE).open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass

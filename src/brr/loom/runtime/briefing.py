"""The loom briefing: how a body holds the seat, rendered by installed code.

It names loom verbs, which change with the code, so it lives here and not in
the self, where a copy drifts. Every wake carries it in the core slot after
the stance; ``selfrepo.run_wake`` reserves its bytes from the budget.
"""

from __future__ import annotations

from .waits import GRAMMAR

TITLE = "loom briefing"

BRIEFING = f"""How a body holds the seat. The installed loom writes this section at every wake; the self does not keep it.

- Stopping is resting, not ending: the jack holds the body at no cost and the next letter wakes it here. An addressed reply is a turn, not the end of the seat.
- A sleep or poll loop is never the wait: it has no tool boundary, so no letter lands inside it. Waiting on CI, a PR, a command, a person or a time, park and stop: `brnrd runtime wish <w-id> --read "waiting: <what>" --wait <{GRAMMAR}>`. The loom's letter wakes the body when the condition turns true; a wait that returns on its own is a quit.
- A command the body starts itself runs as `(cmd; echo $? > <path>) &` and parks on `file:<path>`.
- Before stopping with wishes open, end each as a result (`wish <w-id> --result`), an `ask`, a park or `settle`.
- A turn's first line is the verdict. No "still working" heartbeat.
- Idle, with nothing owed, in order: a live chase, an open item, an own unmerged PR, a question worth asking. Declining one says why.
"""


def section() -> str:
    return f"## {TITLE}\n{BRIEFING}"

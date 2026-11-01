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
- Scratch goes under `$TMPDIR`, the room's `tmp/`, never `/private/tmp` or `/tmp`: the reaper clears the room, nothing clears those. A checkout with work to land sits at the room's root, where the reaper reads it before anything goes; `tmp/` is reaped unread.
- Before stopping with wishes open, end each as a result (`wish <w-id> --result`), an `ask`, a park or `settle`.
- This thread's README is your continuity: a shed or a crash wipes whatever you did not write down, and the body that picks up after a careless handover pays for it or loses it. Tend it when the work moves, not before a shed. Shedding is yours to call, and a wait alone is not a reason: a warm context is cheap. Shed (`brnrd runtime shed --why "<one line>"`, then end the turn) when what you carry is mostly residue (diagnostic reads, superseded drafts, back-and-forth) beside the live work, what matters is written down, and no reasoning lives only in this context.
- A turn's first line is the verdict. No "still working" heartbeat.
- A project lives in a thread whose seat declares its repo: `send --to thread:<new> --wish <w-id> --repo <url or lib name> "<work>"`. Its body commits on `loom/<thread>` and pushes with `publish <name>`, then records `publish <name> --pr N`. A repo the person already has can be named once in the library, so later threads cite it by name: `lib add <name> <url> --kind known --whose <person> --for "<why>"`. When the wish needs a repo that does not exist yet, making it is part of the work: `gh repo create <owner>/<name> --private`, say so in your reply, then open the thread on it.
- Idle, with nothing owed, in order: a live chase, an open item, an own unmerged PR, a question worth asking. Declining one says why.
"""


def section() -> str:
    return f"## {TITLE}\n{BRIEFING}"

"""The seat's scroll, held by the daemon — unspeakable from an event.

A native resume is one process reopening one transcript: ``claude --resume
<id>``. It is a **one-shot claim on one thing**, and until brnrd#2023 it was
carried as ordinary event frontmatter (``resume_native_session_id``), written
onto whichever events a release could reach.

Four separate defects came out of that one choice, each found after the
previous fix shipped (brnrd#2012, #2016, #2022):

1. one release stamped *every* accumulated event in a hold's drawer — N
   claims on one transcript, from one release;
2. a stamp outlived its release on disk, so a message retried a day later
   still resumed a session that had long since ended
   (``evt-1789625355181034000-2jb8``: a 2026-09-17 correspondent message
   carrying a session id minted 2026-09-18);
3. a *minter* that copied a waking event's frontmatter (the ``respawn:``
   outbox verb) handed the successor the very scroll the handover existed to
   end, hours before any releaser had a say;
4. and the whole surface was open by construction — ``Run.from_event``
   copies every unreserved frontmatter key onto run meta, so the key was
   **spellable from an event**, and every event-minting path in the daemon
   was a fresh candidate for the same bug.

Patching writers is a losing game against (4): the enumeration is a grep, and
it had already failed twice. So the claim moves off the event entirely.

**The shape.** One claim per seat, owned by the daemon, living beside the
seat's runs. A release *arms* it; the dispatch that actually leads *consumes*
it, by rename, exactly once. An event can no longer say anything about a
transcript — ``Run._EVENT_META_FIELDS`` drops the two keys before they can
reach run meta, so a hand-forged inbox file is inert.

What each defect becomes:

* N claims from one release is unrepresentable — there is one file.
* A claim cannot outlive its release: consumption unlinks it, and an
  unconsumed one expires (:data:`MAX_AGE_SECONDS`).
* A minter has nothing to copy.
* A forged event has nothing to say.

**What still can't be proven here.** This closes the paths *through an
event*. A transcript is also reachable by anything that writes ``run.md``
meta directly, and by the Shell's own session store — see the report's §5.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

#: One file per seat, beside the seat's own runs. One seat per repo, so one
#: claim: a second ``arm`` replaces the first rather than queueing beside it,
#: which is the invariant the old spray could not express.
FILENAME = "pending-resume.json"

#: A claim nobody consumed is a claim whose seat never came back. Expire it
#: rather than letting it wait: the failure this whole module exists to end
#: is a stale claim firing long after the process it names has gone.
MAX_AGE_SECONDS = 60.0 * 60.0 * 24.0


def path_for(seat_home: Path) -> Path:
    """The claim file for one seat.

    *seat_home* is ``daemon._shuttle_home(...)`` — the account home when
    there is one, else the repo's ``.brr``. Deliberately **not** a repo's
    runs dir: one seat spans the repos an account serves, and a message
    about repo B legitimately resumes a seat parked on repo A
    (``test_message_about_another_repo_is_mail_to_the_same_shuttle``). Keyed
    per-repo, that resume would have armed a claim in one place and looked
    for it in another — a silent cold boot, which is precisely the failure
    mode this module exists to make impossible to produce quietly.

    It also sits beside the shuttle rather than among the runs: ``runs/``
    holds runs, and a surface that lists it should not have to learn to
    filter.
    """
    return Path(seat_home) / FILENAME


def arm(
    seat_home: Path,
    *,
    session_id: str,
    provider: str,
    conversation_key: str,
    from_run: str = "",
    why: str = "",
) -> dict[str, Any] | None:
    """Record that the *next* dispatch on this seat may reopen *session_id*.

    *conversation_key* is the seat's own thread, and consumption requires it
    to match. That is what keeps a strand out: a child dispatches under
    ``run:<parent>``, never the seat's key, so it cannot pick up the parent's
    scroll even though it shares the repo's ``.brr``.

    Returns the armed record, or ``None`` when there is nothing to arm (no
    session id — an honest cold boot, which needs no record).
    """
    session_id = str(session_id or "").strip()
    if not session_id:
        return None
    record = {
        "session_id": session_id,
        "provider": str(provider or ""),
        "conversation_key": str(conversation_key or ""),
        "from_run": str(from_run or ""),
        "why": str(why or ""),
        "armed_at": time.time(),
    }
    target = path_for(seat_home)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    return record


def peek(seat_home: Path) -> dict[str, Any] | None:
    """The armed claim without consuming it — for surfaces and tests."""
    try:
        return json.loads(path_for(seat_home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def clear(seat_home: Path, *, why: str = "") -> bool:
    """Drop any armed claim. Returns whether one was there.

    The release that *ends* a seat (a dashboard release, a handover) calls
    this: not merely declining to arm, but unmaking whatever is armed. The
    stamp this replaces had to be stripped for the same reason — it was a
    cache, and it healed (brnrd#2022, ``evt-…-udee``: two lines deleted by
    hand at 15:10Z were back at 15:14Z).
    """
    target = path_for(seat_home)
    if not target.exists():
        return False
    try:
        target.unlink()
    except OSError:
        return False
    if why:
        print(f"[brnrd] pending resume cleared: {why}")
    return True


def consume(seat_home: Path, *, conversation_key: str) -> dict[str, Any] | None:
    """Claim the seat's scroll for this dispatch, exactly once.

    The rename is the claim. Two dispatches racing on one seat cannot both
    win: ``os.replace`` onto a private name succeeds for one and raises
    ``FileNotFoundError`` for the other — which is the whole reason this is
    not "read the file, then delete it".

    ``None`` — an honest cold boot — when: nothing is armed; the claim was
    armed for a different thread (a strand's ``run:<parent>``, a seat that
    has since moved); or the claim is older than :data:`MAX_AGE_SECONDS`.
    Every refusal says so on stdout, because a resume that silently does not
    happen is indistinguishable from one that was never armed, and that
    ambiguity is how three of the four defects above stayed invisible.
    """
    target = path_for(seat_home)
    if not target.exists():
        return None
    claimed = target.with_suffix(f".json.claimed-{os.getpid()}")
    try:
        os.replace(target, claimed)
    except OSError:
        return None
    try:
        record = json.loads(claimed.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        record = None
    finally:
        try:
            claimed.unlink()
        except OSError:
            pass
    if not isinstance(record, dict):
        return None

    armed_for = str(record.get("conversation_key") or "")
    arriving = str(conversation_key or "")
    if armed_for and armed_for != arriving:
        # Re-arm it: this dispatch is not the seat coming back, and taking
        # the claim away from the one that is would turn a mis-route into a
        # silent cold boot for the run that was owed it.
        print(
            f"[brnrd] pending resume not claimed: armed for {armed_for!r}, "
            f"this dispatch is {arriving!r} — left for the seat"
        )
        arm(
            seat_home,
            session_id=str(record.get("session_id") or ""),
            provider=str(record.get("provider") or ""),
            conversation_key=armed_for,
            from_run=str(record.get("from_run") or ""),
            why=str(record.get("why") or ""),
        )
        return None

    age = time.time() - float(record.get("armed_at") or 0.0)
    if age > MAX_AGE_SECONDS:
        print(
            f"[brnrd] pending resume expired after {age / 3600:.1f}h "
            f"(from {record.get('from_run') or '?'}) — cold boot"
        )
        return None
    return record

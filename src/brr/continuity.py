"""Continuity — the world's readout of what this resident last did.

Slice 3.  :mod:`brr.bootscore` stays a pure IR + renderer; the filesystem, git
and forge-cache reads that *populate* :class:`~brr.bootscore.BootContinuity`
live here.

The distinction this module exists to enforce: **observed, not authored.**

A wake already perceives a great deal about its own past — ``Recent Activity``
and the authored work surface. Every line is prose the resident/user wrote.
That is a message in a bottle: exactly as good as
last wake's discipline, and free to drift from the world in total silence.
Authored memory never brings bad news about itself.

So nothing here reads the resident's prose.  It reads the run directory, the
dominion's git, and the local forge cache — sources that answer *what happened*
rather than *what was claimed* — and when the two disagree, that disagreement is
the single most valuable line in the boot (:attr:`BootContinuity.drift`).

Deterministic and network-free, like every other facet of the boot score.  Every
read is defensive: continuity is an *orientation aid*, and a wake must never
fail to boot because its own memory was hard to read.  A failed read degrades to
``✗ unreachable``, which is itself the honest signal.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import dominion as dominion_mod
from .bootscore import BootContinuity


def _run_git(repo: Path, *args: str) -> str | None:
    """``git -C repo …`` → stdout, or ``None`` on any failure.

    Bounded and quiet.  A slow or broken git must degrade the continuity line,
    never stall the wake behind it.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _format_age(seconds: float) -> str:
    """``14m ago`` / ``3h ago`` / ``2d ago`` — coarse, enough to judge by."""
    if seconds < 90:
        return "just now"
    minutes = seconds / 60
    if minutes < 90:
        return f"{int(minutes)}m ago"
    hours = minutes / 60
    if hours < 36:
        return f"{int(hours)}h ago"
    return f"{int(hours / 24)}d ago"


#: The event ``source`` a concurrently-dispatched ``spawn:`` worker records.
#:
#: Written in exactly one place — ``daemon._queue_spawn_request``, as
#: ``str(fm.get("source") or "spawn")`` — so a run whose score reports it was
#: dispatched down the worker stack, not woken as this resident's next thought.
_SPAWN_SOURCE = "spawn"


def _dispatched_as_worker(score_path: Path) -> bool:
    """Was the run that wrote this score a concurrent ``spawn:`` worker?

    **The fact is already in the file.**  ``BootAttention.source_gate`` is
    persisted as ``str(event["source"])``, and the spawn dispatcher stamps
    ``"spawn"`` there — the same value the home run node's ``state.md`` records
    as ``source: spawn``.  So the picker needs no new field and no schema bump:
    it reads a fact every score since Slice 1 has already carried, which also
    makes the fix *retroactive* over the run directories already on disk.

    **``spawn:`` and ``respawn:`` are distinguishable here, and the distinction
    is deliberate.**  ``daemon._queue_respawn_request`` derives its child's
    source as ``fm.get("source") or current.get("source") or task.source or
    "respawn"`` — it *inherits the originating gate* (``telegram``,
    ``schedule``, ``cli``).  A resident's respawn handoff therefore never lands
    on ``"spawn"`` and still counts as the predecessor, which is right: a
    respawn is the same thought continuing in a different body.  A *worker*
    that respawns does inherit ``"spawn"`` and stays skipped — also right, it
    is still not this resident's line.

    Anything this cannot prove is a worker degrades to "not a worker": an
    unreadable, unparseable or field-less score keeps today's behaviour rather
    than being silently dropped from the candidate set.  A score written before
    this change has no ``source_gate`` only if its wake had no event source at
    all; either way the absent case reads as ordinary, never as *skip it*.

    A parse failure deliberately does **not** skip: ``build_continuity`` owes
    that run a ``✗ unreachable`` mount, and a picker that swallowed it would
    hand back a healthy ``✓`` for an older run while the broken memory went
    unmentioned.
    """
    try:
        payload = json.loads(score_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    attention = payload.get("attention")
    if not isinstance(attention, dict):
        return False
    return str(attention.get("source_gate") or "") == _SPAWN_SOURCE


def find_prior_wake(
    runs_dir: Path, *, current_run_id: str | None = None
) -> tuple[str, Path, float] | None:
    """Newest prior run **this resident's line thought** → ``(run_id, path, mtime)``.

    **The mount is already on disk.**  Since Slice 1 every wake writes its own
    ``boot-score.json`` into its run directory, which means a resident's last
    wake left behind a machine-readable record of exactly who it was.  Reading
    it is not a reconstruction — it is the previous self, verbatim.

    *Whose* previous self is the part this originally got wrong (#987).  Newest-
    first with no notion of parentage meant a ``spawn:`` worker the resident had
    dispatched — a concurrent limb with a different task, often a different
    Shell and Core, on a thread the parent never joined — outsorted the parent
    and became the next wake's "predecessor".  It fired against itself on
    2026-08-02: ``run-260802-0730-cc2f`` booted with
    ``continuity: ✓ run-260802-0649-lcrd`` (a codex worker) while its own
    ``## Your last run`` block, which reads the home node and knows about
    parentage, named ``run-260802-0632-v2ir``.  Two blocks in one prompt, two
    answers to *where was I*, and the anchor whose whole job is closing the loop
    across wakes was the one pointing at a child.

    A run directory without a score (crashed before assembly, or pre-Slice-1) is
    skipped rather than treated as the prior wake: a mount must not report a
    predecessor it cannot actually read.
    """
    try:
        candidates = sorted(
            (d for d in runs_dir.iterdir() if d.is_dir() and d.name.startswith("run-")),
            key=lambda d: d.name,
            reverse=True,
        )
    except OSError:
        return None
    for d in candidates:
        if current_run_id and d.name == current_run_id:
            continue
        score = d / "boot-score.json"
        try:
            mtime = score.stat().st_mtime
        except OSError:
            continue
        if _dispatched_as_worker(score):
            continue
        return (d.name, score, mtime)
    return None


def _dominion_commits_since(repo: Path, since: float) -> int:
    """Commits the dominion actually took since the last wake.

    Memory the resident *committed*, not memory it said it would.  ``0`` after a
    wake that did real work is itself worth seeing.
    """
    out = _run_git(
        repo, "log", "--oneline", f"--since=@{int(since)}", "--no-merges"
    )
    if out is None:
        return 0
    return sum(1 for line in out.splitlines() if line.strip())


def _merged_since(prs: Iterable[Any], since: float) -> tuple[str, ...]:
    """PRs that reached MERGED since the last wake — ``("#386", "#387")``.

    The forge block already knew this and buried it in reference position as a
    *list of PRs*.  The fact was there; the loop was not closed, because nothing
    said **you did this**.  Position and framing are the whole delta.
    """
    from . import forge_pr_cache

    out: list[tuple[float, str]] = []
    for pr in prs or ():
        if not isinstance(pr, dict):
            continue
        if str(pr.get("state") or "").upper() != "MERGED":
            continue
        number = pr.get("number")
        if number is None:
            continue
        merged = forge_pr_cache.parse_iso(pr.get("merged_at"))
        if merged is None or merged < since:
            continue
        out.append((merged, f"#{number}"))
    return tuple(name for _, name in sorted(out))


def _drift(brr_dir: Path | None, dominion_repo: Path | None) -> tuple[str, ...]:
    """Where the resident's account of itself and the world's have come apart.

    Deliberately few, and each one *actionable*.  A drift line that a wake can
    only nod at is noise in the hottest slot of the prompt; these two are things
    a resident must handle before it trusts anything else it was handed.
    """
    found: list[str] = []

    if brr_dir is not None:
        try:
            reason = dominion_mod.needs_sync(brr_dir)
        except Exception:
            reason = None
        if reason:
            found.append(
                f"dominion push was rejected ({reason.strip()}) — the remote "
                "diverged; reconcile before trusting injected memory"
            )

    if dominion_repo is not None:
        # Expand untracked directories to leaf paths: ``runs/`` contains both
        # daemon-owned ``state.md`` and resident-owned ``body.md`` files, so a
        # collapsed ``?? runs/`` entry cannot be classified truthfully.
        out = _run_git(dominion_repo, "status", "--porcelain", "--untracked-files=all")
        if out:
            n = sum(1 for line in out.splitlines() if _is_resident_memory(line))
            if n:
                found.append(
                    f"dominion has {n} uncommitted change(s) — the capture net "
                    "did not close on a prior wake"
                )

    return tuple(found)


#: Daemon-owned bookkeeping inside the dominion repo.  Not resident memory, and
#: never evidence of a dropped capture.
#:
#: ``state.md`` is written at run start and committed at run *end* — always
#: untracked mid-wake.  ``messages/*`` are delivery records the pipeline
#: mutates *after* the capture commit (``status: pending → delivered``,
#: ``platform_message_id``, ``delivered_at`` land once the platform acks), so
#: a delivered reply left the prior run's message files modified on every
#: wake — the same permanent-lie shape ``state.md`` was exempted for, one
#: seam later in the run's life.
_DAEMON_OWNED_RUN_STATE = re.compile(
    r"^runs/[^/]+/[^/]+/(?:state\.md|messages/[^/]+)$"
)

#: Whole roots of the account home repo that hold daemon machinery, never
#: resident memory: gate live-state (``account/gates/*.json`` mutates on the
#: heartbeat, *after* every capture commit), the dispatch queues, and config
#: journals.  Exempted **by root**, deliberately: the daemon-owned family is
#: the open class here — a new bookkeeping file inside these roots joins the
#: exempt set with no edit — while resident memory (``repos/*/dominion``,
#: ``surface/``, a legacy dominion's root-level pages) keeps firing the drift
#: line.  Enumerating individual daemon *files* was tried and is exactly how
#: ``account/gates/cloud.health.json`` became a permanent every-wake false
#: alarm the third time this lesson was paid for (#942; the two prior
#: instalments are documented on :func:`_is_resident_memory`).
_DAEMON_OWNED_ROOTS = ("account/", "dispatch/", "config-changes/")
_DAEMON_OWNED_FILES = frozenset({"security.config", "knowledge.capture.lock"})


def _is_resident_memory(porcelain_line: str) -> bool:
    """Is this ``git status --porcelain`` line resident memory that went uncommitted?

    Caught on the first live render of this very function, which is the only
    reason it is not shipping as a permanent lie.  The dominion always has one
    untracked file mid-wake — ``runs/<repo>/<run>/state.md``, written by the daemon
    at run start and committed by the capture net at run *end*.  Counting it
    meant ``drift: the capture net did not close`` would fire on **every wake,
    forever**, describing a capture net that was in fact working perfectly and
    had simply not run yet.

    Which is worse than useless.  A drift line that cries wolf every wake gets
    skimmed by the third wake — and then gets skimmed the one time it is real.
    The value of this whole facet is that it is *rare and true*; a false positive
    in the hottest slot of the boot doesn't just waste bytes, it trains the
    resident to stop reading the line that was supposed to save it.

    Paid a second time for ``messages/*`` (delivery bookkeeping mutated after
    the capture commit), and a third for ``account/gates/*.json`` (heartbeat
    gate state, #942) — which is when per-file enumeration stopped being the
    model: daemon machinery is now exempted by whole root
    (``_DAEMON_OWNED_ROOTS``), so the next bookkeeping file the daemon grows
    inside them never rejoins this list.
    """
    line = porcelain_line.strip()
    if not line:
        return False
    # Porcelain: two status chars, a space, then the path.
    path = porcelain_line[3:].strip() if len(porcelain_line) > 3 else ""
    if not path:
        return False
    # A rename line names two paths; the destination is where memory would be.
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    if path.startswith(_DAEMON_OWNED_ROOTS) or path in _DAEMON_OWNED_FILES:
        return False
    return _DAEMON_OWNED_RUN_STATE.fullmatch(path) is None


def build_continuity(
    brr_dir: Path | None = None,
    *,
    current_run_id: str | None = None,
    dominion_repo: Path | None = None,
    prs: Sequence[Any] | None = None,
    now: float | None = None,
) -> BootContinuity:
    """Assemble the continuity facet.  Never raises; degrades to a stated ``✗``.

    ``✗ first wake`` and ``✗ unreachable`` are *different facts* and the boot
    says which.  The first is ordinary and true.  The second means the memory
    that should be here is not — and a resident that reads that line knows to
    distrust everything downstream of it, which is precisely the bad news an
    authored ``continuity: ✓ 391 entries`` could never have delivered.
    """
    now = time.time() if now is None else now

    if brr_dir is None:
        return BootContinuity(mount="✗ unreachable")

    # Drift is computed on *every* path, including the failed-mount ones.
    #
    # It was originally computed only after a ✓ mount, which meant a wake that
    # could not find its predecessor also silently skipped the check for
    # uncommitted memory and a rejected dominion push — the two moments a
    # resident most needs the warning.  The bug surfaced because a test written
    # for something else was passing *vacuously*: it asserted "no drift" and got
    # it from an early return, not from the filter it meant to exercise.  Drift
    # is a fact about the dominion's health, and the dominion's health does not
    # depend on whether this checkout has a run history.
    drift = _drift(brr_dir, dominion_repo)

    runs_dir = brr_dir / "runs"
    if not runs_dir.is_dir():
        # No run history at all: a genuinely first wake in this checkout.
        return BootContinuity(mount="✗ first wake", drift=drift)

    prior = find_prior_wake(runs_dir, current_run_id=current_run_id)
    if prior is None:
        return BootContinuity(mount="✗ first wake", drift=drift)

    run_id, score_path, mtime = prior

    # Read the prior score.  We do not (yet) diff its fields — the run id and
    # the fact that it *parses* are what the mount is made of.  A score that
    # will not parse is a broken mount, and saying so is the honest move.
    try:
        json.loads(score_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return BootContinuity(mount="✗ unreachable", last_run=run_id, drift=drift)

    return BootContinuity(
        last_run=run_id,
        last_age=_format_age(max(0.0, now - mtime)),
        mount="✓",
        shipped=_merged_since(prs or (), mtime),
        dominion_commits=(
            _dominion_commits_since(dominion_repo, mtime)
            if dominion_repo is not None
            else 0
        ),
        drift=drift,
    )

"""The blueprint: what this run said it would make.

``.relics.jsonl`` is the manifest of what a run **made** — written as things
land, read by the ledger row, the dashboard payload, the chat card and the
run node. This module is its opposite tense: ``.promises.jsonl``, one line
per claim, written when the claim is made.

#1008, and the maintainer's 2026-08-03 widening of it:

    we could keep a log of promised items and whether you fulfilled them,
    injected in the stop hook… you assemble the relics, you should just
    also have a blueprint, or shipping list, which you compare at the
    closeout

    the boundary injection gives you a plan vs progress diff summary

The failure it catches is a run that names three PRs mid-thread and ships
two, where the only record of the claim was prose in a chat nothing reads
back. Four flags exist today for the same species of fact — ``awaiting_reply``
(#1034), the vigil claim (#947), the closeout latch (#982), ``.card`` §Now —
each hand-carved at a different seam, because there was nowhere to put the
general one. This is the general one. None of the four is retired here: they
go once this has fired live, not in the same change that introduces it.

Append format — one JSON object per line, at least a ``what``:

    {"what": "pr", "count": 2, "ref": "the rollout split"}
    {"what": "kb", "ref": "subject-x.md"}
    {"what": "pr", "count": 1, "released": true, "why": "superseded by #1042"}

**Why a second file rather than a ``kind: "promise"`` row in the relics
manifest.** The first draft of this was the one-file version, and it was
rejected on a count: ``.relics.jsonl`` has six readers today
(``counts_by_kind``, the ledger row, the dashboard payload, the chat-card
tail, the Stop manifest, ``_LIVE_KINDS``) and every one of them means
*produce*. Putting the future tense in that file makes "structured clearly"
a filter obligation distributed across six readers, correct only while every
future edit remembers it; here the structure is the filename. The maintainer
named the real cost of a second file — *a new file nobody remembers is a
guard that never fires* — and the answer is that this one is remembered
every boundary, because it has a chip (:func:`chip`). That is the rule the
exchange settled on: **a control file earns its existence by being one chip
on the boundary bar.**

The loss, stated so nobody has to rediscover it: one ordered log would read
as a *narrative* — promise and fulfilment interleaved in time. Two files
lose that, and it comes back as a rendering (merge on read, sort by
timestamp) if it is ever wanted, never as storage.

Everything here is best-effort in the same posture as ``relics``: a
malformed line is skipped, a missing file is an empty blueprint, and no
read path can raise into a closeout.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONTROL_NAME = ".promises.jsonl"

#: What a promise may name. Deliberately the produce vocabulary and nothing
#: else: a promise that names something the manifest can never attest would
#: sit owed forever, which is the *fires constantly for a non-reason* death
#: with extra steps. Kept as its own tuple rather than imported from
#: ``relics._LIVE_KINDS`` so this module has no import edge into the heavier
#: one — the two are checked against each other by a test, which is the
#: honest form of "keep these in sync".
PROMISABLE = (
    "commit", "branch", "pr", "merge", "kb", "issue", "comment", "message",
    "file",
)

#: Singular/plural per promisable kind, in render order. Mirrors
#: ``relics._TAIL_NOUNS`` for the kinds both name.
_NOUNS: tuple[tuple[str, str, str], ...] = (
    ("commit", "commit", "commits"),
    ("merge", "merge", "merges"),
    ("pr", "PR", "PRs"),
    ("issue", "issue", "issues"),
    ("kb", "page", "pages"),
    ("file", "file", "files"),
    ("comment", "comment", "comments"),
    ("message", "message", "messages"),
    ("branch", "branch", "branches"),
)

# Same caps and posture as ``relics``: a run appending more than this is
# looping, not planning.
_MAX_RECORDS = 200
_MAX_LINE_BYTES = 2048


def _normalise_what(raw: Any) -> str | None:
    what = str(raw or "").strip().lower()
    if what == "kb_page":
        what = "kb"
    return what if what in PROMISABLE else None


def read(outbox_dir: Path | None) -> list[dict[str, Any]]:
    """Parse ``.promises.jsonl``. Missing file → ``[]``.

    Tolerant of blank and malformed lines, capped at :data:`_MAX_RECORDS`,
    and it never raises — a broken blueprint must degrade to *fewer
    promises*, never to a failed boundary. A row whose ``what`` is not
    promisable is dropped here rather than at render time, so exactly one
    place decides what a promise may name.
    """
    if outbox_dir is None:
        return []
    try:
        text = (Path(outbox_dir) / CONTROL_NAME).read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or len(line.encode("utf-8")) > _MAX_LINE_BYTES:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        what = _normalise_what(record.get("what"))
        if what is None:
            continue
        record["what"] = what
        out.append(record)
        if len(out) >= _MAX_RECORDS:
            break
    return out


def append(
    outbox_dir: Path | None,
    what: str,
    *,
    count: int = 1,
    ref: str | None = None,
    released: bool = False,
    why: str | None = None,
) -> None:
    """Append one blueprint row. Best-effort, like ``relics.append``."""
    if outbox_dir is None:
        return
    record: dict[str, Any] = {"what": what, "count": int(count)}
    if ref:
        record["ref"] = ref
    if released:
        record["released"] = True
    if why:
        record["why"] = why
    line = json.dumps(record, separators=(",", ":"), sort_keys=False)
    try:
        with (Path(outbox_dir) / CONTROL_NAME).open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


@dataclass(frozen=True)
class Blueprint:
    """Plan against progress for one run.

    ``owed`` is the only field a guard may assert on, and the asymmetry is
    the whole design:

    - ``promised 3 · shipped 1`` is **evidence a promise broke** — the run
      wrote the row itself, so it can be proven wrong about it;
    - ``promised 0 · shipped 3`` is **not evidence of a kept run**. It is a
      run that wrote no rows, and that is byte-identical to a run that had
      nothing to promise.

    So an empty blueprint renders as *nothing*, never as a pass. A manifest
    is a self-report, and a guard may only assert something the run can be
    proven wrong about.
    """

    promised: dict[str, int]
    shipped: dict[str, int]
    owed: dict[str, int]
    labels: dict[str, list[str]] = field(default_factory=dict)

    @property
    def any_promises(self) -> bool:
        return bool(self.promised)

    @property
    def kept(self) -> bool:
        """Every promise met. Meaningless unless :attr:`any_promises`."""
        return bool(self.promised) and not self.owed


def blueprint(
    rows: list[dict[str, Any]],
    shipped: dict[str, int] | None,
) -> Blueprint:
    """Join the blueprint rows against what actually landed.

    *shipped* is the run's live produce counts — the daemon already computes
    exactly this for the ``produce`` facet (auto-derived commits, branch and
    PR joined with the self-reported relics), so nothing here re-derives it.

    **Count decides; ``ref`` speaks.** Matching is on ``what`` and count and
    never on ``ref``: a promise is made before the thing exists and cannot
    name it, so keying on ``ref`` would report a broken promise every time a
    run shipped the right work under a different name — a guard that cries
    wolf on correct work is worse than no guard. ``ref`` is the *label*,
    which is what makes the rendered line say *which* two PRs are outstanding
    instead of making the reader subtract and then guess (his 2026-08-03
    steer: *"the diff doesn't have to be the literal diff either"*).

    A ``released`` row subtracts. Withdrawing a promise has to be possible
    and has to be deliberate: without it the line is a soft nag with no
    counter, and a nag with no counter fires forever and stops being read.
    The reason rides the row rather than a log, so the record of the
    abandonment lives where the abandonment happened.
    """
    promised: dict[str, int] = {}
    labels: dict[str, list[str]] = {}
    for record in rows:
        what = str(record.get("what") or "")
        if what not in PROMISABLE:
            continue
        raw = record.get("count", 1)
        try:
            count = int(raw)
        except (TypeError, ValueError):
            count = 1
        if count <= 0:
            continue
        if record.get("released"):
            promised[what] = promised.get(what, 0) - count
            continue
        promised[what] = promised.get(what, 0) + count
        ref = str(record.get("ref") or "").strip()
        if ref:
            labels.setdefault(what, []).append(ref)
    promised = {what: n for what, n in promised.items() if n > 0}
    shipped_counts = dict(shipped or {})
    landed = {what: int(shipped_counts.get(what, 0) or 0) for what in promised}
    owed = {
        what: promised[what] - landed[what]
        for what in promised
        if promised[what] > landed[what]
    }
    return Blueprint(
        promised=promised,
        shipped=landed,
        owed=owed,
        labels={what: refs for what, refs in labels.items() if what in promised},
    )


def _noun(what: str, count: int) -> str:
    for kind, singular, plural in _NOUNS:
        if kind == what:
            return singular if count == 1 else plural
    return what


def _ordered(counts: dict[str, int]) -> list[tuple[str, int]]:
    order = {kind: i for i, (kind, _, _) in enumerate(_NOUNS)}
    return sorted(counts.items(), key=lambda item: (order.get(item[0], 99), item[0]))


def chip(plan: Blueprint) -> str | None:
    """``"owed 2"`` — the boundary chip, absent at zero.

    A differential chip, like ``!N``: it earns its ink only while something
    is outstanding. Deliberately **not** a ratio against the produce count —
    ``⚒5`` counts things nobody promised, so ``2/5`` would be an aggregate
    over a population with no shared denominator, and a chip that looks like
    a progress bar will be read as one.
    """
    total = sum(plan.owed.values())
    return f"owed {total}" if total > 0 else None


def owed_line(plan: Blueprint) -> str | None:
    """``"- still owed: 2 PRs — the rollout · the notices split"``, or None.

    The rendered form of the diff, not the arithmetic of it.
    ``promised 3 · shipped 1 · missing 2`` makes the reader subtract and
    then reconstruct *which*; this names the outstanding thing, using the
    ``ref`` labels when the run supplied them.
    """
    if not plan.owed:
        return None
    parts: list[str] = []
    for what, count in _ordered(plan.owed):
        piece = f"{count} {_noun(what, count)}"
        refs = plan.labels.get(what) or []
        if refs:
            # Labels are exact only while *everything* promised of this kind
            # is still owed. Once some of it has landed, matching is on count
            # — which cannot say *which* — so the labels become candidates
            # and the line has to say so. Naming the confident branch here
            # would be a diagnostic asserting something the run cannot be
            # proven wrong about, and it would name the wrong PR roughly half
            # the time it mattered.
            partial = count < plan.promised.get(what, count)
            piece += " — " + ("one of: " if partial and len(refs) > 1 else "")
            piece += " · ".join(refs[:4])
            if len(refs) > 4:
                piece += f" · +{len(refs) - 4} more"
        parts.append(piece)
    return (
        "- still owed: " + " · ".join(parts)
        + " — promised by this run and not yet in its manifest. Ship it, or "
        "release it with a reason (`brnrd promise <what> --release --why …`)."
    )


def token(plan: Blueprint) -> str:
    """A stable token for the blueprint's *state*, for delta latching.

    The boundary channel carries obligations and an ambient status bar in
    one string, and content dedupe cannot tell them apart: an ambient line
    repeats because nothing *happened*, an obligation because nothing was
    *done*, and byte-identical is the signature of both (#818/#963). So the
    owed line is latched on **its own** delta — it speaks when a promise is
    made, when one is met or released, and at the closeout, rather than once
    per boundary for as long as it stands. The always-visible half of the
    same fact is the chip, which is gateless by construction and costs seven
    characters.

    This belongs inside #818's per-component split when that lands; keying
    it here is the same seam at a smaller scale, and it must not wait.
    """
    return json.dumps(
        {"p": _ordered(plan.promised), "o": _ordered(plan.owed)},
        separators=(",", ":"),
    )

"""The ignition assignments (w-69) — the boot as typed obligations.

The boot used to be *blocks to read*; the run's obligations arrived as prose
scattered across them (a ``next:`` list, a portal seed, an orientation meter,
standing nags). This module makes the boot *assignments to discharge*: one
typed list, daemon-derived at wake assembly, each row carrying what discharges
it and when it starts escalating. It is the drift engine's philosophy applied
to t=0 — the steady state is act-keyed (nothing renders unless something
moved), and the boot, where everything is new at once, gets the complementary
rule: everything arrives as an assignment, and the assignment's lifecycle is
act-keyed from its first render.

Design contract: ``design-the-ignition-assignments.md`` (all four forks signed
2026-08-19, plus the rider that the waking event itself is assignment #1).

Three owners share this vocabulary, which is why it lives in its own module
(the :mod:`brr.course` pattern):

- :class:`brr.bootscore.BootScore` carries the derived rows and
  ``format_kernel`` renders them — the list a wake reads first;
- ``brr.prompts._build_assignments`` derives them at wake assembly via
  :func:`derive`, priced from the live quota posture via :func:`price`;
- :mod:`brr.hooks` runs the boundary ledger (:func:`advance`, :func:`chip`,
  :func:`detail_lines`, :func:`stop_lines`) against the rows persisted in
  ``boot-score.json``.

A row is in one of three states, shared with the drift asks and documented
once through ``brnrd legend``: *current* (quiet), *moved* (renders once), or
*overdue* (grows — one line of detail per ``cadence`` boundaries past its
window, up to :data:`ESCALATION_CAP` lines, then holds). Discharge or
defer-with-reason retires a row at any stage; nothing is retired by time.

The card handoff (§3 of the design): the daemon *offers* the rows as the
initial ``## Plan`` scaffold. The resident's first card write that carries a
``## Plan``/``## Course`` section adopts, edits, or replaces it — from then on
the course chip, the drift trigger, and the Stop readback track those rows,
and this ledger stands down: a row whose anchor survives into the plan is
*adopted* (the course engine owns it now); a row the resident left out is
*deferred* (deleting a row is a legal act the escalation respects). Both are
retirements, both are silent — they are the resident's own acts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── Vocabulary ─────────────────────────────────────────────────────────────

KIND_EVENT = "event"      # answer the waking event itself (assignment #1)
KIND_PENDING = "pending"  # answer/retire each other pending event
KIND_ORIENT = "orient"    # walk the orientation set, or declare the skip
KIND_CARD = "card"        # write .card with a ## Plan (scaffold offered)
KIND_CLAIMS = "claims"    # claim .name / .mood / .topics
KIND_BRANCH = "branch"    # host checkout: branch before you edit
KIND_DUTY = "duty"        # repo-specific standing duty (needs-sync marker)

#: Hard cap on escalation growth — one line per ``cadence`` boundaries past
#: the window, then hold (fork 3, signed: the alternative — unbounded growth —
#: was priced as "an ignored run's boundaries become mostly warning").
ESCALATION_CAP = 3


@dataclass(frozen=True)
class Assignment:
    """One typed boot obligation: what is owed, what discharges it, when it
    starts escalating.

    ``window`` is in *boundaries*, not minutes — a run's tempo is its tool
    cadence. ``None`` means unmetered: the row renders in the kernel but the
    ledger never escalates it, because no boundary-time observation can prove
    it undischarged (a guard may only assert something the run can be proven
    wrong about). The waking-event row is the sharpest case: its normal
    discharge is the final stdout reply, which post-dates every boundary, and
    the Stop capsule already governs that seam — metering it here would
    false-nag every clean run.

    ``anchor`` is the lowercase substring the card handoff matches against
    adopted ``## Plan`` rows; the scaffold rows are these titles, so an
    unedited adoption matches trivially, and the conservative direction of a
    miss is silence (a deferral), never a duplicate nag.
    """

    id: str
    kind: str
    title: str
    discharge: str
    window: int | None = None
    cadence: int = 4
    detail: tuple[str, ...] = ()
    anchor: str = ""


@dataclass(frozen=True)
class Pricing:
    """The wake's escalation budget, derived from the live quota posture.

    Fork 2, amended and signed: windows are not fixed constants — "the only
    constraint should be price against quota allocation … so live cost-aware
    decision making." Scarce quota buys a terse ignition (later, smaller
    escalations); rich quota an attentive one. Priced once per wake, at
    assembly, from the same binding-percent reducer the scheduler's pacing
    already trusts (``runner_quota.binding_quota_remaining_pct``) — the hooks
    read the stamped numbers back and never re-price.
    """

    multiplier: float
    cadence: int
    label: str


#: The pacing machinery's own floors (``daemon._quota_low_floor_pct`` /
#: ``_quota_critical_floor_pct`` defaults) — the precedent fork 2 names.
_LOW_FLOOR_PCT = 20.0
_CRITICAL_FLOOR_PCT = 8.0

#: Base escalation windows per kind, in boundaries, at the rich-quota price.
#: ``None`` = unmetered (see :class:`Assignment`).
_BASE_WINDOWS: dict[str, int | None] = {
    KIND_EVENT: None,
    KIND_PENDING: 8,
    KIND_ORIENT: 6,
    KIND_CARD: 4,
    KIND_CLAIMS: 10,
    KIND_BRANCH: None,
    KIND_DUTY: None,
}


def price(
    binding_pct: float | None,
    *,
    low_floor: float = _LOW_FLOOR_PCT,
    critical_floor: float = _CRITICAL_FLOOR_PCT,
) -> Pricing:
    """Price the wake's escalation budget from the binding quota percent.

    ``None`` (quota unmeasured — an ad-hoc wake, a Shell with no collector)
    prices neutral: the base windows, neither stretched nor tightened.
    """
    if binding_pct is None:
        return Pricing(multiplier=1.0, cadence=4, label="unmeasured")
    if binding_pct < critical_floor:
        return Pricing(multiplier=3.0, cadence=8, label="critical")
    if binding_pct < low_floor:
        return Pricing(multiplier=2.0, cadence=6, label="low")
    if binding_pct < 50.0:
        return Pricing(multiplier=1.5, cadence=5, label="mid")
    return Pricing(multiplier=1.0, cadence=4, label="rich")


def _window(kind: str, pricing: Pricing) -> int | None:
    base = _BASE_WINDOWS.get(kind)
    if base is None:
        return None
    return max(1, round(base * pricing.multiplier))


def derive(
    *,
    is_strand: bool = False,
    environment: str | None = None,
    has_event_body: bool = False,
    pending_count: int = 0,
    orientation_files: int = 0,
    orientation_bytes: int = 0,
    needs_sync: str | None = None,
    pricing: Pricing | None = None,
) -> list[Assignment]:
    """Derive the wake's assignment list — deterministic, facts only.

    Nothing here is invented: every row is an obligation the daemon already
    knows, and every field is provable from the inputs. A strand gets the
    minimal set (its waking event, the walk, the host branch rule) — it has
    no gate authority, no card contract with a watching user, and no
    identity claims; the 2026-07-13 incident (strands answering the
    resident's queue) is why ``pending_count`` never reaches a strand row.
    """
    p = pricing or price(None)
    rows: list[Assignment] = []

    if has_event_body:
        rows.append(Assignment(
            id="a-event",
            kind=KIND_EVENT,
            title="answer the waking event",
            discharge=(
                "the reply — final stdout at close, or an `event:` outbox "
                "file; the substance, not a receipt"
            ),
            window=_window(KIND_EVENT, p),
            cadence=p.cadence,
            anchor="waking event",
        ))

    if pending_count and not is_strand:
        plural = "s" if pending_count != 1 else ""
        rows.append(Assignment(
            id="a-pending",
            kind=KIND_PENDING,
            title=f"answer {pending_count} queued event{plural}",
            discharge="one `event:` reply or deliberate `note:` each",
            window=_window(KIND_PENDING, p),
            cadence=p.cadence,
            detail=detail_lines_for(KIND_PENDING),
            anchor="queued event",
        ))

    if orientation_files:
        rows.append(Assignment(
            id="a-orient",
            kind=KIND_ORIENT,
            title=(
                f"walk the orientation set ({orientation_files} file(s) · "
                f"{orientation_bytes:,}B)"
            ),
            discharge=(
                "Read each listed file, or declare the skip on .card "
                "(\"assuming prior knowledge, skipping orientation\")"
            ),
            window=_window(KIND_ORIENT, p),
            cadence=p.cadence,
            detail=detail_lines_for(KIND_ORIENT),
            anchor="orientation",
        ))

    if not is_strand:
        rows.append(Assignment(
            id="a-card",
            kind=KIND_CARD,
            title="write .card with a ## Plan",
            discharge=(
                "the write — these rows as `- [ ]` checkboxes are the "
                "offered scaffold; adopt, edit, or replace it"
            ),
            window=_window(KIND_CARD, p),
            cadence=p.cadence,
            detail=detail_lines_for(KIND_CARD),
            anchor=".card",
        ))
        rows.append(Assignment(
            id="a-claims",
            kind=KIND_CLAIMS,
            title="claim .name · .mood · .topics",
            discharge="one write each (brnrd emotes <feeling> finds a face)",
            window=_window(KIND_CLAIMS, p),
            cadence=p.cadence,
            detail=detail_lines_for(KIND_CLAIMS),
            anchor=".name",
        ))

    if (environment or "").strip() == "host":
        rows.append(Assignment(
            id="a-branch",
            kind=KIND_BRANCH,
            title="branch before you edit",
            discharge=(
                "`git switch -c <name>` off the default branch — host "
                "checkout; your push, or the work never leaves this machine"
            ),
            window=_window(KIND_BRANCH, p),
            cadence=p.cadence,
            anchor="branch",
        ))

    if needs_sync and not is_strand:
        rows.append(Assignment(
            id="a-needs-sync",
            kind=KIND_DUTY,
            title="the knowledge push was rejected — a needs-sync marker stands",
            discharge=(
                "reconcile the knowledge remote; brnrd clears the marker on "
                "the next clean capture"
            ),
            window=_window(KIND_DUTY, p),
            cadence=p.cadence,
            anchor="needs-sync",
        ))

    return rows


def detail_lines_for(kind: str) -> tuple[str, ...]:
    """The escalation lines a row reveals past its window, sharpest last.

    Authored here — prompt-contract text in code, maintainer-merged like the
    rest of the wake contract. Length caps growth at :data:`ESCALATION_CAP`
    by construction.
    """
    return {
        KIND_PENDING: (
            "prose in this thread clears nothing — only `event:` or a "
            "deliberate `note:` retires a pending event",
            "read ids from portal-state.json → inbound.events[].id; never "
            "reconstruct one from memory",
            "an unanswered correspondent outlives this run — answer or "
            "defer each by name before the closeout",
        ),
        KIND_ORIENT: (
            "unread contract files mean acting on remembered permissions",
            "the skip is one card line: \"assuming prior knowledge, "
            "skipping orientation\" — a first-class outcome, not a failure",
            "every file in the set was chosen deterministically for this "
            "task; none is padding",
        ),
        KIND_CARD: (
            "the card is the surface the user watches while you think — "
            "blank reads as stalled",
            "one `## Now` line plus the scaffold rows is a complete first "
            "write",
            "a run with no card leaves no body for the next wake to recover",
        ),
        KIND_CLAIMS: (
            "one line each: .name (≤60 chars), .mood (an emote handle), "
            ".topics (slugs)",
            "a run rendering as hex on the dashboard is a forgotten name",
            "an unclaimed topic leaves this run unfindable once it ends",
        ),
    }.get(kind, ())


# ── Serialization (boot-score.json ↔ the hook ledger) ─────────────────────


def rows_from_score(score: dict[str, Any]) -> list[dict[str, Any]]:
    """The assignment rows out of a persisted ``boot-score.json`` dict.

    Tolerant by design: an older score without the field, or a malformed
    one, yields ``[]`` and every consumer degrades to "no ledger" — the same
    doctrine as ``hooks._orientation_set_paths``.
    """
    raw = score.get("assignments")
    if not isinstance(raw, list):
        return []
    rows: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        row_id = str(entry.get("id") or "").strip()
        if not row_id:
            continue
        rows.append(entry)
    return rows


# ── The boundary ledger (hook side) ────────────────────────────────────────

#: The ledger's home inside ``.hook-state.json`` — one bucket, so the whole
#: feature can be read (and reset) in one place: ``{"ordinal": int, "rows":
#: {id: {"retired": str|None, "overdue_since": int|None, "level": int}}}``.
STATE_KEY = "assignments"

#: Retirement reasons. ``discharged`` — the observable act happened;
#: ``adopted`` — the row survived into the resident's ## Plan (the course
#: engine owns it now); ``deferred`` — the resident's plan left it out
#: (deleting a row is a legal act the escalation respects).
RETIRED_DISCHARGED = "discharged"
RETIRED_ADOPTED = "adopted"
RETIRED_DEFERRED = "deferred"


@dataclass
class LedgerView:
    """One boundary's read of the ledger, ready to render."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    states: dict[str, dict[str, Any]] = field(default_factory=dict)
    ordinal: int = 0
    edge: bool = False

    @property
    def total(self) -> int:
        return len(self.rows)

    @property
    def retired_count(self) -> int:
        return sum(
            1 for row in self.rows
            if self.states.get(str(row.get("id")), {}).get("retired")
        )

    @property
    def open_rows(self) -> list[dict[str, Any]]:
        return [
            row for row in self.rows
            if not self.states.get(str(row.get("id")), {}).get("retired")
        ]

    def overdue_rows(self) -> list[dict[str, Any]]:
        out = []
        for row in self.open_rows:
            st = self.states.get(str(row.get("id")), {})
            if st.get("overdue_since") is not None:
                out.append(row)
        return out


def _discharged(row: dict[str, Any], facts: dict[str, Any]) -> bool:
    """The kind-keyed observable-act test — the ledger's only positive claim.

    Every predicate here is a fact the daemon or the hooks already measure;
    a kind with no observable discharge simply never returns True (its row
    retires through adoption/deferral or stands, unmetered).
    """
    kind = str(row.get("kind") or "")
    if kind == KIND_EVENT:
        return int(facts.get("replies_current") or 0) > 0
    if kind == KIND_PENDING:
        return int(facts.get("action_pending") or 0) == 0
    if kind == KIND_ORIENT:
        return bool(facts.get("orient_done"))
    if kind == KIND_CARD:
        # "write .card with a ## Plan" — the write that carries a plan
        # section. A card with no checkbox section is a card, not a course;
        # the row stands until the plan (or the skip of it, via deferral in
        # a replacing plan) exists.
        return bool(facts.get("card_active")) and facts.get("course_rows") is not None
    if kind == KIND_CLAIMS:
        return (
            bool(facts.get("name_written"))
            and bool(facts.get("mood_ever"))
            and bool(facts.get("topics_claimed"))
        )
    return False


def advance(
    rows: list[dict[str, Any]],
    state: dict[str, Any],
    *,
    facts: dict[str, Any],
    tick: bool = True,
) -> LedgerView:
    """Advance the ledger one boundary; mutate *state*'s bucket in place.

    ``tick=False`` observes without spending a boundary: retirements still
    apply (they are act observations, and the closeout must see an act from
    the final batch), but the ordinal — and therefore the overdue clock —
    stands still. The Stop phase uses it, because Stop can fire more than
    once and a re-fire is not a boundary the run lived.

    Transition rules, in order per row:

    1. already retired → untouched (nothing is un-retired; a new obligation
       is a new event with its own letter chrome, never a resurrected row);
    2. the observable act happened → ``discharged`` (an edge — the chip
       moves and the boundary may open for it);
    3. a ``## Plan``/``## Course`` exists on the card → the handoff:
       anchor found in a plan row → ``adopted``, else ``deferred`` — both
       silent, both the resident's own act;
    4. still open and metered → the overdue clock: past the window the row
       gains one detail line per ``cadence`` boundaries, up to
       :data:`ESCALATION_CAP`, then holds. The overdue transition and every
       level bump are edges.
    """
    bucket = state.get(STATE_KEY)
    if not isinstance(bucket, dict):
        bucket = {}
    row_states = bucket.get("rows")
    if not isinstance(row_states, dict):
        row_states = {}
    ordinal = int(bucket.get("ordinal") or 0) + (1 if tick else 0)

    course_rows = facts.get("course_rows")
    plan_exists = isinstance(course_rows, list)
    plan_texts = (
        [str(t).lower() for t in course_rows] if plan_exists else []
    )

    edge = False
    for row in rows:
        row_id = str(row.get("id") or "")
        st = row_states.get(row_id)
        if not isinstance(st, dict):
            st = {"retired": None, "overdue_since": None, "level": 0}
            row_states[row_id] = st
        if st.get("retired"):
            continue
        if _discharged(row, facts):
            st["retired"] = RETIRED_DISCHARGED
            edge = True
            continue
        if plan_exists:
            anchor = str(row.get("anchor") or "").lower()
            adopted = bool(anchor) and any(anchor in t for t in plan_texts)
            st["retired"] = RETIRED_ADOPTED if adopted else RETIRED_DEFERRED
            # Silent: the resident's own act. The chip's text moves, and the
            # card write that caused this already opened a boundary through
            # the course engine's own edge.
            continue
        window = row.get("window")
        if not isinstance(window, int) or isinstance(window, bool):
            continue
        cadence = row.get("cadence")
        if not isinstance(cadence, int) or isinstance(cadence, bool) or cadence < 1:
            cadence = 4
        if st.get("overdue_since") is None:
            if ordinal > window:
                st["overdue_since"] = ordinal
                st["level"] = 1
                edge = True
        else:
            cap = min(ESCALATION_CAP, max(1, len(row.get("detail") or []) or 1))
            level = min(
                cap, 1 + (ordinal - int(st["overdue_since"])) // cadence
            )
            if level > int(st.get("level") or 0):
                st["level"] = level
                edge = True

    state[STATE_KEY] = {"ordinal": ordinal, "rows": row_states}
    return LedgerView(rows=rows, states=row_states, ordinal=ordinal, edge=edge)


# ── Renderers ──────────────────────────────────────────────────────────────


def chip(view: LedgerView | None) -> str | None:
    """The bar segment: ``assign k/n`` while rows stand open, gone at rest.

    Same ratio license as ``course k/n`` — both numbers come from one
    authored list, one denominator. Act-keyed exit: the boundary the last
    row retires, the chip leaves and never returns.
    """
    if view is None or not view.rows or not view.open_rows:
        return None
    return f"assign {view.retired_count}/{view.total}"


def _row_title(row: dict[str, Any], facts: dict[str, Any]) -> str:
    title = str(row.get("title") or "")
    if str(row.get("kind") or "") == KIND_ORIENT:
        progress = facts.get("orient_progress")
        if (
            isinstance(progress, tuple)
            and len(progress) == 2
        ):
            title += f" — {progress[0]}/{progress[1]} read"
    return title


def detail_lines(view: LedgerView, facts: dict[str, Any]) -> list[str]:
    """The overdue rows, grown to their unlocked level — the loud half.

    Rendered on the ledger's own edge only (the caller gates on
    ``view.edge``): a level bump changes the text, which renders once, and
    the row then stands silent until the next bump — current obligations
    earn quiet, overdue ones earn growth, and nothing repeats per boundary.
    """
    lines: list[str] = []
    for row in view.overdue_rows():
        st = view.states.get(str(row.get("id")), {})
        level = int(st.get("level") or 1)
        lines.append(
            f"- assign overdue: {_row_title(row, facts)} — "
            f"{row.get('discharge')}"
        )
        detail = row.get("detail")
        if isinstance(detail, list):
            for text in detail[:level]:
                lines.append(f"    ↳ {text}")
    return lines


def stop_lines(view: LedgerView | None) -> list[str]:
    """The closeout readback: ignition rows never discharged or deferred.

    Excludes the waking-event and pending kinds on purpose — the Stop
    capsule's delivery clause and pending-event block already govern those
    seams with more standing than a readback line has; naming them twice
    would be two surfaces disagreeing about one obligation.
    """
    if view is None:
        return []
    lines: list[str] = []
    for row in view.open_rows:
        kind = str(row.get("kind") or "")
        if kind in (KIND_EVENT, KIND_PENDING):
            continue
        window = row.get("window")
        if window is None:
            continue
        lines.append(
            f"- ignition, never discharged or deferred: "
            f"{row.get('title')} — {row.get('discharge')}"
        )
    return lines

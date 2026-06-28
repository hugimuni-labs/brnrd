"""The boundary facet schema — defined once, projected by every renderer.

Facets are the resident's perception of its *operating envelope*: the walls a
run can hit and the actionable state that changes a decision. The maintainer's
question was "agreeing by convention — how do I *choose* the facets?" and the
answer (``kb/design-resident-boundary.md`` §1, §8) is to stop choosing them by
editorial taste — three renderers that happen to list the same keys — and
**derive them from the walls we already agreed**, defining the set *here, once*.
"By schema, not by convention." The three renderers (the daemon JSON snapshot,
the woven hook line, ``brr portal state``) project from :data:`FACETS` so they
can never drift on *which* facets they carry, and an operator can list the
catalogue on demand with ``brr portal facets``.

A slot earns facet status iff it is one of:

- a **level** — a wall the run approaches, with a distance the resident plans
  against: subscription ``quota``, session ``spend``, ``context_window``
  headroom. (Wall-clock ``budget`` is a level too, but it has a proven local
  source and rides its own top-level ``budget`` block, so it is not repeated
  here.)
- a **state** — actionable operational posture that changes a decision without
  being a wall: ``coexisting_runs`` (sibling presence), ``remote_scm`` (PR /
  push posture).

Each facet is a uniform three-state record, because "missing" means two
different things the resident must not conflate:

- ``known`` — a value proven by the configured collector this heartbeat.
- ``absent`` — the collector ran and there is genuinely nothing yet: no PR for
  this branch, no quota snapshot this Shell exposes. The *affirmative-empty*
  signal — the same logic the closeout capsule uses for "0 pending events".
  Absence is data, surfaced on purpose, not a silent gap.
- ``unimplemented`` — no collector is wired for this slot on this Shell.
  ``required`` separates expected-to-grow (cost metering) from someday-niceties
  (coexisting runs while brr stays single-flight per dominion).

The level collectors are **per-Shell** (§8): Codex exposes live quota/context
through session-rollout ``token_count`` events, while Claude exposes terminal
spend/context through result JSON and cached subscription quota through the
interactive ``/usage`` PTY collector. A Shell with no collector for a slot reads
``unimplemented``. That asymmetry is the design, surfaced honestly, not a bug.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

# Three-state status values for a facet record.
KNOWN = "known"
ABSENT = "absent"
UNIMPLEMENTED = "unimplemented"

# Facet kinds.
LEVEL = "level"  # a wall the run approaches (distance-from-envelope)
STATE = "state"  # actionable posture that changes a decision


@dataclass(frozen=True)
class FacetSpec:
    """One facet's identity in the catalogue — the schema, not its live value.

    ``fills`` is the operator-facing one-liner: what a collector would put in
    this slot and why it might be empty. It is what ``brr portal facets`` prints
    so an operator can see *what the implemented facets are* without reading the
    code.
    """

    key: str
    label: str
    kind: str
    required: bool
    fills: str


# The ordered, wall-derived facet set. This tuple is the single definition;
# every renderer and the inspection command read it rather than re-listing keys.
FACETS: tuple[FacetSpec, ...] = (
    FacetSpec(
        "quota", "quota", LEVEL, True,
        "subscription quota headroom (used% + reset window) from a Shell-level "
        "source or local quota snapshot; absent until one is read",
    ),
    FacetSpec(
        "spend", "spend", LEVEL, True,
        "estimated session spend so far ($) handed over by the Shell — never a "
        "forward projection; unimplemented on Shells with no spend gauge",
    ),
    FacetSpec(
        "context_window", "context-window", LEVEL, True,
        "context-window headroom (% remaining) from the Shell's level source; "
        "unimplemented on Shells that do not expose it",
    ),
    FacetSpec(
        "coexisting_runs", "coexisting-runs", STATE, False,
        "live sibling runs sharing this dominion; brr is single-flight per "
        "dominion, so unimplemented until sibling-liveness tracking lands",
    ),
    FacetSpec(
        "remote_scm", "remote-scm", STATE, True,
        "PR posture for the run branch — pushed?, PR open / not yet created — "
        "derived network-free from run metadata; absent = no PR recorded yet",
    ),
)

FACETS_BY_KEY: dict[str, FacetSpec] = {f.key: f for f in FACETS}


def _level_record(
    spec: FacetSpec, summary: str | None, *, has_collector: bool
) -> dict[str, object]:
    """Build a level facet record from an optional proven summary.

    ``has_collector`` is the per-Shell switch: when a collector is wired for
    this slot on this Shell but produced no value this heartbeat, the slot is
    ``absent`` (affirmative-empty); when no collector exists, it is
    ``unimplemented`` so the gap reads as "not built" rather than "empty".
    """
    text = str(summary or "").strip()
    if text:
        return {
            "status": KNOWN, "kind": spec.kind, "required": spec.required,
            "summary": text, "note": None,
        }
    if has_collector:
        return {
            "status": ABSENT, "kind": spec.kind, "required": spec.required,
            "summary": None,
            "note": f"no {spec.label} reading from this Shell yet",
        }
    return {
        "status": UNIMPLEMENTED, "kind": spec.kind, "required": spec.required,
        "summary": None,
        "note": f"no {spec.label} collector for this Shell yet",
    }


def _runner_block(
    runner_name: str | None,
    runner_meta: "dict[str, object] | None",
) -> dict[str, object]:
    """Build the ``resources.runner`` governance block.

    Records which Shell/Core is actually executing this run so the
    resident (and future tooling) can see the selection without parsing
    the ``run.runner`` prose string. ``runner_name`` is the profile key;
    ``runner_meta`` is the raw profile dict (optional keys: model,
    class, provider, hooks, quota_source, cost_rank, owner).

    The block is always ``status: known`` when a runner_name is present
    — brr resolves the runner before the run starts, so it is always
    determined. ``status: absent`` when nothing is resolved yet (pre-run
    or test contexts).
    """
    if not runner_name:
        return {"status": ABSENT, "summary": None, "note": "no runner resolved yet"}
    meta = runner_meta or {}
    return {
        "status": KNOWN,
        "name": runner_name,
        "model": str(meta.get("model") or "").strip() or None,
        "class": str(meta.get("class") or "").strip() or None,
        "provider": str(meta.get("provider") or "").strip() or None,
        "hooks": str(meta.get("hooks") or "").strip() or None,
        "owner": str(meta.get("owner") or "user").strip() or "user",
        "quota_source": str(meta.get("quota_source") or "").strip() or None,
        "cost_rank": meta.get("cost_rank"),
        "summary": runner_name,
    }


def build(
    *,
    quota_summary: str | None = None,
    levels: dict[str, object] | None = None,
    levels_collector: bool | Iterable[str] = False,
    branch: str | None = None,
    pr_number: str | None = None,
    runner_name: str | None = None,
    runner_meta: "dict[str, object] | None" = None,
) -> dict[str, object]:
    """Build the live ``resources`` facet dict from the collected inputs.

    Single construction point for every renderer (replaces the old hand-rolled
    ``daemon._resources_facet``). Inputs:

    - ``quota_summary`` — a quota one-liner from the local quota snapshot
      (``runner_quota``), the always-available quota path.
    - ``levels`` — a parsed level snapshot from the Shell's level collector
      (Claude result JSON, Codex session rollout), carrying ``quota`` /
      ``spend`` / ``context_window`` summaries. Its quota wins over
      ``quota_summary`` when present.
    - ``levels_collector`` — which level slots this Shell has a *wired*
      collector for, so an empty slot reads ``absent`` (collector ran, nothing
      yet) rather than ``unimplemented`` (no collector). ``True`` means all
      level slots; ``False`` means none; an iterable names the specific slots
      (per-Shell asymmetry: Codex collects ``quota`` + ``context_window`` but
      has no dollar-spend gauge, so ``spend`` stays ``unimplemented``).
    - ``branch`` / ``pr_number`` — run metadata for the ``remote_scm`` posture.
    - ``runner_name`` / ``runner_meta`` — which Shell/Core is executing; the
      profile dict carries model, class, provider, etc. Renders as
      ``resources.runner`` for governance visibility (step 3,
      ``design-runner-cores.md``).
    """
    levels = levels or {}
    if isinstance(levels_collector, bool):
        wired_slots = {"spend", "context_window"} if levels_collector else set()
    else:
        wired_slots = {str(s) for s in levels_collector}

    def _level_summary(key: str) -> str | None:
        slot = levels.get(key)
        if isinstance(slot, dict):
            return str(slot.get("summary") or "").strip() or None
        if isinstance(slot, str):
            return slot.strip() or None
        return None

    quota = _level_summary("quota") or quota_summary
    # quota always has a collector (the local runner_quota snapshot), so an
    # empty quota slot is affirmative-``absent``, never ``unimplemented``.
    quota_facet = _level_record(
        FACETS_BY_KEY["quota"], quota, has_collector=True
    )
    spend_facet = _level_record(
        FACETS_BY_KEY["spend"], _level_summary("spend"),
        has_collector="spend" in wired_slots,
    )
    context_facet = _level_record(
        FACETS_BY_KEY["context_window"], _level_summary("context_window"),
        has_collector="context_window" in wired_slots,
    )

    spec_co = FACETS_BY_KEY["coexisting_runs"]
    coexisting = {
        "status": UNIMPLEMENTED, "kind": spec_co.kind,
        "required": spec_co.required, "summary": None,
        "note": "single-flight per dominion; no concurrent-run view yet",
    }

    pr = str(pr_number or "").strip()
    pr_recorded = bool(pr)
    spec_scm = FACETS_BY_KEY["remote_scm"]
    remote_scm = {
        "status": KNOWN if pr_recorded else ABSENT,
        "kind": spec_scm.kind, "required": spec_scm.required,
        "branch": branch,
        "pr_number": pr if pr_recorded else None,
        "pr_state": "open" if pr_recorded else "none",
        "summary": f"PR #{pr}" if pr_recorded else None,
        "note": None if pr_recorded else "no PR recorded for this branch yet",
    }

    return {
        "runner": _runner_block(runner_name, runner_meta),
        "quota": quota_facet,
        "spend": spend_facet,
        "context_window": context_facet,
        "coexisting_runs": coexisting,
        "remote_scm": remote_scm,
    }


def facet_value(facet: dict[str, object] | None) -> str:
    """Render one facet record's value for a one-line view.

    Shared by every renderer so the three rails can never disagree on how a
    ``known`` / ``absent`` / ``unimplemented`` slot reads. ``known`` shows its
    value (a PR handle or a summary); the empty states name themselves and carry
    their reason in parentheses — substantially more legible than a flat
    "unavailable".
    """
    facet = facet if isinstance(facet, dict) else {}
    status = facet.get("status")
    if status == KNOWN:
        pr_state = str(facet.get("pr_state") or "").strip()
        if pr_state == "open" and facet.get("pr_number"):
            return f"PR #{facet.get('pr_number')}"
        summary = str(facet.get("summary") or "").strip()
        return summary or "known"
    note = str(facet.get("note") or "").strip()
    state = status if status in {ABSENT, UNIMPLEMENTED} else "unavailable"
    return f"{state} ({note})" if note else str(state)


def render_line(resources: dict[str, object] | None) -> str | None:
    """The woven ``- resources: …`` one-liner for the hook delta.

    Iterates :data:`FACETS` so the woven line carries exactly the schema's
    facets in order — add a facet to the schema and it appears here for free.
    """
    if not resources:
        return None
    parts = [
        f"{spec.label}={facet_value(resources.get(spec.key))}"
        for spec in FACETS
    ]
    return "- resources: " + "; ".join(parts) + "."


def describe_facets(
    resources: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    """The facet catalogue for operator inspection (``brr portal facets``).

    Returns one row per schema facet: its identity (key / label / kind /
    required), the ``fills`` blurb describing what a collector would put there,
    and — when a live ``resources`` dict is supplied — the current status and
    value. This is the on-demand answer to "what are the implemented facets,
    and which are populated right now?"
    """
    rows: list[dict[str, object]] = []
    resources = resources if isinstance(resources, dict) else {}
    for spec in FACETS:
        live = resources.get(spec.key) if resources else None
        live = live if isinstance(live, dict) else None
        rows.append(
            {
                "key": spec.key,
                "label": spec.label,
                "kind": spec.kind,
                "required": spec.required,
                "fills": spec.fills,
                "status": (live or {}).get("status") if live else None,
                "value": facet_value(live) if live else None,
            }
        )
    return rows
